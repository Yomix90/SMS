"""Dispatcher : affecte les messages en file aux téléphones et traite leurs retours.

Boucle (toutes les 5 s) :
1. promote_scheduled()  : messages planifiés arrivés à échéance -> queued.
2. requeue_timeouts()   : messages transmis sans accusé de réception depuis ACK_TIMEOUT -> queued.
3. dispatch()           : pour chaque téléphone en ligne, calcule un budget (limites heure/jour,
                          messages déjà en vol) et lui pousse un lot via Socket.IO.
Les messages hors fenêtre horaire sont décalés à la prochaine ouverture (sauf ignore_window).
"""
import logging
import uuid as uuidlib
from datetime import timedelta

import sqlalchemy as sa
from flask import current_app

from ..extensions import db
from ..models import Campaign, Device, Message, utcnow
from ..models.campaign import CAMPAIGN_RUNNING, CAMPAIGN_SCHEDULED
from ..models.device import DEVICE_ONLINE
from ..models.message import (DIR_OUT, ST_CANCELED, ST_DELIVERED, ST_DISPATCHED, ST_FAILED, ST_QUEUED,
                              ST_SCHEDULED, ST_SENT)
from . import queue, realtime
from .devices import usage
from .settings import get_setting
from .timeutil import SendWindow

log = logging.getLogger(__name__)

# Nombre max de SMS « en vol » (transmis non envoyés) par téléphone
MAX_IN_FLIGHT = 30


def promote_scheduled() -> int:
    now = utcnow()
    n = (db.session.query(Message)
         .filter(Message.status == ST_SCHEDULED, Message.next_attempt_at <= now)
         .update({Message.status: ST_QUEUED}, synchronize_session=False))
    if n:
        for c in db.session.query(Campaign).filter(Campaign.status == CAMPAIGN_SCHEDULED,
                                                   Campaign.scheduled_at <= now).all():
            c.status = CAMPAIGN_RUNNING
            c.started_at = c.started_at or now
            queue.refresh_campaign(c.id)
    return n


def requeue_timeouts() -> int:
    limit = utcnow() - timedelta(seconds=current_app.config["DEVICE_ACK_TIMEOUT_SEC"])
    stale = (db.session.query(Message)
             .filter(Message.status == ST_DISPATCHED, Message.acked_at.is_(None),
                     Message.dispatched_at < limit)
             .all())
    for msg in stale:
        queue.handle_failure(msg, "ACK_TIMEOUT", "Pas d'accusé de réception du téléphone")
    return len(stale)


def _budget(device: Device) -> int:
    u = usage(device)
    batch_max = current_app.config["DISPATCH_BATCH_MAX"]
    return max(0, min(device.max_per_hour - u["last_hour"],
                      device.max_per_day - u["today"],
                      MAX_IN_FLIGHT - u["in_flight"],
                      batch_max))


def defer_outside_window(window: SendWindow) -> int:
    """Décale les messages en file à la prochaine ouverture si l'on est hors fenêtre."""
    now = utcnow()
    if window.contains(now):
        return 0
    next_open = window.next_open(now)
    return (db.session.query(Message)
            .filter(Message.status == ST_QUEUED, Message.ignore_window.is_(False),
                    Message.next_attempt_at <= now)
            .update({Message.next_attempt_at: next_open}, synchronize_session=False))


def dispatch() -> dict[int, int]:
    """Affecte les messages et pousse les lots. Retourne {device_id: nb_messages}."""
    devices = (db.session.query(Device)
               .filter(Device.status == DEVICE_ONLINE, Device.is_enabled.is_(True), Device.revoked_at.is_(None))
               .all())
    if not devices:
        return {}
    budgets = {d.id: _budget(d) for d in devices}
    by_id = {d.id: d for d in devices}
    total_budget = sum(budgets.values())
    if total_budget == 0:
        return {}

    now = utcnow()
    candidates = (db.session.query(Message)
                  .filter(Message.direction == DIR_OUT, Message.status == ST_QUEUED,
                          Message.next_attempt_at <= now)
                  .order_by(Message.priority.desc(), Message.next_attempt_at, Message.id)
                  .limit(total_budget * 3)  # marge pour les messages épinglés à un appareil saturé
                  .all())
    batches: dict[int, list[Message]] = {}
    for msg in candidates:
        if msg.device_pinned and msg.device_id:
            target = msg.device_id if budgets.get(msg.device_id, 0) > 0 else None
        else:
            # Appareil le moins chargé (budget restant le plus élevé)
            target = max(budgets, key=budgets.get)
            if budgets[target] <= 0:
                target = None
        if target is None:
            if all(b <= 0 for b in budgets.values()):
                break
            continue
        budgets[target] -= 1
        device = by_id[target]
        msg.device_id = target
        if msg.sim_slot is None:
            msg.sim_slot = device.default_sim_slot
        msg.attempts += 1
        msg.acked_at = None
        queue.set_status(msg, ST_DISPATCHED, details={"device": device.name, "attempt": msg.attempts},
                         force=True)
        batches.setdefault(target, []).append(msg)

    for device_id, msgs in batches.items():
        realtime.to_device(device_id, "task.send", {"messages": [m.to_device_task() for m in msgs]})
    return {k: len(v) for k, v in batches.items()}


