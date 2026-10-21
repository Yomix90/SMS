"""Journal d'audit et notifications internes."""
from flask import g, has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog, Notification
from . import realtime


def _client_ip() -> str | None:
    if not has_request_context():
        return None
    # Nginx renseigne X-Real-IP ; ProxyFix gère X-Forwarded-For
    return request.headers.get("X-Real-IP") or request.remote_addr


def audit(action: str, entity=None, details: dict | None = None, *, user_id: int | None = None,
          api_key_id: int | None = None) -> AuditLog:
    if has_request_context():
        if user_id is None and getattr(current_user, "is_authenticated", False):
            user_id = current_user.id
        if api_key_id is None and getattr(g, "api_key", None) is not None:
            api_key_id = g.api_key.id
    log = AuditLog(
        action=action,
        user_id=user_id,
        api_key_id=api_key_id,
        entity_type=entity.__tablename__ if entity is not None else None,
        entity_id=getattr(entity, "id", None),
        ip=_client_ip(),
        user_agent=(request.headers.get("User-Agent", "")[:255] if has_request_context() else None),
        details=details,
    )
    db.session.add(log)
    return log


def notify(type_: str, title: str, body: str | None = None, link: str | None = None,
           user_id: int | None = None) -> Notification:
    """user_id=None : notification visible par tous les administrateurs."""
    n = Notification(type=type_, title=title, body=body, link=link, user_id=user_id)
    db.session.add(n)
    realtime.to_dashboard("notification", {"type": type_, "title": title, "body": body, "link": link})
    return n
