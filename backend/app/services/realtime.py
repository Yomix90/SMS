"""Diffusion temps réel (Socket.IO) exécutée APRÈS le commit de la transaction.

Pousser une tâche au téléphone avant le commit créerait une course : le téléphone
pourrait accuser réception d'un message encore invisible pour les autres sessions.
"""
import logging

from flask import current_app
from sqlalchemy import event
from sqlalchemy.orm import Session

from ..extensions import db, socketio

log = logging.getLogger(__name__)

NS_DEVICE = "/device"
NS_DASHBOARD = "/dashboard"
ROOM_DASHBOARD = "dashboard"


def device_room(device_id: int) -> str:
    return f"device:{device_id}"


def after_commit(fn, *args, **kwargs) -> None:
    """Planifie fn(*args) pour exécution après le prochain commit de db.session."""
    db.session.info.setdefault("post_commit", []).append((fn, args, kwargs))


@event.listens_for(Session, "after_commit")
def _run_post_commit(session):
    callbacks = session.info.pop("post_commit", [])
    for fn, args, kwargs in callbacks:
        try:
            fn(*args, **kwargs)
        except Exception:  # une diffusion ratée ne doit jamais casser la requête
            log.exception("post-commit callback failed: %s", getattr(fn, "__name__", fn))


@event.listens_for(Session, "after_rollback")
def _clear_post_commit(session):
    session.info.pop("post_commit", None)


def _emit(event_name: str, data, namespace: str, room: str) -> None:
    if current_app and current_app.config.get("TESTING") and not current_app.config.get("SOCKETIO_IN_TESTS"):
        sent = current_app.extensions.setdefault("test_emits", [])
        sent.append({"event": event_name, "data": data, "namespace": namespace, "room": room})
        return
    socketio.emit(event_name, data, namespace=namespace, to=room)


def to_dashboard(event_name: str, data) -> None:
    after_commit(_emit, event_name, data, NS_DASHBOARD, ROOM_DASHBOARD)


def to_device(device_id: int, event_name: str, data) -> None:
    after_commit(_emit, event_name, data, NS_DEVICE, device_room(device_id))
