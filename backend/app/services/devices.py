"""Appareils : appairage QR, authentification par token, heartbeat, état en ligne."""
import io
import json
import secrets
from datetime import timedelta

import qrcode
import qrcode.image.svg
import sqlalchemy as sa
from flask import current_app

from ..extensions import db
from ..models import Device, DeviceSim, Message, PairingCode, utcnow
from ..models.device import DEVICE_DISABLED, DEVICE_OFFLINE, DEVICE_ONLINE
from ..models.message import ST_DISPATCHED, ST_QUEUED
from . import realtime, webhooks
from .audit import audit, notify
from .security import generate_token, hash_token
from .timeutil import local_day_bounds, local_today

TOKEN_PREFIX = "dev_"


class DeviceAuthError(Exception):
    pass


# --- Appairage --------------------------------------------------------------------

def create_pairing_code(user_id: int | None = None, device_name: str | None = None,
                        device: Device | None = None) -> tuple[PairingCode, str]:
    """Retourne (code en base, code en clair à mettre dans le QR).
    Avec `device`, le QR sert à réappairer cet appareil (nouveau token, même historique)."""
    code = secrets.token_urlsafe(24)
    ttl = current_app.config["PAIRING_CODE_TTL_MIN"]
    pc = PairingCode(code_hash=hash_token(code), device_name=device_name,
                     expires_at=utcnow() + timedelta(minutes=ttl), created_by=user_id,
                     device_id=device.id if device is not None else None)
    db.session.add(pc)
    return pc, code


def pairing_payload(code: str) -> str:
    return json.dumps({"v": 1, "url": current_app.config["PUBLIC_BASE_URL"], "code": code},
                      separators=(",", ":"))


