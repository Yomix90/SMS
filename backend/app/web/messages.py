"""Envoi d'un SMS unique, historique filtrable, détail et exports."""
import io

from flask import flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Campaign, Contact, Device, Message, Template
from ..services import queue
from ..services.reports import MessageFilters, export_filename, export_pdf, export_xlsx, filtered_messages
from ..services.templating import contact_variables, render
from . import bp, flash_error
from .helpers import form_datetime, form_int, get_or_404, lang, page_arg, status_badge
from .i18n import translate as _


def active_devices():
    return db.session.query(Device).filter(Device.revoked_at.is_(None), Device.is_enabled.is_(True)) \
        .order_by(Device.name).all()


def _device_from_form():
    uid = request.form.get("device_id")
    return get_or_404(Device, uid) if uid else None


@bp.route("/send", methods=["GET", "POST"])
def send():
    form = request.form
    if request.method == "POST":
        body = form.get("body", "")
        contact = db.session.query(Contact).filter_by(uuid=_uuid(form["contact_id"])).one_or_none() \
            if form.get("contact_id") else None
        if contact is None:  # numéro saisi à la main : contact existant ?
            from ..services.phone import normalize_phone
            e164 = normalize_phone(form.get("phone", "")).e164
            contact = db.session.query(Contact).filter_by(phone=e164).one_or_none() if e164 else None
        if "{" in body:  # variables du contact (vides si numéro inconnu)
            from ..services.campaigns import nearest_unpaid
            receivable = nearest_unpaid([contact.id]).get(contact.id) if contact else None
            body = render(body, contact_variables(contact, receivable)).text
        try:
            queue.check_abuse(1, user=current_user)
            msg = queue.enqueue(form.get("phone", ""), body, contact=contact, device=_device_from_form(),
                                sim_slot=form_int("sim_slot", None, 0, 1), scheduled_at=form_datetime("scheduled_at"),
                                priority=5, source="web", user_id=current_user.id)
        except queue.QueueError as exc:
            db.session.rollback()
            flash_error(exc)
            return render_template("messages/send.html", devices=active_devices(), form=form,
                                   templates=_templates()), 422
        db.session.commit()
        flash(_("SMS mis en file d'envoi."), "success")
        return redirect(url_for("web.message_detail", uid=msg.uuid))
    prefill = {"phone": request.args.get("phone", ""), "body": request.args.get("body", "")}
    return render_template("messages/send.html", devices=active_devices(), form=prefill, templates=_templates())


def _templates():
    return db.session.query(Template).filter_by(is_active=True).order_by(Template.category, Template.name).all()


def _filters() -> MessageFilters:
    from datetime import date
    a = request.args

    def _date(key):
        try:
            return date.fromisoformat(a.get(key, ""))
        except ValueError:
            return None
    f = MessageFilters(date_from=_date("from"), date_to=_date("to"), status=a.get("status") or None,
                       direction=a.get("direction") or None, phone=a.get("phone") or None,
                       search=a.get("q") or None, source=a.get("source") or None)
    if a.get("device"):
        device = db.session.query(Device).filter_by(uuid=_uuid(a["device"])).one_or_none()
        f.device_id = device.id if device else -1
    if a.get("campaign"):
        campaign = db.session.query(Campaign).filter_by(uuid=_uuid(a["campaign"])).one_or_none()
        f.campaign_id = campaign.id if campaign else -1
    return f


def _uuid(value):
    import uuid
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


@bp.route("/messages")
def messages():
    f = _filters()
    export = request.args.get("export")
    if export == "xlsx":
        return send_file(io.BytesIO(export_xlsx(f, lang())), as_attachment=True, download_name=export_filename("xlsx"),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if export == "pdf":
        return send_file(io.BytesIO(export_pdf(f, lang())), as_attachment=True, download_name=export_filename("pdf"),
                         mimetype="application/pdf")
    page = filtered_messages(f).order_by(Message.created_at.desc()).paginate(page=page_arg(), per_page=50,
                                                                             error_out=False)
    devices = db.session.query(Device).order_by(Device.name).all()
    campaigns = db.session.query(Campaign).order_by(Campaign.created_at.desc()).limit(100).all()
    return render_template("messages/list.html", page=page, devices=devices, campaigns=campaigns,
                           device_names={d.id: d.name for d in devices})


@bp.route("/messages/<uid>")
def message_detail(uid):
    msg = get_or_404(Message, uid)
    return render_template("messages/detail.html", m=msg)


@bp.get("/messages/<uid>/badge")
def message_badge(uid):
    return str(status_badge(get_or_404(Message, uid).status))


@bp.post("/messages/<uid>/cancel")
def message_cancel(uid):
    msg = get_or_404(Message, uid)
    if queue.cancel_message(msg):
        db.session.commit()
        flash(_("Message annulé."), "success")
    else:
        flash(_("Ce message ne peut plus être annulé."), "warning")
    return redirect(url_for("web.message_detail", uid=uid))


@bp.post("/messages/<uid>/retry")
def message_retry(uid):
    msg = get_or_404(Message, uid)
    if queue.retry_message(msg):
        db.session.commit()
        flash(_("Message remis en file d'envoi."), "success")
    else:
        flash(_("Ce message ne peut pas être relancé."), "warning")
    return redirect(url_for("web.message_detail", uid=uid))
