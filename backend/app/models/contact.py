"""Contacts, groupes, liste noire, échéances."""
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import BigIntPK, JSONType, PublicIdMixin, TimestampMixin, UTCDateTime, utcnow

CONSENT_OPT_IN = "opt_in"
CONSENT_UNKNOWN = "unknown"
CONSENT_OPTED_OUT = "opted_out"

contact_groups = sa.Table(
    "contact_groups",
    db.metadata,
    sa.Column("contact_id", sa.ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("group_id", sa.ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True),
)


class Contact(PublicIdMixin, TimestampMixin, db.Model):
    __tablename__ = "contacts"
    __table_args__ = (sa.Index("ix_contacts_name", "last_name", "first_name"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    phone: Mapped[str] = mapped_column(sa.String(20), unique=True, nullable=False)
    first_name: Mapped[str | None] = mapped_column(sa.String(80))
    last_name: Mapped[str | None] = mapped_column(sa.String(80))
    email: Mapped[str | None] = mapped_column(sa.String(255))
    birthday: Mapped[date | None] = mapped_column(sa.Date)
    locale: Mapped[str] = mapped_column(sa.String(5), default="fr", nullable=False)
    custom_fields: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    consent_status: Mapped[str] = mapped_column(sa.String(15), default=CONSENT_UNKNOWN, nullable=False)
    consent_source: Mapped[str | None] = mapped_column(sa.String(40))
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    opted_out_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notes: Mapped[str | None] = mapped_column(sa.Text)

    groups = relationship("Group", secondary=contact_groups, back_populates="contacts")
    receivables = relationship("Receivable", back_populates="contact", cascade="all, delete-orphan")

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p) or ""

    @property
    def is_opted_out(self) -> bool:
        return self.consent_status == CONSENT_OPTED_OUT


class Group(db.Model):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(255))
    color: Mapped[str | None] = mapped_column(sa.String(9))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    contacts = relationship("Contact", secondary=contact_groups, back_populates="groups")


class Blacklist(db.Model):
    __tablename__ = "blacklist"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    phone: Mapped[str] = mapped_column(sa.String(20), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(sa.String(30), default="manuel", nullable=False)
    source_message_id: Mapped[int | None] = mapped_column(sa.ForeignKey("messages.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


RECEIVABLE_UNPAID = "unpaid"
RECEIVABLE_PAID = "paid"
RECEIVABLE_CANCELED = "canceled"


class Receivable(TimestampMixin, db.Model):
    """Échéance / facture d'un client : alimente {montant}, {date_echeance}, {reference}."""

    __tablename__ = "receivables"
    __table_args__ = (
        sa.UniqueConstraint("contact_id", "reference", name="uq_receivables_ref"),
        sa.Index("ix_receivables_due", "status", "due_date"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    contact_id: Mapped[int] = mapped_column(sa.ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    reference: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    amount: Mapped[Decimal] = mapped_column(sa.Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(sa.String(3), default="MAD", nullable=False)
    due_date: Mapped[date] = mapped_column(sa.Date, nullable=False)
    status: Mapped[str] = mapped_column(sa.String(12), default=RECEIVABLE_UNPAID, nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    extra: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)

    contact = relationship("Contact", back_populates="receivables")
