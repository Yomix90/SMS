"""File d'attente des SMS : mise en file, transitions de statut, relances, quotas.

La table `messages` est la file : le dispatcher y prend les messages `queued`
dont `next_attempt_at` est échu. Toute transition passe par set_status() qui
historise (message_events), déclenche les webhooks et notifie le tableau de bord.
"""
from datetime import timedelta

import sqlalchemy as sa
from flask import current_app
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..extensions import db
from ..models import Blacklist, Campaign, Contact, Conversation, Message, MessageEvent, utcnow
from ..models.campaign import CAMPAIGN_COMPLETED, CAMPAIGN_RUNNING, CAMPAIGN_SCHEDULED
from ..models.message import (DIR_IN, DIR_OUT, PENDING_STATUSES, ST_BLOCKED, ST_CANCELED, ST_DELIVERED,
                              ST_DISPATCHED, ST_FAILED, ST_PAUSED, ST_QUEUED, ST_SCHEDULED, ST_SENT, STATUS_RANK)
from . import realtime, webhooks
from .phone import normalize_phone
from .settings import get_setting
from .sms_encoding import analyze
from .timeutil import ensure_utc, local_day_bounds, local_today

MAX_SEGMENTS = 10

# Codes d'erreur renvoyés par l'app Android (SmsManager) considérés comme temporaires
RETRYABLE_ERRORS = {
    "GENERIC_FAILURE", "RADIO_OFF", "NO_SERVICE", "LIMIT_EXCEEDED", "NETWORK_ERROR", "MODEM_ERROR",
    "TIMEOUT", "ACK_TIMEOUT", "DEVICE_OFFLINE", "SIM_NOT_READY", "QUEUE_FULL", "RATE_LIMITED",
}
PERMANENT_ERRORS = {"NULL_PDU", "INVALID_NUMBER", "SHORT_CODE_NOT_ALLOWED", "PERMISSION_DENIED",
                    "DELIVERY_FAILED", "NO_SIM", "BLACKLISTED"}


class QueueError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 400, details=None):
        super().__init__(f"[{code}] {message or code}")
        self.code, self.message, self.status, self.details = code, message or code, status, details


class InvalidPhoneError(QueueError):
    def __init__(self, phone, reason):
        super().__init__("invalid_phone", f"Numéro invalide : {phone}", 422, {"phone": str(phone), "reason": reason})


# --- Conversations & liste noire -----------------------------------------------

def get_or_create_conversation(phone: str, contact_id: int | None = None, cache: dict | None = None) -> Conversation:
    if cache is not None and phone in cache:
        return cache[phone]
    conv = db.session.query(Conversation).filter_by(phone=phone).one_or_none()
    if conv is None:
        try:
            with db.session.begin_nested():
                conv = Conversation(phone=phone, contact_id=contact_id)
                db.session.add(conv)
        except IntegrityError:  # créée en parallèle par un autre greenlet
            conv = db.session.query(Conversation).filter_by(phone=phone).one()
    if contact_id and conv.contact_id is None:
        conv.contact_id = contact_id
    if cache is not None:
        cache[phone] = conv
    return conv


def touch_conversation(conv: Conversation, msg: Message) -> None:
    conv.last_message_at = msg.received_at or msg.created_at or utcnow()
    conv.last_message_preview = (msg.body or "")[:160]
    conv.last_direction = msg.direction
    if msg.direction == DIR_IN:
        conv.unread_count = (conv.unread_count or 0) + 1
        conv.is_archived = False


def is_blacklisted(phone: str) -> bool:
    return db.session.query(Blacklist.id).filter_by(phone=phone).first() is not None


def blacklisted_phones() -> set[str]:
    return {p for (p,) in db.session.query(Blacklist.phone).all()}


def add_to_blacklist(phone: str, reason: str = "manuel", *, user_id=None, source_message_id=None) -> Blacklist:
    entry = db.session.query(Blacklist).filter_by(phone=phone).one_or_none()
    if entry is None:
        entry = Blacklist(phone=phone, reason=reason, created_by=user_id, source_message_id=source_message_id)
        db.session.add(entry)
    # Les messages encore en attente vers ce numéro ne partiront pas
    # (sauf les réponses automatiques, dont la confirmation de désinscription)
    (db.session.query(Message)
     .filter(Message.phone == phone, Message.direction == DIR_OUT, Message.source != "auto_reply",
             Message.status.in_((ST_SCHEDULED, ST_QUEUED, ST_PAUSED)))
     .update({Message.status: ST_BLOCKED, Message.error_code: "BLACKLISTED"}, synchronize_session=False))
    return entry