def pairing_qr_svg(code: str) -> str:
    img = qrcode.make(pairing_payload(code), image_factory=qrcode.image.svg.SvgPathImage, box_size=10,
                      border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def pair_device(code: str, info: dict, ip: str | None = None) -> tuple[Device, str]:
    """Consomme un code d'appairage et crée l'appareil. Retourne (device, token en clair)."""
    pc = db.session.query(PairingCode).filter_by(code_hash=hash_token(code or "")).one_or_none()
    if pc is None or not pc.is_usable:
        raise DeviceAuthError("invalid_or_expired_code")
    token = generate_token(TOKEN_PREFIX, 30)
    model = (info.get("model") or "")[:100]
    device = db.session.get(Device, pc.device_id) if pc.device_id else None
    if device is not None:  # réappairage : l'ancien token est invalidé
        realtime.to_device(device.id, "device.revoked", {"reason": "re_paired"})
        device.token_hash, device.token_prefix = hash_token(token), token[:12]
        device.revoked_at = None
        device.status = DEVICE_OFFLINE
        device.model = model or device.model
    else:
        device = Device(
            name=(pc.device_name or info.get("name") or model or "Téléphone")[:100],
            token_hash=hash_token(token), token_prefix=token[:12],
            model=model or None, created_by=pc.created_by, status=DEVICE_OFFLINE,
        )
        db.session.add(device)
    device.manufacturer = (info.get("manufacturer") or "")[:100] or device.manufacturer
    device.android_version = str(info.get("android_version") or "")[:20] or device.android_version
    device.app_version = str(info.get("app_version") or "")[:20] or device.app_version
    device.last_ip = ip
    db.session.flush()
    sync_sims(device, info.get("sims") or [])
    pc.used_at = utcnow()
    pc.device_id = device.id
    audit("device.pair", device, {"model": device.model}, user_id=pc.created_by)
    realtime.to_dashboard("device.paired", {"id": str(device.uuid), "name": device.name})
    return device, token


def sync_sims(device: Device, sims: list[dict]) -> None:
    """Met à jour la liste des SIM remontée par le téléphone."""
    existing = {s.slot_index: s for s in device.sims}
    seen = set()
    for s in sims:
        try:
            slot = int(s.get("slot", s.get("slot_index", 0)))
        except (TypeError, ValueError):
            continue
        seen.add(slot)
        sim = existing.get(slot)
        if sim is None:
            sim = DeviceSim(slot_index=slot)
            device.sims.append(sim)
        sim.subscription_id = s.get("subscription_id")
        sim.carrier = (s.get("carrier") or "")[:60] or None
        sim.phone_number = (s.get("phone_number") or "")[:20] or None
        sim.is_active = True
    for slot, sim in existing.items():
        if slot not in seen and sims:
            sim.is_active = False
    if device.sims and device.default_sim_slot not in {s.slot_index for s in device.sims if s.is_active}:
        active = [s.slot_index for s in device.sims if s.is_active]
        device.default_sim_slot = active[0] if active else 0


def rotate_token(device: Device) -> str:
    token = generate_token(TOKEN_PREFIX, 30)
    device.token_hash = hash_token(token)
    device.token_prefix = token[:12]
    realtime.to_device(device.id, "device.revoked", {"reason": "token_rotated"})
    audit("device.rotate_token", device)
    return token


def revoke_device(device: Device) -> None:
    device.revoked_at = utcnow()
    device.status = DEVICE_DISABLED
    requeue_unsent(device, include_acked=True)
    realtime.to_device(device.id, "device.revoked", {"reason": "revoked"})
    audit("device.revoke", device)


# --- Authentification ------------------------------------------------------------------

def authenticate(token: str | None) -> Device:
    if not token:
        raise DeviceAuthError("missing_token")
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    device = db.session.query(Device).filter_by(token_hash=hash_token(token)).one_or_none()
    if device is None or device.revoked_at is not None:
        raise DeviceAuthError("invalid_token")
    return device


# --- Heartbeat & état ---------------------------------------------------------------------

def heartbeat(device: Device, data: dict | None = None, ip: str | None = None) -> bool:
    """Met à jour l'état. Retourne True si l'appareil vient de repasser en ligne."""
    data = data or {}
    was_offline = device.status != DEVICE_ONLINE
    device.last_seen_at = utcnow()
    if ip:
        device.last_ip = ip
    if device.is_enabled and device.revoked_at is None:
        device.status = DEVICE_ONLINE
    for field, cast in (("battery_level", int), ("signal_level", int), ("local_queue_size", int)):
        key = field if field in data else field.replace("_level", "")
        if data.get(key) is not None:
            try:
                setattr(device, field, cast(data[key]))
            except (TypeError, ValueError):
                pass
    if "is_charging" in data or "charging" in data:
        device.is_charging = bool(data.get("is_charging", data.get("charging")))
    for field in ("app_version", "android_version"):
        if data.get(field):
            setattr(device, field, str(data[field])[:20])
    if data.get("sims"):
        sync_sims(device, data["sims"])

    if was_offline and device.status == DEVICE_ONLINE:
        webhooks.emit_event("device.online", device_payload(device))
    realtime.to_dashboard("device.update", device_payload(device))
    return was_offline and device.status == DEVICE_ONLINE


def device_payload(device: Device) -> dict:
    return {
        "id": str(device.uuid), "name": device.name, "status": device.status,
        "battery_level": device.battery_level, "is_charging": device.is_charging,
        "signal_level": device.signal_level, "local_queue_size": device.local_queue_size,
        "last_seen_at": device.last_seen_at.isoformat() if device.last_seen_at else None,
        "model": device.model, "app_version": device.app_version,
        "sims": [{"slot": s.slot_index, "carrier": s.carrier, "phone_number": s.phone_number,
                  "active": s.is_active} for s in device.sims],
    }


def mark_offline_devices() -> list[Device]:
    """Passe hors ligne les appareils silencieux depuis DEVICE_OFFLINE_AFTER_SEC."""
    limit = utcnow() - timedelta(seconds=current_app.config["DEVICE_OFFLINE_AFTER_SEC"])
    stale = (db.session.query(Device)
             .filter(Device.status == DEVICE_ONLINE,
                     sa.or_(Device.last_seen_at.is_(None), Device.last_seen_at < limit))
             .all())
    for device in stale:
        device.status = DEVICE_OFFLINE
        requeue_unsent(device, include_acked=False)
        webhooks.emit_event("device.offline", device_payload(device))
        notify("device.offline", f"Appareil hors ligne : {device.name}",
               f"Dernier contact : {device.last_seen_at:%d/%m/%Y %H:%M} UTC" if device.last_seen_at else None,
               "/devices")
        realtime.to_dashboard("device.update", device_payload(device))
    return stale


def requeue_unsent(device: Device, include_acked: bool) -> int:
    """Remet en file les messages transmis à l'appareil mais pas encore envoyés.
    Les messages acquittés restent dans la file Room du téléphone (il les enverra
    à son retour), sauf si include_acked (appareil révoqué)."""
    q = db.session.query(Message).filter(Message.device_id == device.id, Message.status == ST_DISPATCHED)
    if not include_acked:
        q = q.filter(Message.acked_at.is_(None))
    count = 0
    for msg in q.all():
        msg.status = ST_QUEUED
        msg.acked_at = None
        msg.next_attempt_at = utcnow()
        if not msg.device_pinned:
            msg.device_id = None
        count += 1
    return count


# --- Compteurs de débit ------------------------------------------------------------------

def usage(device: Device) -> dict:
    """SMS confiés à l'appareil sur la dernière heure et la journée locale (pour le budget)."""
    now = utcnow()
    hour_ago = now - timedelta(hours=1)
    day_start, _ = local_day_bounds(local_today())
    base = db.session.query(sa.func.count(Message.id)).filter(Message.device_id == device.id,
                                                              Message.direction == "out")
    last_hour = base.filter(Message.dispatched_at >= hour_ago).scalar()
    today = base.filter(Message.dispatched_at >= day_start).scalar()
    in_flight = base.filter(Message.status == ST_DISPATCHED).scalar()
    return {"last_hour": last_hour, "today": today, "in_flight": in_flight}


def push_config(device: Device) -> None:
    realtime.to_device(device.id, "config.update", device.config_payload())
