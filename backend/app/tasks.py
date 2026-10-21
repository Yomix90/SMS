"""Jobs planifiés (APScheduler) exécutés dans le process web.

Chaque job tourne dans un contexte applicatif et prend un verrou en base
(scheduler_locks) : si plusieurs process démarrent un scheduler, un seul exécute le job.
"""
import logging
import os
import socket
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.exc import IntegrityError

from .extensions import db
from .models import SchedulerLock, utcnow

log = logging.getLogger(__name__)

_OWNER = f"{socket.gethostname()}:{os.getpid()}"
scheduler: BackgroundScheduler | None = None


def acquire_lock(name: str, ttl_sec: int) -> bool:
    now = utcnow()
    until = now + timedelta(seconds=ttl_sec)
    updated = (db.session.query(SchedulerLock)
               .filter(SchedulerLock.name == name,
                       (SchedulerLock.locked_until < now) | (SchedulerLock.owner == _OWNER))
               .update({SchedulerLock.locked_until: until, SchedulerLock.owner: _OWNER},
                       synchronize_session=False))
    if updated:
        db.session.commit()
        return True
    if db.session.get(SchedulerLock, name) is not None:
        db.session.rollback()
        return False
    try:
        db.session.add(SchedulerLock(name=name, locked_until=until, owner=_OWNER))
        db.session.commit()
        return True
    except IntegrityError:
        db.session.rollback()
        return False


def _job(app, name: str, ttl_sec: int, fn):
    def run():
        with app.app_context():
            try:
                if not acquire_lock(name, ttl_sec):
                    return
                result = fn()
                db.session.commit()
                if result:
                    log.debug("job %s: %s", name, result)
            except Exception:
                db.session.rollback()
                log.exception("job %s failed", name)
            finally:
                db.session.remove()
    run.__name__ = f"job_{name}"
    return run


def job_definitions():
    from .services import automations, devices, dispatcher, imports, schedules, webhooks
    return [
        # (nom, intervalle en secondes, fonction)
        ("dispatcher", 5, dispatcher.tick),
        ("offline_devices", 30, devices.mark_offline_devices),
        ("schedules", 30, schedules.run_due_schedules),
        ("automations", 300, automations.run_due_automations),
        ("webhooks", 15, webhooks.deliver_pending),
        ("purge_imports", 3600 * 6, imports.purge_old_imports),
    ]


def start_scheduler(app) -> BackgroundScheduler:
    global scheduler
    if scheduler is not None:
        return scheduler
    scheduler = BackgroundScheduler(timezone=app.config["TIMEZONE"],
                                    job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30})
    for name, interval, fn in job_definitions():
        scheduler.add_job(_job(app, name, max(interval * 2, 30), fn), "interval", seconds=interval, id=name,
                          replace_existing=True)
    scheduler.start()
    log.info("scheduler started (%s)", _OWNER)
    return scheduler


def run_job_now(name: str):
    """Exécution manuelle (CLI / tests)."""
    for job_name, _interval, fn in job_definitions():
        if job_name == name:
            result = fn()
            db.session.commit()
            return result
    raise KeyError(name)