def tick() -> dict:
    """Un passage complet du dispatcher (appelé par APScheduler)."""
    window = SendWindow.from_setting(get_setting("send_window"))
    promoted = promote_scheduled()
    timeouts = requeue_timeouts()
    deferred = defer_outside_window(window)
    dispatched = dispatch()
    db.session.commit()
    return {"promoted": promoted, "timeouts": timeouts, "deferred": deferred, "dispatched": dispatched}


# --- Retours du téléphone -------------------------------------------------------------

def _find(device: Device, message_id: str) -> Message | None:
    try:
        uid = uuidlib.UUID(str(message_id))
    except (ValueError, TypeError):
        return None
    msg = db.session.query(Message).filter_by(uuid=uid).one_or_none()
    if msg is None or (msg.device_id is not None and msg.device_id != device.id):
        return None  # un téléphone ne peut modifier que ses propres messages
    return msg


def pending_tasks(device: Device, limit: int = 50) -> list[Message]:
    """Messages transmis à l'appareil et non acquittés (mode polling / reconnexion)."""
    return (db.session.query(Message)
            .filter(Message.device_id == device.id, Message.status == ST_DISPATCHED,
                    Message.acked_at.is_(None))
            .order_by(Message.priority.desc(), Message.id)
            .limit(limit).all())


def ack(device: Device, ids: list[str]) -> int:
    now, count = utcnow(), 0
    for mid in ids or []:
        msg = _find(device, mid)
        if msg is not None and msg.status == ST_DISPATCHED and msg.acked_at is None:
            msg.acked_at = now
            count += 1
    return count


DEVICE_STATUS_MAP = {"sent": ST_SENT, "delivered": ST_DELIVERED, "failed": ST_FAILED, "canceled": ST_CANCELED}


def apply_status(device: Device, report: dict) -> bool:
    """report = {"id", "status": sent|delivered|failed|canceled, "error_code", "error_message",
                 "timestamp", "sim_slot", "parts"}"""
    msg = _find(device, report.get("id"))
    if msg is None:
        return False
    status = DEVICE_STATUS_MAP.get(str(report.get("status", "")).lower())
    if status is None:
        return False
    if msg.device_id is None:
        msg.device_id = device.id
    if msg.acked_at is None:
        msg.acked_at = utcnow()
    if report.get("sim_slot") is not None:
        try:
            msg.sim_slot = int(report["sim_slot"])
        except (TypeError, ValueError):
            pass
    at = _parse_ts(report.get("timestamp"))
    details = {"parts": report.get("parts")} if report.get("parts") else None
    if status == ST_FAILED:
        if msg.status in (ST_DELIVERED, ST_FAILED, ST_CANCELED):
            return False
        queue.handle_failure(msg, report.get("error_code"), report.get("error_message"), details)
        return True
    if status == ST_CANCELED:
        if msg.status != ST_DISPATCHED:
            return False
        return queue.set_status(msg, ST_CANCELED, details={"by": "device"}, force=True)
    return queue.set_status(msg, status, at=at, details=details)


def _parse_ts(value):
    """Horodatage du téléphone : epoch millisecondes ou ISO 8601."""
    from datetime import datetime, timezone
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            v = float(value)
            return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None


def stats_snapshot() -> dict:
    """Compteurs globaux pour le tableau de bord."""
    rows = dict(db.session.query(Message.status, sa.func.count(Message.id))
                .filter(Message.direction == DIR_OUT).group_by(Message.status).all())
    return rows