# --- Pied de message STOP --------------------------------------------------------

def stop_footer(lang: str = "fr", sender_number: str | None = None) -> str:
    footers = get_setting("stop_footer") or {}
    text = footers.get(lang) or footers.get("fr") or ""
    if "{numero}" in text:
        fallback = "ce numéro" if lang == "fr" else "هذا الرقم"
        text = text.replace("{numero}", sender_number or fallback)
    return text


def detect_lang(text: str) -> str:
    return "ar" if any("؀" <= c <= "ۿ" for c in text or "") else "fr"


# --- Quotas et anti-abus -----------------------------------------------------------

def check_api_quota(api_key, count: int) -> None:
    if api_key is None or not api_key.daily_quota:
        return
    start, end = local_day_bounds(local_today())
    used = (db.session.query(sa.func.count(Message.id))
            .filter(Message.api_key_id == api_key.id, Message.created_at >= start, Message.created_at < end)
            .scalar())
    if used + count > api_key.daily_quota:
        raise QueueError("quota_exceeded", f"Quota journalier atteint ({used}/{api_key.daily_quota})", 429,
                         {"used": used, "quota": api_key.daily_quota, "requested": count})


def check_abuse(count: int, *, api_key=None, user=None) -> None:
    """Bloque les rafales anormales (clé API ou opérateur). Les admins ne sont pas limités."""
    if user is not None and getattr(user, "is_admin", False):
        return
    if api_key is None and user is None:
        return
    cfg = get_setting("abuse_threshold") or {}
    limit, minutes = int(cfg.get("count", 500)), int(cfg.get("minutes", 10))
    since = utcnow() - timedelta(minutes=minutes)
    q = db.session.query(sa.func.count(Message.id)).filter(Message.created_at >= since)
    q = q.filter(Message.api_key_id == api_key.id) if api_key is not None else q.filter(Message.user_id == user.id)
    recent = q.scalar()
    if recent + count > limit:
        from .audit import audit, notify
        who = f"clé API {api_key.name}" if api_key is not None else f"utilisateur {user.email}"
        notify("abuse.detected", "Envoi massif suspect bloqué",
               f"{who} : {recent + count} SMS en {minutes} min (seuil {limit}).")
        audit("abuse.blocked", api_key or user, {"recent": recent, "requested": count, "limit": limit})
        db.session.commit()  # la notification doit persister malgré l'erreur
        raise QueueError("abuse_suspected", "Volume d'envoi anormal : envoi bloqué, contactez un administrateur",
                         429, {"recent": recent, "limit": limit, "minutes": minutes})


# --- Mise en file ------------------------------------------------------------------

