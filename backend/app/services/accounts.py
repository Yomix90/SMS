"""Utilisateurs et clés API."""
from datetime import datetime

from ..extensions import db
from ..models import ApiKey, User, utcnow
from ..models.user import ROLES
from .audit import audit
from .security import generate_token, hash_token, password_problems

API_KEY_PREFIX = "sk_"
API_SCOPES = (
    "messages:send", "messages:read", "inbox:read", "contacts:read", "contacts:write",
    "templates:read", "templates:write", "schedules:read", "schedules:write",
    "devices:read", "campaigns:read", "campaigns:write", "webhooks:manage",
)


class AccountError(ValueError):
    pass


def create_user(email: str, password: str, role: str = "operator", full_name: str | None = None,
                locale: str = "fr", check_strength: bool = True) -> User:
    email = (email or "").strip().lower()
    if "@" not in email:
        raise AccountError("invalid_email")
    if role not in ROLES:
        raise AccountError("invalid_role")
    if db.session.query(User.id).filter_by(email=email).first():
        raise AccountError("email_taken")
    if check_strength and password_problems(password):
        raise AccountError("weak_password:" + ",".join(password_problems(password)))
    user = User(email=email, role=role, full_name=full_name, locale=locale)
    user.set_password(password)
    db.session.add(user)
    return user


def create_api_key(name: str, scopes: list[str], *, created_by: int | None = None, rate_limit_per_min: int = 60,
                   daily_quota: int | None = None, expires_at: datetime | None = None,
                   allow_outside_window: bool = False) -> tuple[ApiKey, str]:
    """Retourne (clé, valeur en clair affichée une seule fois)."""
    invalid = [s for s in scopes if s != "*" and s not in API_SCOPES]
    if invalid or not scopes:
        raise AccountError("invalid_scopes:" + ",".join(invalid))
    token = generate_token(API_KEY_PREFIX, 32)
    key = ApiKey(name=name[:100], prefix=token[:12], key_hash=hash_token(token), scopes=sorted(set(scopes)),
                 rate_limit_per_min=rate_limit_per_min, daily_quota=daily_quota, expires_at=expires_at,
                 allow_outside_window=allow_outside_window, created_by=created_by)
    db.session.add(key)
    db.session.flush()
    audit("api_key.create", key, {"name": name, "scopes": key.scopes}, user_id=created_by)
    return key, token


def revoke_api_key(key: ApiKey, user_id: int | None = None) -> None:
    key.revoked_at = utcnow()
    audit("api_key.revoke", key, user_id=user_id)


def rotate_api_key(key: ApiKey, user_id: int | None = None) -> tuple[ApiKey, str]:
    """Révoque la clé et en crée une nouvelle avec les mêmes droits."""
    revoke_api_key(key, user_id)
    return create_api_key(key.name, key.scopes, created_by=user_id, rate_limit_per_min=key.rate_limit_per_min,
                          daily_quota=key.daily_quota, expires_at=key.expires_at,
                          allow_outside_window=key.allow_outside_window)


def find_api_key(token: str) -> ApiKey | None:
    if not token or not token.startswith(API_KEY_PREFIX):
        return None
    return db.session.query(ApiKey).filter_by(key_hash=hash_token(token)).one_or_none()
