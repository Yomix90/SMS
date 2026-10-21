"""Utilisateurs, clés API, audit, réglages, notifications."""
from datetime import datetime

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import BigIntPK, JSONType, PublicIdMixin, TimestampMixin, UTCDateTime, utcnow

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"
ROLES = (ROLE_ADMIN, ROLE_OPERATOR)


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    email: Mapped[str] = mapped_column(sa.String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(sa.String(120))
    role: Mapped[str] = mapped_column(sa.String(20), default=ROLE_OPERATOR, nullable=False)
    locale: Mapped[str] = mapped_column(sa.String(5), default="fr", nullable=False)
    theme: Mapped[str] = mapped_column(sa.String(10), default="auto", nullable=False)
    is_active_flag: Mapped[bool] = mapped_column("is_active", sa.Boolean, default=True, nullable=False)
    failed_logins: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    @property
    def is_active(self) -> bool:  # utilisé par Flask-Login
        return self.is_active_flag

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    def set_password(self, password: str) -> None:
        from ..services.security import hash_password
        self.password_hash = hash_password(password)

    def check_password(self, password: str) -> bool:
        from ..services.security import verify_password
        ok, new_hash = verify_password(self.password_hash, password)
        if ok and new_hash:
            self.password_hash = new_hash  # re-hachage si paramètres argon2 plus forts
        return ok

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class ApiKey(PublicIdMixin, db.Model):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    prefix: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    scopes: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    rate_limit_per_min: Mapped[int] = mapped_column(sa.Integer, default=60, nullable=False)
    daily_quota: Mapped[int | None] = mapped_column(sa.Integer)
    allow_outside_window: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_used_ip: Mapped[str | None] = mapped_column(sa.String(45))
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    creator = relationship("User")

    def has_scope(self, scope: str) -> bool:
        return "*" in (self.scopes or []) or scope in (self.scopes or [])

    @property
    def is_valid(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > utcnow()


class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    __table_args__ = (sa.Index("ix_audit_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    api_key_id: Mapped[int | None] = mapped_column(sa.ForeignKey("api_keys.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(sa.String(40))
    entity_id: Mapped[int | None] = mapped_column(sa.BigInteger)
    ip: Mapped[str | None] = mapped_column(sa.String(45))
    user_agent: Mapped[str | None] = mapped_column(sa.String(255))
    details: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)

    user = relationship("User")
    api_key = relationship("ApiKey")


class Setting(db.Model):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(sa.String(80), primary_key=True)
    value: Mapped[dict | list | str | int | None] = mapped_column(JSONType)
    updated_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False)


class Notification(db.Model):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="CASCADE"))  # null = tous les admins
    type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text)
    link: Mapped[str | None] = mapped_column(sa.String(255))
    is_read: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)


class SchedulerLock(db.Model):
    __tablename__ = "scheduler_locks"

    name: Mapped[str] = mapped_column(sa.String(60), primary_key=True)
    locked_until: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    owner: Mapped[str] = mapped_column(sa.String(80), nullable=False)
