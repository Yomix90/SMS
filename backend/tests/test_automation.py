from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from app.models import Automation, Contact, Group, Message, Receivable, Schedule, Template
from app.services import automations, schedules
from app.services.timeutil import SendWindow, local_to_utc, local_today


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# --- Fenêtre horaire --------------------------------------------------------------------

def test_send_window_day(app):
    w = SendWindow("08:00", "21:00")
    # Casablanca = UTC+1 (heure légale marocaine hors ramadan)
    assert w.contains(local_to_utc(date(2026, 10, 20), time(8, 0)))
    assert not w.contains(local_to_utc(date(2026, 10, 20), time(21, 0)))
    assert not w.contains(local_to_utc(date(2026, 10, 20), time(2, 0)))
    nxt = w.next_open(local_to_utc(date(2026, 10, 20), time(22, 0)))
    assert nxt == local_to_utc(date(2026, 10, 21), time(8, 0))


def test_send_window_days_and_overnight(app):
    w = SendWindow("09:00", "18:00", days=[1, 2, 3, 4, 5])  # lundi-vendredi
    saturday = local_to_utc(date(2026, 10, 24), time(10, 0))
    assert not w.contains(saturday)
    assert w.next_open(saturday) == local_to_utc(date(2026, 10, 26), time(9, 0))
    night = SendWindow("22:00", "02:00")
    assert night.contains(local_to_utc(date(2026, 10, 20), time(23, 30)))
    assert night.contains(local_to_utc(date(2026, 10, 21), time(1, 30)))
    assert not night.contains(local_to_utc(date(2026, 10, 21), time(12, 0)))


# --- Planifications ---------------------------------------------------------------------

def _schedule(**kw):
    base = dict(name="S", body="x", audience={}, recurrence="daily", run_time=time(9, 0), days_of_week=[])
    base.update(kw)
    return Schedule(**base)


def test_next_run_daily(app):
    s = _schedule()
    after = local_to_utc(date(2026, 10, 20), time(10, 0))
    assert schedules.compute_next_run(s, after) == local_to_utc(date(2026, 10, 21), time(9, 0))
    after = local_to_utc(date(2026, 10, 20), time(8, 0))
    assert schedules.compute_next_run(s, after) == local_to_utc(date(2026, 10, 20), time(9, 0))


def test_next_run_weekly_monthly_once(app):
    after = local_to_utc(date(2026, 10, 20), time(10, 0))  # mardi
    s = _schedule(recurrence="weekly", days_of_week=[1, 5])  # lundi, vendredi
    assert schedules.compute_next_run(s, after) == local_to_utc(date(2026, 10, 23), time(9, 0))
    s = _schedule(recurrence="monthly", day_of_month=31)
    assert schedules.compute_next_run(s, after) == local_to_utc(date(2026, 10, 31), time(9, 0))
    nov = local_to_utc(date(2026, 11, 1), time(0, 0))
    assert schedules.compute_next_run(s, nov) == local_to_utc(date(2026, 11, 30), time(9, 0))
    s = _schedule(recurrence="once", run_date=date(2026, 10, 19))
    assert schedules.compute_next_run(s, after) is None
    s = _schedule(end_date=date(2026, 10, 20))
    assert schedules.compute_next_run(s, after) is None


def test_run_due_schedule_creates_campaign(db):
    g = Group(name="G")
    c = Contact(phone="+212612345678", first_name="Salma")
    c.groups.append(g)
    db.session.add_all([g, c])
    db.session.flush()
    s = _schedule(body="Bonjour {prenom}", audience={"groups": [g.id]}, recurrence="once",
                  run_date=local_today(), next_run_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    db.session.add(s)
    db.session.commit()
    assert schedules.run_due_schedules() == 1
    msg = db.session.query(Message).one()
    assert msg.body == "Bonjour Salma" and msg.source == "schedule"
    assert s.is_active is False and s.last_campaign_id is not None


# --- Automatisations ----------------------------------------------------------------------

def _setup_receivable(db, due_in_days: int, locale="fr"):
    c = Contact(phone="+212612345678", first_name="Salma", locale=locale)
    db.session.add(c)
    db.session.flush()
    db.session.add(Receivable(contact_id=c.id, reference="FAC-9", amount=Decimal("500"),
                              due_date=local_today() + timedelta(days=due_in_days), extra={}))
    fr = Template(name="R", language="fr", body="Rappel {reference} : {montant} MAD le {date_echeance}")
    ar = Template(name="R", language="ar", body="تذكير {reference}")
    db.session.add_all([fr, ar])
    db.session.flush()
    return c, fr, ar


def test_due_reminder_sent_once(db):
    _c, fr, ar = _setup_receivable(db, 3)
    auto = Automation(name="J-3", trigger="due_reminder", config={"days_before": [3, 1]},
                      template_fr_id=fr.id, template_ar_id=ar.id, run_time=time(0, 0))
    db.session.add(auto)
    db.session.commit()
    assert automations.run_automation(auto) == 1
    db.session.commit()
    msg = db.session.query(Message).one()
    assert msg.body.startswith("Rappel FAC-9 : 500 MAD le ")
    assert automations.run_automation(auto) == 0  # dédoublonnage


def test_due_reminder_uses_arabic_template(db):
    _c, fr, ar = _setup_receivable(db, 1, locale="ar")
    auto = Automation(name="J-1", trigger="due_reminder", config={"days_before": [1]},
                      template_fr_id=fr.id, template_ar_id=ar.id)
    db.session.add(auto)
    db.session.commit()
    automations.run_automation(auto)
    assert db.session.query(Message).one().body == "تذكير FAC-9"


def test_unpaid_followup_and_paid_skip(db):
    _c, fr, _ar = _setup_receivable(db, -7)
    auto = Automation(name="Relance", trigger="unpaid_followup", config={"days_after": [1, 7], "max": 3},
                      template_fr_id=fr.id)
    db.session.add(auto)
    db.session.commit()
    assert automations.run_automation(auto) == 1
    rec = db.session.query(Receivable).one()
    rec.status = "paid"
    db.session.commit()
    auto.last_run_at = None
    assert automations.run_automation(auto) == 0


def test_birthday_and_opt_out(db):
    today = local_today()
    tpl = Template(name="B", language="fr", body="Joyeux anniversaire {prenom}")
    db.session.add_all([
        tpl,
        Contact(phone="+212612345678", first_name="A", birthday=date(1990, today.month, today.day)),
        Contact(phone="+212700112233", first_name="B", birthday=date(1990, today.month, today.day),
                consent_status="opted_out"),
    ])
    db.session.flush()
    auto = Automation(name="Anniv", trigger="birthday", template_fr_id=tpl.id)
    db.session.add(auto)
    db.session.commit()
    assert automations.run_automation(auto) == 1


def test_leap_day_birthday():
    assert automations._birthday_matches(date(2000, 2, 29), date(2027, 2, 28))
    assert not automations._birthday_matches(date(2000, 2, 29), date(2028, 2, 28))


def test_is_due(db):
    auto = Automation(name="x", trigger="birthday", run_time=time(10, 0))
    from app.services.timeutil import tz
    assert not automations.is_due(auto, datetime(2026, 10, 20, 9, 0, tzinfo=tz()))
    assert automations.is_due(auto, datetime(2026, 10, 20, 10, 5, tzinfo=tz()))
    auto.last_run_at = local_to_utc(date(2026, 10, 20), time(10, 1))
    assert not automations.is_due(auto, datetime(2026, 10, 20, 15, 0, tzinfo=tz()))
