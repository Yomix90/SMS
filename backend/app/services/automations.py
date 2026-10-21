"""Déclencheurs automatiques : rappel avant échéance, relance impayés, anniversaire, bienvenue.

Chaque automatisation s'exécute une fois par jour, à partir de son heure `run_time`
(heure de Casablanca). La table automation_runs (clé unique par automatisation)
garantit qu'un même rappel n'est jamais envoyé deux fois, même après un redémarrage.
"""
import calendar
import logging
from datetime import date, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Automation, AutomationRun, Contact, Receivable, utcnow
from ..models.automation import TRIGGER_BIRTHDAY, TRIGGER_DUE_REMINDER, TRIGGER_UNPAID, TRIGGER_WELCOME
from ..models.contact import CONSENT_OPTED_OUT, RECEIVABLE_UNPAID, contact_groups
from . import queue
from .templating import contact_variables, render
from .timeutil import local_now, local_today, to_local

log = logging.getLogger(__name__)


def _template_for(auto: Automation, contact: Contact):
    if contact.locale == "ar" and auto.template_ar is not None:
        return auto.template_ar
    return auto.template_fr or auto.template_ar


def _audience_filter(auto: Automation, q):
    groups = (auto.audience or {}).get("groups")
    if groups:
        sub = sa.select(contact_groups.c.contact_id).where(contact_groups.c.group_id.in_(groups))
        q = q.filter(Contact.id.in_(sub))
    return q.filter(Contact.consent_status != CONSENT_OPTED_OUT)


def _send(auto: Automation, contact: Contact, key: str, receivable: Receivable | None = None) -> bool:
    """Crée le message et l'entrée de dédoublonnage. False si déjà envoyé ou impossible."""
    if db.session.query(AutomationRun.id).filter_by(automation_id=auto.id, dedupe_key=key).first():
        return False
    template = _template_for(auto, contact)
    if template is None:
        return False
    text = render(template.body, contact_variables(contact, receivable)).text
    if not text:
        return False
    try:
        with db.session.begin_nested():
            run = AutomationRun(automation_id=auto.id, contact_id=contact.id, dedupe_key=key)
            db.session.add(run)
            db.session.flush()
            msg = queue.enqueue(contact.phone, text, contact=contact, source="automation")
            run.message_id = msg.id
    except IntegrityError:
        return False
    except queue.QueueError as exc:
        log.info("automation %s: %s ignoré (%s)", auto.id, contact.phone, exc.code)
        return False
    return True


def _receivables_due_on(day: date, auto: Automation):
    q = (db.session.query(Receivable, Contact).join(Contact, Receivable.contact_id == Contact.id)
         .filter(Receivable.status == RECEIVABLE_UNPAID, Receivable.due_date == day))
    return _audience_filter(auto, q).all()


def run_due_reminder(auto: Automation, today: date) -> int:
    sent = 0
    for days in (auto.config or {}).get("days_before", [3]):
        for rec, contact in _receivables_due_on(today + timedelta(days=int(days)), auto):
            sent += _send(auto, contact, f"due:{rec.id}:J-{days}", rec)
    return sent


def run_unpaid_followup(auto: Automation, today: date) -> int:
    sent = 0
    cfg = auto.config or {}
    max_runs = int(cfg.get("max", 3))
    for days in cfg.get("days_after", [1, 7, 15]):
        for rec, contact in _receivables_due_on(today - timedelta(days=int(days)), auto):
            done = (db.session.query(sa.func.count(AutomationRun.id))
                    .filter(AutomationRun.automation_id == auto.id,
                            AutomationRun.dedupe_key.like(f"unpaid:{rec.id}:%")).scalar())
            if done >= max_runs:
                continue
            sent += _send(auto, contact, f"unpaid:{rec.id}:J+{days}", rec)
    return sent


def _birthday_matches(birthday: date, today: date) -> bool:
    if (birthday.month, birthday.day) == (today.month, today.day):
        return True
    # Nés un 29 février : fêtés le 28 les années non bissextiles
    return (birthday.month, birthday.day) == (2, 29) and not calendar.isleap(today.year) \
        and (today.month, today.day) == (2, 28)


def run_birthday(auto: Automation, today: date) -> int:
    q = _audience_filter(auto, db.session.query(Contact).filter(Contact.birthday.isnot(None)))
    sent = 0
    for contact in q.all():
        if _birthday_matches(contact.birthday, today):
            sent += _send(auto, contact, f"bday:{contact.id}:{today.year}")
    return sent


def run_welcome(auto: Automation, today: date) -> int:
    """Nouveaux contacts depuis la dernière exécution (24 h au premier passage)."""
    since = auto.last_run_at or (utcnow() - timedelta(days=1))
    group_id = (auto.config or {}).get("group_id")
    q = db.session.query(Contact).filter(Contact.created_at >= since)
    if group_id:
        sub = sa.select(contact_groups.c.contact_id).where(contact_groups.c.group_id == group_id)
        q = q.filter(Contact.id.in_(sub))
    sent = 0
    for contact in _audience_filter(auto, q).all():
        sent += _send(auto, contact, f"welcome:{contact.id}")
    return sent


RUNNERS = {
    TRIGGER_DUE_REMINDER: run_due_reminder,
    TRIGGER_UNPAID: run_unpaid_followup,
    TRIGGER_BIRTHDAY: run_birthday,
    TRIGGER_WELCOME: run_welcome,
}


def is_due(auto: Automation, now_local: datetime) -> bool:
    if now_local.time() < auto.run_time:
        return False
    if auto.trigger == TRIGGER_WELCOME:
        return True  # vérifié à chaque passage pour accueillir rapidement les nouveaux contacts
    return auto.last_run_at is None or to_local(auto.last_run_at).date() < now_local.date()


def run_automation(auto: Automation, today: date | None = None) -> int:
    today = today or local_today()
    sent = RUNNERS[auto.trigger](auto, today)
    auto.last_run_at = utcnow()
    return sent


def run_due_automations() -> dict[int, int]:
    now_local = local_now()
    results = {}
    for auto in db.session.query(Automation).filter(Automation.is_active.is_(True)).all():
        if auto.trigger not in RUNNERS or not is_due(auto, now_local):
            continue
        try:
            results[auto.id] = run_automation(auto, now_local.date())
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("automation %s failed", auto.id)
    return results
