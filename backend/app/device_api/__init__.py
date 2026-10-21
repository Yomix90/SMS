"""API HTTP du téléphone (appairage + repli en polling quand le WebSocket est indisponible).

Authentification : `Authorization: Bearer dev_…` (token remis à l'appairage).
"""
from functools import wraps

from flask import Blueprint, g, jsonify, request

from ..api.errors import error_response
from ..extensions import db
from ..services import devices as device_service
from ..services import dispatcher, inbound
from ..services.security import rate_limiter

bp = Blueprint("device_api", __name__, url_prefix="/device/v1")

MAX_BATCH = 200


def _ip() -> str | None:
    return request.headers.get("X-Real-IP") or request.remote_addr


def device_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            g.device = device_service.authenticate(request.headers.get("Authorization"))
        except device_service.DeviceAuthError as exc:
            return error_response(401, str(exc), "Token d'appareil invalide ou révoqué")
        return fn(*args, **kwargs)
    return wrapper


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.post("/pair")
def pair():
    """Corps : {"code", "model", "manufacturer", "android_version", "app_version", "sims": [...]}"""
    allowed, _, reset = rate_limiter.hit(f"pair:{_ip()}", 10, 600)
    if not allowed:
        return error_response(429, "rate_limited", "Trop de tentatives d'appairage", headers={"Retry-After": str(reset)})
    data = _json()
    try:
        device, token = device_service.pair_device(data.get("code"), data, _ip())
    except device_service.DeviceAuthError as exc:
        db.session.rollback()
        return error_response(400, str(exc), "Code d'appairage invalide ou expiré : régénérez le QR code")
    db.session.commit()
    return jsonify({"token": token, "device": device.config_payload()}), 201


@bp.get("/config")
@device_required
def config():
    return jsonify(g.device.config_payload())


@bp.post("/heartbeat")
@device_required
def heartbeat():
    device_service.heartbeat(g.device, _json(), _ip())
    db.session.commit()
    return jsonify({"ok": True, "config": g.device.config_payload(),
                    "pending": len(dispatcher.pending_tasks(g.device, 1))})


@bp.get("/tasks")
@device_required
def tasks():
    """Messages à envoyer (transmis et non acquittés). Vaut aussi heartbeat."""
    limit = min(request.args.get("limit", 50, type=int), MAX_BATCH)
    device_service.heartbeat(g.device, {}, _ip())
    msgs = dispatcher.pending_tasks(g.device, limit)
    db.session.commit()
    return jsonify({"messages": [m.to_device_task() for m in msgs]})


@bp.post("/tasks/ack")
@device_required
def ack():
    ids = (_json().get("ids") or [])[:MAX_BATCH]
    count = dispatcher.ack(g.device, ids)
    db.session.commit()
    return jsonify({"acked": count})


@bp.post("/status")
@device_required
def status():
    """Corps : {"statuses": [{"id", "status", "error_code", "error_message", "timestamp", "sim_slot"}]}"""
    reports = (_json().get("statuses") or [])[:MAX_BATCH]
    applied = 0
    for report in reports:
        if isinstance(report, dict) and dispatcher.apply_status(g.device, report):
            applied += 1
    db.session.commit()
    # Le téléphone purge sa boîte d'envoi locale pour tous les ids renvoyés
    return jsonify({"received": [r.get("id") for r in reports if isinstance(r, dict)], "applied": applied})


@bp.post("/inbound")
@device_required
def receive():
    """Corps : {"messages": [{"uuid", "phone", "body", "received_at", "sim_slot"}]}"""
    items = (_json().get("messages") or [])[:MAX_BATCH]
    stored = []
    for item in items:
        if not isinstance(item, dict) or not item.get("phone"):
            continue
        msg, _created = inbound.receive(g.device, item)
        stored.append(item.get("uuid") or item.get("id"))
    db.session.commit()
    return jsonify({"received": stored})
