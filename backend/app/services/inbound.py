"""SMS entrants : enregistrement, désinscription STOP, réponses automatiques par mots-clés."""
import re
import unicodedata
from datetime import timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Contact, Group, KeywordRule, Label, Message, MessageEvent, utcnow
from ..models.automation import (ACTION_ADD_GROUP, ACTION_LABEL, ACTION_OPT_IN, ACTION_OPT_OUT,
                                 MATCH_CONTAINS, MATCH_EXACT, MATCH_REGEX, MATCH_STARTS)
from ..models.contact import CONSENT_OPT_IN, CONSENT_OPTED_OUT
from ..models.message import DIR_IN, DIR_OUT, ST_RECEIVED
from . import queue, realtime, webhooks
from .phone import normalize_phone
from .settings import get_setting
from .templating import contact_variables, render


def normalize_text(text: str) -> str:
    """Majuscules, sans accents ni ponctuation de bord, espaces compactés (arabe conservé)."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"\s+", " ", text).strip().strip(".!?,;:-_\"'«»")
    return text.upper()


def is_stop(text: str) -> bool:
    words = {normalize_text(k) for k in (get_setting("stop_keywords") or [])}
    norm = normalize_text(text)
    return norm in words or (norm.split(" ")[0] in words if norm else False)


def receive(device, payload: dict) -> tuple[Message, bool]:
    """payload = {"id"/"uuid": identifiant local unique, "phone", "body", "received_at", "sim_slot"}.
    Retourne (message, créé). Idempotent grâce à (device_id, device_uuid)."""
    from .dispatcher import _parse_ts

    device_uuid = str(payload.get("uuid") or payload.get("id") or "")[:64] or None
    if device_uuid:
        existing = db.session.query(Message).filter_by(device_id=device.id, device_uuid=device_uuid).one_or_none()
        if existing is not None:
            return existing, False

    raw_phone = str(payload.get("phone") or payload.get("from") or "").strip()
    result = normalize_phone(raw_phone, current_app.config.get("DEFAULT_REGION", "MA"))
    # Expéditeurs alphanumériques (ex. « Orange ») : on garde la valeur brute
    phone = result.e164 or raw_phone[:20] or "inconnu"
    body = str(payload.get("body") or "")
    contact = db.session.query(Contact).filter_by(phone=phone).one_or_none()
    conv = queue.get_or_create_conversation(phone, contact.id if contact else None)
    received_at = _parse_ts(payload.get("received_at")) or utcnow()

    msg = Message(direction=DIR_IN, phone=phone, body=body, status=ST_RECEIVED, device_id=device.id,
                  sim_slot=payload.get("sim_slot"), conversation_id=conv.id,
                  contact_id=contact.id if contact else None, received_at=received_at,
                  device_uuid=device_uuid, source="device", encoding="ucs2" if queue.detect_lang(body) == "ar"
                  else "gsm7")
    try:
        with db.session.begin_nested():
            db.session.add(msg)
    except IntegrityError:  # doublon concurrent
        return db.session.query(Message).filter_by(device_id=device.id, device_uuid=device_uuid).one(), False
    db.session.add(MessageEvent(message_id=msg.id, status=ST_RECEIVED))
    queue.touch_conversation(conv, msg)

    if result.valid:
        _process_keywords(msg, contact, device)
    webhooks.emit_event("sms.received", webhooks.message_payload(msg))
    realtime.to_dashboard("inbox.new", {**queue.brief(msg), "contact": contact.full_name if contact else None,
                                        "unread": conv.unread_count})
    return msg, True


def _process_keywords(msg: Message, contact: Contact | None, device) -> None:
    if is_stop(msg.body):
        opt_out(msg.phone, contact, msg, device)
        return
    rules = (db.session.query(KeywordRule).filter(KeywordRule.is_active.is_(True))
             .order_by(KeywordRule.priority.desc(), KeywordRule.id).all())
    norm = normalize_text(msg.body)
    for rule in rules:
        if _matches(rule, msg.body, norm):
            rule.hit_count += 1
            _apply_rule(rule, msg, contact, device)
            break


def _matches(rule: KeywordRule, raw: str, norm: str) -> bool:
    for kw in rule.keywords or []:
        if rule.match_type == MATCH_REGEX:
            try:
                if re.search(kw, raw or "", re.IGNORECASE):
                    return True
            except re.error:
                continue
            continue
        k = normalize_text(kw)
        if not k:
            continue
        if rule.match_type == MATCH_EXACT and norm == k:
            return True
        if rule.match_type == MATCH_STARTS and (norm == k or norm.startswith(k + " ")):
            return True
        if rule.match_type == MATCH_CONTAINS and k in norm:
            return True
    return False


def _recently_replied(phone: str, minutes: int) -> bool:
    if minutes <= 0:
        return False
    since = utcnow() - timedelta(minutes=minutes)
    return db.session.query(Message.id).filter(Message.phone == phone, Message.direction == DIR_OUT,
                                               Message.source == "auto_reply",
                                               Message.created_at >= since).first() is not None


def _reply(msg: Message, text: str, device) -> Message | None:
    """Réponse sur le même appareil et la même SIM, prioritaire, autorisée hors fenêtre horaire."""
    try:
        return queue.enqueue(msg.phone, text, device=device, sim_slot=msg.sim_slot, priority=10,
                             source="auto_reply", ignore_window=True)
    except queue.QueueError:
        return None


def _apply_rule(rule: KeywordRule, msg: Message, contact: Contact | None, device) -> None:
    if rule.action == ACTION_OPT_OUT:
        opt_out(msg.phone, contact, msg, device, reply_text=rule.reply_body)
        return
    if rule.action == ACTION_OPT_IN:
        opt_in(msg.phone, contact)
    elif rule.action == ACTION_LABEL and rule.action_value:
        label = db.session.query(Label).filter_by(name=rule.action_value).one_or_none()
        if label is None:
            label = Label(name=rule.action_value)
            db.session.add(label)
        if label not in msg.conversation.labels:
            msg.conversation.labels.append(label)
    elif rule.action == ACTION_ADD_GROUP and rule.action_value and contact is not None:
        group = db.session.query(Group).filter_by(name=rule.action_value).one_or_none()
        if group is not None and group not in contact.groups:
            contact.groups.append(group)
    if rule.reply_body:  # toute règle peut aussi répondre (reply, opt_in, label, add_group)
        if not _recently_replied(msg.phone, rule.cooldown_minutes):
            text = render(rule.reply_body, contact_variables(contact)).text
            if text:
                _reply(msg, text, device)


def opt_out(phone: str, contact: Contact | None, msg: Message | None = None, device=None,
            reply_text: str | None = None) -> None:
    """Désinscription : liste noire + contact désinscrit + confirmation (envoyée avant le blocage)."""
    already = queue.is_blacklisted(phone)
    if not already and device is not None and get_setting("stop_reply_enabled"):
        lang = contact.locale if contact else queue.detect_lang(msg.body if msg else "")
        text = reply_text or (get_setting("stop_reply") or {}).get(lang)
        if text and msg is not None:
            _reply(msg, text, device)
    queue.add_to_blacklist(phone, "stop_keyword", source_message_id=msg.id if msg else None)
    if contact is not None:
        contact.consent_status = CONSENT_OPTED_OUT
        contact.opted_out_at = utcnow()
    from .audit import audit
    audit("contact.opt_out", contact, {"phone": phone, "via": "sms" if msg else "manual"})


def opt_in(phone: str, contact: Contact | None) -> None:
    from ..models import Blacklist
    db.session.query(Blacklist).filter_by(phone=phone, reason="stop_keyword").delete()
    if contact is not None:
        contact.consent_status = CONSENT_OPT_IN
        contact.consent_source = "sms"
        contact.consent_at = utcnow()
        contact.opted_out_at = None
