"""Téléphones passerelles, SIM et codes d'appairage."""
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import BigIntPK, PublicIdMixin, TimestampMixin, UTCDateTime, utcnow

DEVICE_ONLINE = "online"
DEVICE_OFFLINE = "offline"
DEVICE_DISABLED = "disabled"


class Device(PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    token_prefix: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    model: Mapped[str | None] = mapped_column(sa.String(100))
    manufacturer: Mapped[str | None] = mapped_column(sa.String(100))
    android_version: Mapped[str | None] = mapped_column(sa.String(20))
    app_version: Mapped[str | None] = mapped_column(sa.String(20))
    status: Mapped[str] = mapped_column(sa.String(15), default=DEVICE_OFFLINE, nullable=False)
    battery_level: Mapped[int | None] = mapped_column(sa.SmallInteger)
    is_charging: Mapped[bool | None] = mapped_column(sa.Boolean)
    signal_level: Mapped[int | None] = mapped_column(sa.SmallInteger)
    local_queue_size: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_ip: Mapped[str | None] = mapped_column(sa.String(45))
    default_sim_slot: Mapped[int] = mapped_column(sa.SmallInteger, default=0, nullable=False)
    send_interval_sec: Mapped[int] = mapped_column(sa.Integer, default=5, nullable=False)
    max_per_hour: Mapped[int] = mapped_column(sa.Integer, default=100, nullable=False)
    max_per_day: Mapped[int] = mapped_column(sa.Integer, default=500, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))

    sims = relationship("DeviceSim", back_populates="device", cascade="all, delete-orphan",
                        order_by="DeviceSim.slot_index")

    @property
    def is_online(self) -> bool:
        return self.status == DEVICE_ONLINE

    @property
    def can_send(self) -> bool:
        return self.is_enabled and self.revoked_at is None

    def config_payload(self) -> dict:
        """Paramètres envoyés au téléphone (limites gérées depuis le tableau de bord)."""
        return {
            "device_id": str(self.uuid),
            "name": self.name,
            "default_sim_slot": self.default_sim_slot,
            "send_interval_sec": self.send_interval_sec,
            "max_per_hour": self.max_per_hour,
            "max_per_day": self.max_per_day,
            "heartbeat_sec": 30,
            "enabled": self.can_send,
        }


class DeviceSim(db.Model):
    __tablename__ = "device_sims"
    __table_args__ = (sa.UniqueConstraint("device_id", "slot_index", name="uq_device_sims_slot"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    device_id: Mapped[int] = mapped_column(sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    slot_index: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    subscription_id: Mapped[int | None] = mapped_column(sa.Integer)
    carrier: Mapped[str | None] = mapped_column(sa.String(60))
    phone_number: Mapped[str | None] = mapped_column(sa.String(20))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    sent_today: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    counter_date: Mapped[date | None] = mapped_column(sa.Date)

    device = relationship("Device", back_populates="sims")


class PairingCode(db.Model):
    __tablename__ = "pairing_codes"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    code_hash: Mapped[str] = mapped_column(sa.String(64), unique=True, nullable=False)
    device_name: Mapped[str | None] = mapped_column(sa.String(100))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    device_id: Mapped[int | None] = mapped_column(sa.ForeignKey("devices.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    @property
    def is_usable(self) -> bool:
        return self.used_at is None and self.expires_at > utcnow()
