"""Webhooks sortants : création des livraisons, signature HMAC, envoi et nouvelles tentatives.

En-têtes envoyés :
    X-Webhook-Event: sms.delivered
    X-Webhook-Id: <uuid de l'événement, identique entre les tentatives>
    X-Webhook-Timestamp: <epoch secondes>
    X-Webhook-Signature: sha256=<hex HMAC-SHA256(secret, "<timestamp>.<corps brut>")>

Vérification côté client : recalculer la signature sur le corps brut et refuser
si l'horodatage a plus de 5 minutes (anti-rejeu).
"""
import json
import logging
import secrets
import time
from datetime import timedelta
from ipaddress import ip_address
from socket import gaierror, getaddrinfo
from urllib.parse import urlparse

import requests
from flask import current_app

from ..extensions import db
from ..models import Webhook, WebhookDelivery, utcnow
from ..models.webhook import DELIVERY_FAILED, DELIVERY_PENDING, DELIVERY_SUCCESS, WEBHOOK_EVENTS
from . import realtime
from .security import encrypt, hmac_sha256

log = logging.getLogger(__name__)

BACKOFF = [30, 120, 600, 3600, 21600]  # secondes entre les tentatives
MAX_ATTEMPTS = len(BACKOFF) + 1
DISABLE_AFTER_FAILURES = 50


class WebhookUrlError(ValueError):
    pass


def validate_url(url: str) -> str:
    """HTTPS obligatoire (sauf dev) et refus des adresses internes (anti-SSRF)."""
    parsed = urlparse(url.strip())
    allow_http = current_app.config.get("WEBHOOK_ALLOW_HTTP")
    if parsed.scheme not in ("https", "http") or (parsed.scheme == "http" and not allow_http):
        raise WebhookUrlError("https_required")
    if not parsed.hostname:
        raise WebhookUrlError("invalid_url")
    if not allow_http:
        try:
            for info in getaddrinfo(parsed.hostname, None):
                ip = ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    raise WebhookUrlError("private_address")
        except gaierror as exc:
            raise WebhookUrlError("unresolvable_host") from exc
    return url.strip()


def create_webhook(url: str, events: list[str], *, user_id=None, api_key_id=None) -> tuple[Webhook, str]:
    """Crée un webhook et retourne (webhook, secret en clair affiché une seule fois)."""
    unknown = set(events) - set(WEBHOOK_EVENTS)
    if unknown or not events:
        raise WebhookUrlError("invalid_events")
    secret = "whsec_" + secrets.token_urlsafe(32)
    hook = Webhook(url=validate_url(url), events=sorted(set(events)), secret_encrypted=encrypt(secret),
                   created_by=user_id, api_key_id=api_key_id)
    db.session.add(hook)
    return hook, secret


def sign(secret: str, timestamp: int, body: str) -> str:
    return "sha256=" + hmac_sha256(secret, f"{timestamp}.{body}")


def _create_delivery(hook: Webhook, event: str, data: dict) -> WebhookDelivery:
    now = utcnow()
    delivery = WebhookDelivery(webhook_id=hook.id, event=event, payload={}, next_attempt_at=now)
    db.session.add(delivery)
    db.session.flush()
    delivery.payload = {"id": str(delivery.event_id), "event": event, "created_at": now.isoformat(), "data": data}
    return delivery


def emit_event(event: str, data: dict) -> int:
    """Crée une livraison par webhook abonné ; l'envoi a lieu après le commit."""
    hooks = db.session.query(Webhook).filter(Webhook.is_active.is_(True)).all()
    count = 0
    for hook in hooks:
        if event in (hook.events or []):
            _create_delivery(hook, event, data)
            count += 1
    if count:
        realtime.after_commit(_kick_delivery)
    return count


def emit_event_to(hook: Webhook, event: str, data: dict) -> WebhookDelivery:
    """Livraison ciblée (bouton « Tester » du tableau de bord)."""
    delivery = _create_delivery(hook, event, data)
    realtime.after_commit(_kick_delivery)
    return delivery


def _kick_delivery():
    """Lance la livraison en tâche de fond sans attendre le prochain passage du job."""
    if current_app.config.get("TESTING"):
        return
    from ..extensions import socketio
    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            deliver_pending()

    socketio.start_background_task(_run)


def deliver_one(delivery: WebhookDelivery) -> bool:
    hook = delivery.webhook
    body = json.dumps(delivery.payload, ensure_ascii=False, separators=(",", ":"), default=str)
    ts = int(time.time())
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "SMS-Gateway-Webhook/1.0",
        "X-Webhook-Event": delivery.event,
        "X-Webhook-Id": str(delivery.event_id),
        "X-Webhook-Timestamp": str(ts),
        "X-Webhook-Signature": sign(hook.secret, ts, body),
    }
    delivery.attempts += 1
    start = time.monotonic()
    try:
        resp = requests.post(hook.url, data=body.encode("utf-8"), headers=headers,
                             timeout=current_app.config["WEBHOOK_TIMEOUT_SEC"], allow_redirects=False)
        delivery.response_code = resp.status_code
        delivery.response_body = (resp.text or "")[:1024]
        ok = 200 <= resp.status_code < 300
    except requests.RequestException as exc:
        delivery.response_code = None
        delivery.response_body = str(exc)[:1024]
        ok = False
    delivery.duration_ms = int((time.monotonic() - start) * 1000)

    if ok:
        delivery.status = DELIVERY_SUCCESS
        delivery.next_attempt_at = None
        hook.failure_count = 0
    else:
        hook.failure_count += 1
        if delivery.attempts >= MAX_ATTEMPTS:
            delivery.status = DELIVERY_FAILED
            delivery.next_attempt_at = None
        else:
            delivery.next_attempt_at = utcnow() + timedelta(seconds=BACKOFF[delivery.attempts - 1])
        if hook.failure_count >= DISABLE_AFTER_FAILURES and hook.is_active:
            hook.is_active = False
            from .audit import notify
            notify("webhook.disabled", f"Webhook désactivé après {hook.failure_count} échecs", hook.url)
    return ok


def deliver_pending(limit: int = 50) -> int:
    """Envoie les livraisons dues. Appelé par le job planifié et après chaque événement."""
    due = (db.session.query(WebhookDelivery)
           .join(Webhook)
           .filter(WebhookDelivery.status == DELIVERY_PENDING,
                   WebhookDelivery.next_attempt_at <= utcnow(),
                   Webhook.is_active.is_(True))
           .order_by(WebhookDelivery.id)
           .limit(limit)
           .all())
    for delivery in due:
        try:
            deliver_one(delivery)
        except Exception:
            log.exception("webhook delivery %s crashed", delivery.id)
        db.session.commit()
    return len(due)


def message_payload(msg) -> dict:
    """Représentation d'un message dans les événements webhook."""
    return {
        "id": str(msg.uuid),
        "direction": msg.direction,
        "phone": msg.phone,
        "body": msg.body,
        "status": msg.status,
        "segments": msg.segments,
        "error_code": msg.error_code,
        "error_message": msg.error_message,
        "external_ref": msg.external_ref,
        "campaign_id": str(msg.campaign.uuid) if msg.campaign else None,
        "device_id": str(msg.device.uuid) if msg.device else None,
        "sim_slot": msg.sim_slot,
        "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
        "delivered_at": msg.delivered_at.isoformat() if msg.delivered_at else None,
        "received_at": msg.received_at.isoformat() if msg.received_at else None,
    }
