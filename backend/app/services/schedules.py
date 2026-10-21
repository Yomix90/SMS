"""Envois planifiés (unique, quotidien, hebdomadaire, mensuel) en heure de Casablanca."""
import calendar
import logging
from datetime import date, datetime, timedelta

from ..extensions import db
from ..models import Device, Schedule, utcnow
from ..models.automation import RECUR_DAILY, RECUR_MONTHLY, RECUR_ONCE, RECUR_WEEKLY
from . import campaigns, queue
from .audit import audit
from .timeutil import local_to_utc, to_local

log = logging.getLogger(__name__)


def _monthly_date(year: int, month: int, day: int) -> date:
    """Le 31 d'un mois de 30 jours devient le dernier jour du mois."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def compute_next_run(s: Schedule, after: datetime | None = None) -> datetime | None:
    """Prochaine exécution (UTC) strictement après `after` (par défaut maintenant)."""
    after = after or utcnow()
    local_after = to_local(after)
    start = max(filter(None, [s.start_date, local_after.date()]))

    def ok(d: date) -> bool:
        if s.end_date and d > s.end_date:
            return False
        return local_to_utc(d, s.run_time) > after

    if s.recurrence == RECUR_ONCE:
        if s.run_date and ok(s.run_date):
            return local_to_utc(s.run_date, s.run_time)
        return None

    if s.recurrence == RECUR_DAILY:
        candidates = (start + timedelta(days=i) for i in range(0, 3))
    elif s.recurrence == RECUR_WEEKLY:
        days = set(s.days_of_week or []) or {start.isoweekday()}
        candidates = (start + timedelta(days=i) for i in range(0, 15)
                      if (start + timedelta(days=i)).isoweekday() in days)
    elif s.recurrence == RECUR_MONTHLY:
        dom = s.day_of_month or start.day

        def months():
            y, m = start.year, start.month
            for _ in range(14):
                yield _monthly_date(y, m, dom)
                m += 1
                if m > 12:
                    y, m = y + 1, 1
        candidates = months()
    else:
        return None

    for d in candidates:
        if s.end_date and d > s.end_date:
            return None
        if ok(d):
            return local_to_utc(d, s.run_time)
    return None


def run_schedule(s: Schedule) -> None:
    """Crée la campagne de cette occurrence et calcule la suivante."""
    now = utcnow()
    try:
        result = campaigns.create_campaign(
            f"{s.name} — {to_local(now):%d/%m/%Y %H:%M}", body=s.body,
            template=s.template if not s.body else None, audience=s.audience,
            device=db.session.get(Device, s.device_id) if s.device_id else None,
            sim_slot=s.sim_slot, add_stop_footer=s.add_stop_footer, source="schedule")
        s.last_campaign_id = result.campaign.id
        audit("schedule.run", s, {"campaign": result.campaign.id, "recipients": result.created})
    except queue.QueueError as exc:
        from .audit import notify
        notify("schedule.failed", f"Planification « {s.name} » non exécutée", exc.message, "/schedules")
        log.warning("schedule %s skipped: %s", s.id, exc.code)
    s.last_run_at = now
    s.next_run_at = None if s.recurrence == RECUR_ONCE else compute_next_run(s, now)
    if s.next_run_at is None:
        s.is_active = False


def run_due_schedules() -> int:
    due = (db.session.query(Schedule)
           .filter(Schedule.is_active.is_(True), Schedule.next_run_at.isnot(None),
                   Schedule.next_run_at <= utcnow())
           .all())
    for s in due:
        run_schedule(s)
        db.session.commit()
    return len(due)