def enqueue(phone, body: str, *, contact: Contact | None = None, campaign: Campaign | None = None,
            device=None, sim_slot: int | None = None, scheduled_at=None, priority: int = 0,
            source: str = "web", user_id: int | None = None, api_key=None, external_ref: str | None = None,
            idempotency_key: str | None = None, ignore_window: bool = False, max_attempts: int | None = None,
            paused: bool = False, cache: dict | None = None, notify_dashboard: bool = True) -> Message:
    """Crée un SMS sortant. Lève InvalidPhoneError / QueueError si la demande est invalide.

    Un message vers un numéro en liste noire (ou contact désinscrit) est créé avec le
    statut `blocked` : il apparaît dans l'historique mais ne part jamais.
    """
    region = current_app.config.get("DEFAULT_REGION", "MA")
    result = normalize_phone(phone, region)
    if not result.valid:
        raise InvalidPhoneError(phone, result.error)
    e164 = result.e164

    body = (body or "").strip()
    if not body:
        raise QueueError("empty_body", "Le message est vide", 422)
    info = analyze(body)
    if info.segments > MAX_SEGMENTS:
        raise QueueError("message_too_long", f"Message trop long ({info.segments} segments, max {MAX_SEGMENTS})",
                         422, {"segments": info.segments})

    if idempotency_key and api_key is not None:
        existing = (db.session.query(Message)
                    .filter_by(api_key_id=api_key.id, idempotency_key=idempotency_key).one_or_none())
        if existing is not None:
            existing._idempotent_replay = True
            return existing

    if contact is None:
        contact = db.session.query(Contact).filter_by(phone=e164).one_or_none()
    conv = get_or_create_conversation(e164, contact.id if contact else None, cache)

    now = utcnow()
    scheduled_at = ensure_utc(scheduled_at)
    if scheduled_at and scheduled_at <= now + timedelta(seconds=5):
        scheduled_at = None
    status = ST_PAUSED if paused else (ST_SCHEDULED if scheduled_at else ST_QUEUED)
    error_code = None
    if (contact is not None and contact.is_opted_out) or is_blacklisted(e164):
        status, error_code = ST_BLOCKED, "BLACKLISTED"

    msg = Message(
        direction=DIR_OUT, phone=e164, body=body, encoding=info.encoding, segments=info.segments,
        status=status, priority=priority, campaign_id=campaign.id if campaign else None,
        contact_id=contact.id if contact else None, conversation_id=conv.id,
        device_id=device.id if device is not None else None, device_pinned=device is not None,
        sim_slot=sim_slot, scheduled_at=scheduled_at, next_attempt_at=scheduled_at or now,
        max_attempts=max_attempts or int(get_setting("max_attempts") or 3),
        ignore_window=ignore_window, source=source, user_id=user_id,
        api_key_id=api_key.id if api_key is not None else None, external_ref=external_ref,
        idempotency_key=idempotency_key if api_key is not None else None, error_code=error_code,
        created_at=now,
    )
    db.session.add(msg)
    db.session.flush()
    db.session.add(MessageEvent(message_id=msg.id, status=status, error_code=error_code,
                                details={"source": source}))
    if status != ST_BLOCKED:
        touch_conversation(conv, msg)
    if campaign is not None:
        _mark_campaign_dirty(campaign.id)
    if notify_dashboard:
        realtime.to_dashboard("message.new", brief(msg))
    return msg


def brief(msg: Message) -> dict:
    return {"id": str(msg.uuid), "direction": msg.direction, "phone": msg.phone,
            "body": msg.body[:160], "status": msg.status, "conversation_id": msg.conversation_id,
            "campaign_id": msg.campaign_id, "error_code": msg.error_code,
            "created_at": msg.created_at.isoformat() if msg.created_at else None}


# --- Transitions de statut ------------------------------------------------------------

_WEBHOOK_FOR = {ST_SENT: "sms.sent", ST_DELIVERED: "sms.delivered", ST_FAILED: "sms.failed"}


def set_status(msg: Message, status: str, *, error_code: str | None = None, error_message: str | None = None,
               details: dict | None = None, at=None, force: bool = False) -> bool:
    """Applique une transition. Ignore les régressions (ex. « sent » reçu après « delivered »).
    Retourne True si le statut a changé."""
    current = msg.status
    if not force:
        if current == status:
            return False
        if STATUS_RANK.get(status, 0) < STATUS_RANK.get(current, 0):
            return False
        if current in (ST_DELIVERED, ST_FAILED, ST_CANCELED, ST_BLOCKED) and status != current:
            return False  # statut final
    at = ensure_utc(at) or utcnow()
    msg.status = status
    if status == ST_DISPATCHED:
        msg.dispatched_at = at
    elif status == ST_SENT:
        msg.sent_at = at
    elif status == ST_DELIVERED:
        msg.delivered_at = at
        msg.sent_at = msg.sent_at or at
    elif status == ST_FAILED:
        msg.failed_at = at
    if error_code is not None or status in (ST_SENT, ST_DELIVERED):
        msg.error_code = error_code
        msg.error_message = (error_message or "")[:255] or None

    db.session.add(MessageEvent(message_id=msg.id, status=status, error_code=error_code, details=details))
    if status in _WEBHOOK_FOR:
        webhooks.emit_event(_WEBHOOK_FOR[status], webhooks.message_payload(msg))
    if msg.campaign_id:
        _mark_campaign_dirty(msg.campaign_id)
    realtime.to_dashboard("message.update", brief(msg))
    return True


