"""Handlers Socket.IO.

Namespace /device (téléphones) — authentification par token dans `auth` :
    io.connect(url, {auth: {token: "dev_…"}, path: "/socket.io"})
    serveur -> téléphone : task.send {messages}, task.cancel {ids}, config.update {...}, device.revoked
    téléphone -> serveur : heartbeat {...}, task.ack {ids}, sms.status {statuses}, sms.received {messages}
    Les événements téléphone -> serveur répondent par un ACK Socket.IO ({ok, ...}).

Namespace /dashboard (navigateur) — session Flask-Login requise ; diffusion de
device.update, message.new, message.update, campaign.update, inbox.new, notification.
"""
import logging

from flask import request
from flask_login import current_user
from flask_socketio import ConnectionRefusedError, Namespace, disconnect, join_room

from ..extensions import db, socketio
from ..services import devices as device_service
from ..services import dispatcher, inbound
from ..services.realtime import NS_DASHBOARD, NS_DEVICE, ROOM_DASHBOARD, device_room

log = logging.getLogger(__name__)

# sid -> device_id (process unique)
_connected: dict[str, int] = {}


def connected_device_ids() -> set[int]:
    return set(_connected.values())


def _device_for_sid():
    device_id = _connected.get(request.sid)
    if device_id is None:
        disconnect()
        return None
    from ..models import Device
    device = db.session.get(Device, device_id)
    if device is None or device.revoked_at is not None:
        disconnect()
        return None
    return device


def _ip():
    return request.headers.get("X-Real-IP") or request.remote_addr


class DeviceNamespace(Namespace):
    def on_connect(self, auth=None):
        token = (auth or {}).get("token") if isinstance(auth, dict) else None
        token = token or request.headers.get("Authorization") or request.args.get("token")
        try:
            device = device_service.authenticate(token)
        except device_service.DeviceAuthError as exc:
            db.session.rollback()
            raise ConnectionRefusedError(str(exc))
        _connected[request.sid] = device.id
        join_room(device_room(device.id))
        device_service.heartbeat(device, {}, _ip())
        pending = [m.to_device_task() for m in dispatcher.pending_tasks(device, 100)]
        db.session.commit()
        self.emit("config.update", device.config_payload(), room=request.sid)
        if pending:  # tâches transmises pendant une coupure
            self.emit("task.send", {"messages": pending}, room=request.sid)
        log.info("device %s connected (%s)", device.id, request.sid)

    def on_disconnect(self, reason=None):
        device_id = _connected.pop(request.sid, None)
        log.info("device %s disconnected", device_id)
        # Le passage hors ligne est décidé par l'absence de heartbeat (coupures brèves tolérées)

    def on_heartbeat(self, data=None):
        device = _device_for_sid()
        if device is None:
            return {"ok": False}
        device_service.heartbeat(device, data if isinstance(data, dict) else {}, _ip())
        db.session.commit()
        return {"ok": True}

    def on_task_ack(self, data=None):
        device = _device_for_sid()
        if device is None:
            return {"ok": False}
        count = dispatcher.ack(device, (data or {}).get("ids") or [])
        db.session.commit()
        return {"ok": True, "acked": count}

    def on_sms_status(self, data=None):
        device = _device_for_sid()
        if device is None:
            return {"ok": False}
        reports = (data or {}).get("statuses") or ([data] if isinstance(data, dict) and data.get("id") else [])
        for report in reports[:200]:
            if isinstance(report, dict):
                dispatcher.apply_status(device, report)
        db.session.commit()
        return {"ok": True, "received": [r.get("id") for r in reports if isinstance(r, dict)]}

    def on_sms_received(self, data=None):
        device = _device_for_sid()
        if device is None:
            return {"ok": False}
        items = (data or {}).get("messages") or ([data] if isinstance(data, dict) and data.get("phone") else [])
        received = []
        for item in items[:200]:
            if isinstance(item, dict) and item.get("phone"):
                inbound.receive(device, item)
                received.append(item.get("uuid") or item.get("id"))
        db.session.commit()
        return {"ok": True, "received": received}

    def trigger_event(self, event, *args):
        """Les noms d'événements contiennent des points (task.ack) : on les mappe sur on_task_ack."""
        return super().trigger_event((event or "").replace(".", "_"), *args)


class DashboardNamespace(Namespace):
    def on_connect(self, auth=None):
        if not current_user.is_authenticated:
            raise ConnectionRefusedError("unauthorized")
        join_room(ROOM_DASHBOARD)

    def on_disconnect(self, reason=None):
        pass


def init_sockets(app):
    socketio.init_app(
        app,
        cors_allowed_origins=[app.config["PUBLIC_BASE_URL"]],
        ping_interval=25,
        ping_timeout=20,
        max_http_buffer_size=1_000_000,
        logger=False,
        engineio_logger=False,
    )
    socketio.on_namespace(DeviceNamespace(NS_DEVICE))

    @socketio.on_error(NS_DEVICE)
    def _device_error(exc):  # la réponse sert d'ACK au téléphone, qui réessaiera
        db.session.rollback()
        log.exception("device socket event failed: %s", exc)
        return {"ok": False, "error": "server_error"}
    socketio.on_namespace(DashboardNamespace(NS_DASHBOARD))
