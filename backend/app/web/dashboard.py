"""Tableau de bord et notifications."""
import sqlalchemy as sa
from flask import redirect, render_template, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Campaign, Device, Message, Notification
from ..models.campaign import CAMPAIGN_RUNNING, CAMPAIGN_SCHEDULED
from ..models.message import PENDING_STATUSES
from ..services import devices as device_service
from ..services.reports import MessageFilters, daily_series, summary
from ..services.timeutil import local_today
from . import bp
from .helpers import page_arg


@bp.route("/")
def dashboard():
    today = local_today()
    stats = summary(MessageFilters(date_from=today, date_to=today))
    pending = db.session.query(Message).filter(Message.status.in_(PENDING_STATUSES)).count()
    devices = db.session.query(Device).filter(Device.revoked_at.is_(None)).order_by(Device.name).all()
    usage = {d.id: device_service.usage(d) for d in devices}
    running = (db.session.query(Campaign).filter(Campaign.status.in_((CAMPAIGN_RUNNING, CAMPAIGN_SCHEDULED)))
               .order_by(Campaign.created_at.desc()).limit(5).all())
    recent = db.session.query(Message).order_by(Message.created_at.desc()).limit(8).all()
    return render_template("dashboard.html", stats=stats, pending=pending, devices=devices, usage=usage,
                           running=running, recent=recent, chart=daily_series(14))


def _notifications_query():
    q = db.session.query(Notification)
    cond = Notification.user_id == current_user.id
    if current_user.is_admin:
        cond = sa.or_(cond, Notification.user_id.is_(None))
    return q.filter(cond)


@bp.route("/notifications")
def notifications():
    page = _notifications_query().order_by(Notification.created_at.desc()).paginate(
        page=page_arg(), per_page=30, error_out=False)
    return render_template("notifications.html", page=page)


@bp.post("/notifications/read")
def notifications_read():
    _notifications_query().filter(Notification.is_read.is_(False)).update(
        {Notification.is_read: True}, synchronize_session=False)
    db.session.commit()
    return redirect(url_for("web.notifications"))
