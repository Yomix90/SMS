"""Messages (file d'attente + historique + boîte de réception) et conversations."""
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import BigIntPK, JSONType, PublicIdMixin, UTCDateTime, utcnow

DIR_OUT = "out"
DIR_IN = "in"

# Statuts sortants
ST_SCHEDULED = "scheduled"
ST_QUEUED = "queued"
ST_PAUSED = "paused"
ST_DISPATCHED = "dispatched"
ST_SENT = "sent"
ST_DELIVERED = "delivered"
ST_FAILED = "failed"
ST_CANCELED = "canceled"
ST_BLOCKED = "blocked"
# Statut entrant
ST_RECEIVED = "received"

OUT_STATUSES = (ST_SCHEDULED, ST_QUEUED, ST_PAUSED, ST_DISPATCHED, ST_SENT,
                ST_DELIVERED, ST_FAILED, ST_CANCELED, ST_BLOCKED)
PENDING_STATUSES = (ST_SCHEDULED, ST_QUEUED, ST_PAUSED, ST_DISPATCHED)
FINAL_STATUSES = (ST_DELIVERED, ST_FAILED, ST_CANCELED, ST_BLOCKED)

# Ordre de progression : un statut ne peut pas « reculer » (ex. delivered puis sent en retard)
STATUS_RANK = {ST_SCHEDULED: 0, ST_PAUSED: 0, ST_QUEUED: 1, ST_DISPATCHED: 2, ST_SENT: 3,
               ST_DELIVERED: 4, ST_FAILED: 4, ST_CANCELED: 5, ST_BLOCKED: 5}

conversation_labels = sa.Table(
    "conversation_labels",
    db.metadata,
    sa.Column("conversation_id", sa.ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("label_id", sa.ForeignKey("labels.id", ondelete="CASCADE"), primary_key=True),
)


class Conversation(db.Model):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    phone: Mapped[str] = mapped_column(sa.String(20), unique=True, nullable=False)
    contact_id: Mapped[int | None] = mapped_column(sa.ForeignKey("contacts.id", ondelete="SET NULL"))
    last_message_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    last_message_preview: Mapped[str | None] = mapped_column(sa.String(160))
    last_direction: Mapped[str | None] = mapped_column(sa.String(3))
    unread_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    is_archived: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    assigned_to: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    contact = relationship("Contact")
    labels = relationship("Label", secondary=conversation_labels)


class Label(db.Model):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(60), unique=True, nullable=False)
    color: Mapped[str] = mapped_column(sa.String(9), default="#6c757d", nullable=False)


class Message(PublicIdMixin, db.Model):
    __tablename__ = "messages"
    __table_args__ = (
        sa.Index("ix_msg_queue", "status", "next_attempt_at", "priority"),
        sa.Index("ix_msg_conversation", "conversation_id", "created_at"),
        sa.Index("ix_msg_campaign", "campaign_id", "status"),
        sa.Index("ix_msg_device", "device_id", "status"),
        sa.UniqueConstraint("api_key_id", "idempotency_key", name="uq_messages_idempotency"),
        sa.UniqueConstraint("device_id", "device_uuid", name="uq_messages_device_uuid"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    direction: Mapped[str] = mapped_column(sa.String(3), default=DIR_OUT, nullable=False)
    conversation_id: Mapped[int | None] = mapped_column(sa.ForeignKey("conversations.id", ondelete="SET NULL"))
    contact_id: Mapped[int | None] = mapped_column(sa.ForeignKey("contacts.id", ondelete="SET NULL"))
    phone: Mapped[str] = mapped_column(sa.String(20), nullable=False, index=True)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    encoding: Mapped[str] = mapped_column(sa.String(5), default="gsm7", nullable=False)
    segments: Mapped[int] = mapped_column(sa.SmallInteger, default=1, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    priority: Mapped[int] = mapped_column(sa.SmallInteger, default=0, nullable=False)
    campaign_id: Mapped[int | None] = mapped_column(sa.ForeignKey("campaigns.id", ondelete="SET NULL"))
    device_id: Mapped[int | None] = mapped_column(sa.ForeignKey("devices.id", ondelete="SET NULL"))
    # Appareil imposé par l'utilisateur (sinon le dispatcher peut réaffecter en cas d'échec)
    device_pinned: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    sim_slot: Mapped[int | None] = mapped_column(sa.SmallInteger)
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    attempts: Mapped[int] = mapped_column(sa.SmallInteger, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(sa.SmallInteger, default=3, nullable=False)
    dispatched_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Accusé de réception du téléphone (le lot est bien dans sa file Room)
    acked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    received_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error_code: Mapped[str | None] = mapped_column(sa.String(40))
    error_message: Mapped[str | None] = mapped_column(sa.String(255))
    ignore_window: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(sa.String(15), default="web", nullable=False)
    user_id: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    api_key_id: Mapped[int | None] = mapped_column(sa.ForeignKey("api_keys.id", ondelete="SET NULL"))
    external_ref: Mapped[str | None] = mapped_column(sa.String(100))
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(100))
    device_uuid: Mapped[str | None] = mapped_column(sa.String(64))
    is_read: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)

    conversation = relationship("Conversation")
    contact = relationship("Contact")
    campaign = relationship("Campaign")
    device = relationship("Device")
    events = relationship("MessageEvent", back_populates="message", cascade="all, delete-orphan",
                          order_by="MessageEvent.id")

    @property
    def is_final(self) -> bool:
        return self.status in FINAL_STATUSES

    def to_device_task(self) -> dict:
        """Charge utile envoyée au téléphone."""
        return {
            "id": str(self.uuid),
            "phone": self.phone,
            "body": self.body,
            "sim_slot": self.sim_slot,
            "segments": self.segments,
            "priority": self.priority,
        }


class MessageEvent(db.Model):
    __tablename__ = "message_events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    message_id: Mapped[int] = mapped_column(sa.ForeignKey("messages.id", ondelete="CASCADE"),
                                            nullable=False, index=True)
    status: Mapped[str] = mapped_column(sa.String(12), nullable=False)
    error_code: Mapped[str | None] = mapped_column(sa.String(40))
    details: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    message = relationship("Message", back_populates="events")
