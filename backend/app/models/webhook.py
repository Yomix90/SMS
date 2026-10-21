"""Webhooks sortants et journal des livraisons."""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import BigIntPK, JSONType, PublicIdMixin, UTCDateTime, utcnow

WEBHOOK_EVENTS = ("sms.sent", "sms.delivered", "sms.failed", "sms.received", "device.offline", "device.online")

DELIVERY_PENDING = "pending"
DELIVERY_SUCCESS = "success"
DELIVERY_FAILED = "failed"


class Webhook(PublicIdMixin, db.Model):
    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    url: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    events: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    failure_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    api_key_id: Mapped[int | None] = mapped_column(sa.ForeignKey("api_keys.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    @property
    def secret(self) -> str:
        from ..services.security import decrypt
        return decrypt(self.secret_encrypted)


class WebhookDelivery(db.Model):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (sa.Index("ix_webhook_deliveries_queue", "status", "next_attempt_at"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    webhook_id: Mapped[int] = mapped_column(sa.ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    event: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    event_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, default=uuid.uuid4, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(10), default=DELIVERY_PENDING, nullable=False)
    attempts: Mapped[int] = mapped_column(sa.SmallInteger, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    response_code: Mapped[int | None] = mapped_column(sa.SmallInteger)
    response_body: Mapped[str | None] = mapped_column(sa.String(1024))
    duration_ms: Mapped[int | None] = mapped_column(sa.Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    webhook = relationship("Webhook")
