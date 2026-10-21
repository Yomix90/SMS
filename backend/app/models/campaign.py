"""Modèles de messages, imports Excel/CSV, campagnes."""
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import BigIntPK, JSONType, PublicIdMixin, TimestampMixin, UTCDateTime, utcnow


class Template(PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "templates"
    __table_args__ = (sa.UniqueConstraint("name", "language", name="uq_templates_name_lang"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    category: Mapped[str] = mapped_column(sa.String(40), default="general", nullable=False)
    language: Mapped[str] = mapped_column(sa.String(5), default="fr", nullable=False)
    body: Mapped[str] = mapped_column(sa.Text, nullable=False)
    variables: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))


IMPORT_UPLOADED = "uploaded"
IMPORT_MAPPED = "mapped"
IMPORT_VALIDATED = "validated"
IMPORT_SENT = "sent"
IMPORT_FAILED = "failed"


class Import(PublicIdMixin, db.Model):
    __tablename__ = "imports"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    original_filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    stored_path: Mapped[str | None] = mapped_column(sa.String(255))
    sheet_name: Mapped[str | None] = mapped_column(sa.String(100))
    header_row: Mapped[int] = mapped_column(sa.Integer, default=1, nullable=False)
    columns: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    mapping: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(15), default=IMPORT_UPLOADED, nullable=False)
    total_rows: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    valid_rows: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    invalid_rows: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    duplicate_rows: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    save_contacts: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    error_report_path: Mapped[str | None] = mapped_column(sa.String(255))
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    rows = relationship("ImportRow", back_populates="import_", cascade="all, delete-orphan",
                        order_by="ImportRow.row_number", lazy="dynamic")


class ImportRow(db.Model):
    __tablename__ = "import_rows"
    __table_args__ = (sa.Index("ix_import_rows_status", "import_id", "status"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    import_id: Mapped[int] = mapped_column(sa.ForeignKey("imports.id", ondelete="CASCADE"), nullable=False)
    row_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    raw: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    phone_raw: Mapped[str | None] = mapped_column(sa.String(60))
    phone_e164: Mapped[str | None] = mapped_column(sa.String(20))
    variables: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    custom_message: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[str] = mapped_column(sa.String(12), default="pending", nullable=False)
    error: Mapped[str | None] = mapped_column(sa.String(255))
    contact_id: Mapped[int | None] = mapped_column(sa.ForeignKey("contacts.id", ondelete="SET NULL"))

    import_ = relationship("Import", back_populates="rows")


CAMPAIGN_DRAFT = "draft"
CAMPAIGN_SCHEDULED = "scheduled"
CAMPAIGN_RUNNING = "running"
CAMPAIGN_PAUSED = "paused"
CAMPAIGN_COMPLETED = "completed"
CAMPAIGN_CANCELED = "canceled"


class Campaign(PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(150), nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text)
    template_id: Mapped[int | None] = mapped_column(sa.ForeignKey("templates.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(sa.String(15), default="manual", nullable=False)
    import_id: Mapped[int | None] = mapped_column(sa.ForeignKey("imports.id", ondelete="SET NULL"))
    audience: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    device_id: Mapped[int | None] = mapped_column(sa.ForeignKey("devices.id", ondelete="SET NULL"))
    sim_slot: Mapped[int | None] = mapped_column(sa.SmallInteger)
    status: Mapped[str] = mapped_column(sa.String(12), default=CAMPAIGN_DRAFT, nullable=False, index=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    add_stop_footer: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    total: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    queued: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    sent: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    delivered: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    failed: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    blocked: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    api_key_id: Mapped[int | None] = mapped_column(sa.ForeignKey("api_keys.id", ondelete="SET NULL"))

    template = relationship("Template")
    device = relationship("Device")
    import_ = relationship("Import")
    creator = relationship("User")

    @property
    def progress(self) -> int:
        done = self.sent + self.delivered + self.failed + self.blocked
        return int(done * 100 / self.total) if self.total else 0
