"""Campagnes : création (audience, aperçu par client réel), suivi en direct, pause / reprise / annulation."""
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Campaign, Device, Group, Message, Template
from ..services import campaigns as campaign_service
from ..services import queue
from ..services.phone import normalize_phone
from ..services.templating import extract_variables
from ..services.timeutil import local_now
from . import bp, flash_error
from .helpers import form_bool, form_datetime, form_ids, form_int, get_or_404, page_arg
from .i18n import translate as _
from .messages import _uuid, active_devices

BASE_VARIABLES = ["prenom", "nom", "nom_complet", "montant", "date_echeance", "reference", "telephone"]


def _audience_from_form() -> dict:
    return {"groups": form_ids("groups"), "exclude_groups": form_ids("exclude_groups"),
            "all": form_bool("all_contacts")}


def _manual_recipients() -> tuple[list, list[str]]:
    """Numéros collés (un par ligne, ou séparés par virgule / point-virgule)."""
    raw = request.form.get("numbers", "")
    recipients, invalid = [], []
    for token in raw.replace(";", "\n").replace(",", "\n").splitlines():
        token = token.strip()
        if not token:
            continue
        if normalize_phone(token).valid:
            recipients.append(campaign_service.Recipient(token))
        else:
            invalid.append(token)
    return recipients, invalid


def _body_and_template():
    template = None
    if request.form.get("template_id"):
        template = get_or_404(Template, request.form["template_id"])
    body = request.form.get("body", "").strip() or (template.body if template else "")
    return body, template


def _form_context(form=None):
    return {"groups": db.session.query(Group).order_by(Group.name).all(),
            "templates": db.session.query(Template).filter_by(is_active=True).order_by(Template.name).all(),
            "devices": active_devices(), "form": form or {}, "variables": BASE_VARIABLES}


@bp.route("/campaigns")
def campaigns():
    q = db.session.query(Campaign)
    if request.args.get("status"):
        q = q.filter(Campaign.status == request.args["status"])
    page = q.order_by(Campaign.created_at.desc()).paginate(page=page_arg(), per_page=25, error_out=False)
    return render_template("campaigns/list.html", page=page)


@bp.route("/campaigns/new", methods=["GET", "POST"])
def campaign_new():
    if request.method == "POST":
        body, template = _body_and_template()
        manual, invalid = _manual_recipients()
        audience = _audience_from_form()
        device_uid = request.form.get("device_id")
        try:
            result = campaign_service.create_campaign(
                request.form.get("name", "").strip() or _("Campagne du {d}", d=f"{local_now():%d/%m/%Y %H:%M}"), body=body, template=template,
                recipients=manual, audience=audience if (audience["groups"] or audience["all"]) else None,
                device=get_or_404(Device, device_uid) if device_uid else None,
                sim_slot=form_int("sim_slot", None, 0, 1),
                scheduled_at=form_datetime("scheduled_at") if request.form.get("when") == "later" else None,
                add_stop_footer=form_bool("add_stop_footer"), source="manual", user=current_user)
        except queue.QueueError as exc:
            db.session.rollback()
            flash_error(exc)
            return render_template("campaigns/new.html", **_form_context(request.form)), 422
        db.session.commit()
        msg = _("Campagne créée : {n} messages en file.", n=result.created)
        if result.rejected or invalid:
            msg += " " + _("{n} numéros ignorés (invalides).", n=len(result.rejected) + len(invalid))
        flash(msg, "success")
        return redirect(url_for("web.campaign_detail", uid=result.campaign.uuid))
    form = {}
    if request.args.get("template"):
        tpl = db.session.query(Template).filter_by(uuid=_uuid(request.args["template"])).one_or_none() \
            if len(request.args["template"]) == 36 else None
        form = {"body": tpl.body, "template_id": str(tpl.uuid)} if tpl else {}
    if request.args.get("group", "").isdigit():
        form["groups"] = [int(request.args["group"])]
    return render_template("campaigns/new.html", **_form_context(form))


@bp.post("/campaigns/preview")
def campaign_preview():
    """Aperçu AJAX : nombre de destinataires et rendu du message pour un client réel."""
    body, _template = _body_and_template()
    audience = _audience_from_form()
    recipients = campaign_service.audience_recipients(audience) if (audience["groups"] or audience["all"]) else []
    manual, invalid = _manual_recipients()
    recipients = recipients + manual
    index = min(form_int("index", 0, 0), max(len(recipients) - 1, 0))
    data = {"count": len(recipients), "invalid": invalid[:20], "index": index,
            "variables": sorted(set(BASE_VARIABLES) | set(extract_variables(body)))}
    if recipients and body:
        r = recipients[index]
        data["recipient"] = {"phone": r.phone, "name": r.contact.full_name if r.contact else ""}
        data.update(campaign_service.preview(body, r, form_bool("add_stop_footer")))
    return jsonify(data)


@bp.route("/campaigns/<uid>")
def campaign_detail(uid):
    campaign = get_or_404(Campaign, uid)
    q = db.session.query(Message).filter_by(campaign_id=campaign.id)
    if request.args.get("status"):
        q = q.filter(Message.status == request.args["status"])
    page = q.order_by(Message.id).paginate(page=page_arg(), per_page=50, error_out=False)
    return render_template("campaigns/detail.html", c=campaign, page=page)


@bp.post("/campaigns/<uid>/<action>")
def campaign_action(uid, action):
    campaign = get_or_404(Campaign, uid)
    handlers = {"pause": campaign_service.pause_campaign, "resume": campaign_service.resume_campaign,
                "cancel": campaign_service.cancel_campaign, "retry": campaign_service.retry_failed}
    if action not in handlers:
        return redirect(url_for("web.campaign_detail", uid=uid))
    try:
        n = handlers[action](campaign)
    except queue.QueueError as exc:
        flash_error(exc)
        return redirect(url_for("web.campaign_detail", uid=uid))
    db.session.commit()
    labels = {"pause": _("Campagne en pause."), "resume": _("Campagne reprise."),
              "cancel": _("Campagne annulée."), "retry": _("{n} messages échoués relancés.", n=n)}
    flash(labels[action], "success")
    return redirect(url_for("web.campaign_detail", uid=uid))