def handle_failure(msg: Message, error_code: str | None, error_message: str | None = None,
                   details: dict | None = None) -> str:
    """Relance automatique si l'erreur est temporaire, sinon échec définitif. Retourne le statut final."""
    code = (error_code or "GENERIC_FAILURE").upper()
    if code in RETRYABLE_ERRORS and msg.attempts < msg.max_attempts:
        backoff = get_setting("retry_backoff") or [60, 300, 900]
        delay = int(backoff[min(max(msg.attempts - 1, 0), len(backoff) - 1)])
        set_status(msg, ST_QUEUED, error_code=code, error_message=error_message, force=True,
                   details={**(details or {}), "retry_in_sec": delay, "attempt": msg.attempts})
        msg.next_attempt_at = utcnow() + timedelta(seconds=delay)
        msg.acked_at = None
        if not msg.device_pinned:
            msg.device_id = None  # le dispatcher pourra choisir un autre téléphone
        return ST_QUEUED
    set_status(msg, ST_FAILED, error_code=code, error_message=error_message, details=details, force=True)
    if msg.campaign_id is None:
        from .audit import notify
        notify("message.failed", f"Échec d'envoi vers {msg.phone}",
               f"{code} {error_message or ''}".strip(), f"/messages/{msg.uuid}")
    return ST_FAILED


def retry_message(msg: Message) -> bool:
    """Relance manuelle d'un message échoué ou annulé (compteur de tentatives remis à zéro)."""
    if msg.direction != DIR_OUT or msg.status not in (ST_FAILED, ST_CANCELED):
        return False
    if is_blacklisted(msg.phone):
        return False
    set_status(msg, ST_QUEUED, force=True, details={"manual_retry": True})
    msg.attempts = 0
    msg.next_attempt_at = utcnow()
    msg.acked_at = None
    msg.error_code = msg.error_message = None
    if not msg.device_pinned:
        msg.device_id = None
    return True


def cancel_message(msg: Message) -> bool:
    if msg.status not in PENDING_STATUSES:
        return False
    if msg.status == ST_DISPATCHED and msg.device_id:
        realtime.to_device(msg.device_id, "task.cancel", {"ids": [str(msg.uuid)]})
    return set_status(msg, ST_CANCELED, force=True)


# --- Compteurs de campagne (recalculés juste avant chaque commit) -------------------

def _mark_campaign_dirty(campaign_id: int) -> None:
    db.session.info.setdefault("dirty_campaigns", set()).add(campaign_id)


@event.listens_for(Session, "before_commit")
def _refresh_dirty_campaigns(session):
    ids = session.info.pop("dirty_campaigns", None)
    if not ids:
        return
    for cid in ids:
        refresh_campaign(cid, session)


def refresh_campaign(campaign_id: int, session=None) -> Campaign | None:
    session = session or db.session
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        return None
    session.flush()
    counts = dict(session.query(Message.status, sa.func.count(Message.id))
                  .filter(Message.campaign_id == campaign_id).group_by(Message.status).all())
    campaign.total = sum(counts.values())
    campaign.queued = sum(counts.get(s, 0) for s in PENDING_STATUSES)
    campaign.sent = counts.get(ST_SENT, 0)
    campaign.delivered = counts.get(ST_DELIVERED, 0)
    campaign.failed = counts.get(ST_FAILED, 0)
    campaign.blocked = counts.get(ST_BLOCKED, 0) + counts.get(ST_CANCELED, 0)

    now = utcnow()
    if campaign.status == CAMPAIGN_SCHEDULED and (campaign.scheduled_at is None or campaign.scheduled_at <= now):
        campaign.status = CAMPAIGN_RUNNING
        campaign.started_at = campaign.started_at or now
    if campaign.status == CAMPAIGN_RUNNING and campaign.queued == 0 and campaign.total > 0:
        campaign.status = CAMPAIGN_COMPLETED
        campaign.completed_at = now
        from .audit import notify
        notify("campaign.completed", f"Campagne « {campaign.name} » terminée",
               f"{campaign.sent + campaign.delivered} envoyés, {campaign.delivered} livrés, "
               f"{campaign.failed} échecs, {campaign.blocked} bloqués", f"/campaigns/{campaign.uuid}")
    realtime.to_dashboard("campaign.update", {
        "id": str(campaign.uuid), "status": campaign.status, "total": campaign.total,
        "queued": campaign.queued, "sent": campaign.sent, "delivered": campaign.delivered,
        "failed": campaign.failed, "blocked": campaign.blocked, "progress": campaign.progress})
    return campaign
