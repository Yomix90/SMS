"""Planifications récurrentes, déclencheurs automatiques, réponses par mots-clés."""
from datetime import date, datetime, time

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import db
from .base import BigIntPK, JSONType, PublicIdMixin, UTCDateTime, utcnow

RECUR_ONCE = "once"
RECUR_DAILY = "daily"
RECUR_WEEKLY = "weekly"
RECUR_MONTHLY = "monthly"
RECURRENCES = (RECUR_ONCE, RECUR_DAILY, RECUR_WEEKLY, RECUR_MONTHLY)


class Schedule(PublicIdMixin, db.Model):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(150), nullable=False)
    body: Mapped[str | None] = mapped_column(sa.Text)
    template_id: Mapped[int | None] = mapped_column(sa.ForeignKey("templates.id", ondelete="SET NULL"))
    audience: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    device_id: Mapped[int | None] = mapped_column(sa.ForeignKey("devices.id", ondelete="SET NULL"))
    sim_slot: Mapped[int | None] = mapped_column(sa.SmallInteger)
    add_stop_footer: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    recurrence: Mapped[str] = mapped_column(sa.String(10), default=RECUR_ONCE, nullable=False)
    run_time: Mapped[time] = mapped_column(sa.Time, nullable=False)
    run_date: Mapped[date | None] = mapped_column(sa.Date)
    days_of_week: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)  # 1=lundi … 7=dimanche
    day_of_month: Mapped[int | None] = mapped_column(sa.SmallInteger)
    start_date: Mapped[date | None] = mapped_column(sa.Date)
    end_date: Mapped[date | None] = mapped_column(sa.Date)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_campaign_id: Mapped[int | None] = mapped_column(sa.ForeignKey("campaigns.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    template = relationship("Template")
    last_campaign = relationship("Campaign")


TRIGGER_DUE_REMINDER = "due_reminder"
TRIGGER_UNPAID = "unpaid_followup"
TRIGGER_BIRTHDAY = "birthday"
TRIGGER_WELCOME = "welcome"
TRIGGERS = (TRIGGER_DUE_REMINDER, TRIGGER_UNPAID, TRIGGER_BIRTHDAY, TRIGGER_WELCOME)


class Automation(db.Model):
    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    name: Mapped[str] = mapped_column(sa.String(150), nullable=False)
    trigger: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    template_fr_id: Mapped[int | None] = mapped_column(sa.ForeignKey("templates.id", ondelete="SET NULL"))
    template_ar_id: Mapped[int | None] = mapped_column(sa.ForeignKey("templates.id", ondelete="SET NULL"))
    audience: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    run_time: Mapped[time] = mapped_column(sa.Time, default=time(10, 0), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[int | None] = mapped_column(sa.ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)

    template_fr = relationship("Template", foreign_keys=[template_fr_id])
    template_ar = relationship("Template", foreign_keys=[template_ar_id])


class AutomationRun(db.Model):
    __tablename__ = "automation_runs"
    __table_args__ = (sa.UniqueConstraint("automation_id", "dedupe_key", name="uq_automation_runs_key"),)

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    automation_id: Mapped[int] = mapped_column(sa.ForeignKey("automations.id", ondelete="CASCADE"), nullable=False)
    contact_id: Mapped[int | None] = mapped_column(sa.ForeignKey("contacts.id", ondelete="SET NULL"))
    dedupe_key: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    message_id: Mapped[int | None] = mapped_column(sa.ForeignKey("messages.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


MATCH_EXACT = "exact"
MATCH_STARTS = "starts_with"
MATCH_CONTAINS = "contains"
MATCH_REGEX = "regex"

ACTION_REPLY = "reply"
ACTION_OPT_OUT = "opt_out"
ACTION_OPT_IN = "opt_in"
ACTION_LABEL = "label"
ACTION_ADD_GROUP = "add_group"


class KeywordRule(db.Model):
    __tablename__ = "keyword_rules"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    keywords: Mapped[list] = mapped_column(JSONType, default=list, nullable=False)
    match_type: Mapped[str] = mapped_column(sa.String(12), default=MATCH_EXACT, nullable=False)
    action: Mapped[str] = mapped_column(sa.String(12), default=ACTION_REPLY, nullable=False)
    reply_body: Mapped[str | None] = mapped_column(sa.Text)
    action_value: Mapped[str | None] = mapped_column(sa.String(100))
    priority: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    cooldown_minutes: Mapped[int] = mapped_column(sa.Integer, default=60, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    hit_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
