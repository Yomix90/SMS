"""Assistant d'import Excel/CSV : 1. fichier -> 2. mappage des colonnes + message -> 3. contrôle -> envoi."""
from flask import flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Device, Group, Import, ImportRow, Template
from ..services import imports as import_service
from ..services import importer, queue
from ..services.importer import ImportFileError
from ..services.templating import normalize_key
from . import bp, flash_error
from .helpers import form_bool, form_datetime, form_int, get_or_404, lang, page_arg
from .i18n import translate as _
from .messages import active_devices

FILE_ERRORS = {
    "empty_file": "Le fichier est vide.",
    "unsupported_format": "Format non supporté : utilisez .xlsx ou .csv.",
    "unreadable_file": "Fichier illisible ou corrompu.",
    "too_many_rows": "Trop de lignes dans le fichier.",
}


@bp.route("/imports")
def imports():
    page = db.session.query(Import).order_by(Import.created_at.desc()).paginate(page=page_arg(), per_page=20,
                                                                                error_out=False)
    return render_template("imports/list.html", page=page)


@bp.post("/imports/upload")
def import_upload():
    file = request.files.get("file")
    if not file or not file.filename:
        flash(_("Choisissez un fichier."), "danger")
        return redirect(url_for("web.imports"))
    try:
        imp, _table = import_service.create_import(file.read(), file.filename, current_user.id)
    except ImportFileError as exc:
        db.session.rollback()
        flash(_(FILE_ERRORS.get(str(exc), str(exc))), "danger")
        return redirect(url_for("web.imports"))
    db.session.commit()
    return redirect(url_for("web.import_map", uid=imp.uuid))


def _sample(imp: Import, n: int = 5) -> list[dict]:
    return [r.raw for r in imp.rows.order_by(ImportRow.row_number).limit(n)]


def _mapping_from_form(imp: Import) -> dict:
    mapping = {}
    for i, col in enumerate(imp.columns):
        choice = request.form.get(f"map_{i}", "")
        if choice == "__var__":
            choice = normalize_key(request.form.get(f"var_{i}") or col)
        mapping[col] = choice
    return mapping


@bp.route("/imports/<uid>/map", methods=["GET", "POST"])
def import_map(uid):
    imp = get_or_404(Import, uid)
    templates = db.session.query(Template).filter_by(is_active=True).order_by(Template.name).all()
    if request.method == "POST":
        mapping = _mapping_from_form(imp)
        body = request.form.get("body", "").strip()
        if importer.PHONE_FIELD not in mapping.values():
            flash(_("Indiquez la colonne qui contient le numéro de téléphone."), "danger")
            return render_template("imports/map.html", imp=imp, sample=_sample(imp), mapping=mapping, body=body,
                                   templates=templates), 422
        if list(mapping.values()).count(importer.PHONE_FIELD) > 1:
            flash(_("Une seule colonne peut être le numéro de téléphone."), "danger")
            return render_template("imports/map.html", imp=imp, sample=_sample(imp), mapping=mapping, body=body,
                                   templates=templates), 422
        report = import_service.validate_import(imp, mapping, body or None, dedupe=form_bool("dedupe"), lang=lang())
        db.session.commit()
        return _render_review(imp, report_summary=report.summary(), body=body)
    return render_template("imports/map.html", imp=imp, sample=_sample(imp), mapping=imp.mapping, body="",
                           templates=templates)


def _render_review(imp: Import, report_summary: dict | None = None, body: str = "", status: int = 200):
    if report_summary is None:
        report_summary = {"total": imp.total_rows, "valid": imp.valid_rows,
                          "invalid": imp.invalid_rows, "duplicate": imp.duplicate_rows}
    valid = imp.rows.filter(ImportRow.status == importer.ROW_VALID).order_by(ImportRow.row_number).limit(10).all()
    rejected = imp.rows.filter(ImportRow.status != importer.ROW_VALID).order_by(ImportRow.row_number).limit(100).all()
    from ..services.templating import render
    previews = [(r, render(r.custom_message or body, r.variables).text) for r in valid]
    return render_template("imports/review.html", imp=imp, summary=report_summary, previews=previews,
                           rejected=rejected, body=body, devices=active_devices(),
                           groups=db.session.query(Group).order_by(Group.name).all(),
                           variables=sorted({v for v in imp.mapping.values()
                                             if v not in (importer.PHONE_FIELD, importer.MESSAGE_FIELD, "")})), status


@bp.route("/imports/<uid>/review")
def import_review(uid):
    return _render_review(get_or_404(Import, uid))


@bp.post("/imports/<uid>/send")
def import_send(uid):
    imp = get_or_404(Import, uid)
    if imp.status == "sent":  # évite un double envoi (formulaire renvoyé)
        flash(_("Cet import a déjà été envoyé."), "warning")
        return redirect(url_for("web.campaigns"))
    body = request.form.get("body", "").strip()
    # Le texte a pu être modifié à l'étape de contrôle : nouvelle validation (rapide)
    import_service.validate_import(imp, imp.mapping, body or None, lang=lang())
    group = None
    if request.form.get("group_id", "").isdigit():
        group = db.session.get(Group, int(request.form["group_id"]))
    elif request.form.get("new_group", "").strip():
        name = request.form["new_group"].strip()[:100]
        group = db.session.query(Group).filter_by(name=name).one_or_none() or Group(name=name)
        db.session.add(group)
    device_uid = request.form.get("device_id")
    try:
        result = import_service.send_import(
            imp, body or None, request.form.get("name", "").strip() or None,
            device=get_or_404(Device, device_uid) if device_uid else None,
            sim_slot=form_int("sim_slot", None, 0, 1),
            scheduled_at=form_datetime("scheduled_at") if request.form.get("when") == "later" else None,
            add_stop_footer=form_bool("add_stop_footer"), save_contacts=form_bool("save_contacts") or group is not None,
            group=group, user=current_user)
    except queue.QueueError as exc:
        db.session.rollback()
        flash_error(exc)
        return redirect(url_for("web.import_review", uid=uid))
    db.session.commit()
    flash(_("Campagne créée : {n} messages en file.", n=result.created), "success")
    return redirect(url_for("web.campaign_detail", uid=result.campaign.uuid))


@bp.post("/imports/<uid>/contacts")
def import_save_contacts(uid):
    """Enregistrer les lignes valides comme contacts, sans envoyer de SMS."""
    imp = get_or_404(Import, uid)
    group = db.session.get(Group, int(request.form["group_id"])) if request.form.get("group_id", "").isdigit() else None
    contacts = import_service.upsert_contacts(imp, group, consent=form_bool("consent"))
    db.session.commit()
    flash(_("{n} contacts enregistrés.", n=len(contacts)), "success")
    return redirect(url_for("web.contacts"))


@bp.get("/imports/<uid>/report")
def import_report(uid):
    imp = get_or_404(Import, uid)
    if not imp.error_report_path:
        flash(_("Aucun rapport d'erreurs pour cet import."), "warning")
        return redirect(url_for("web.import_review", uid=uid))
    return send_file(imp.error_report_path, as_attachment=True,
                     download_name=f"erreurs_{imp.original_filename.rsplit('.', 1)[0]}.xlsx")


@bp.get("/imports/sample.xlsx")
def import_sample():
    """Fichier d'exemple à télécharger depuis l'assistant."""
    import io

    from ..services.sample import build_sample_xlsx
    return send_file(io.BytesIO(build_sample_xlsx()), as_attachment=True, download_name="exemple_import_sms.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
