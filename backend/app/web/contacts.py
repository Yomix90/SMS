"""Contacts, groupes, échéances, liste noire."""
import io
import json
from datetime import date

import sqlalchemy as sa
from flask import flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from openpyxl import Workbook

from ..extensions import db
from ..models import Blacklist, Contact, Group, Message, Receivable, utcnow
from ..models.contact import contact_groups
from ..services import contacts as contact_service
from ..services import queue
from ..services.audit import audit
from ..services.campaigns import nearest_unpaid
from ..services.imports import parse_amount, parse_date
from ..services.phone import format_display, normalize_phone
from ..services.templating import contact_variables, format_value
from . import bp
from .helpers import form_ids, get_or_404, page_arg
from .i18n import translate as _

CONTACT_ERRORS = {"invalid_phone": "Numéro de téléphone invalide.", "contact_exists": "Ce numéro existe déjà.",
                  "invalid_consent_status": "Statut de consentement invalide."}


def _contacts_query():
    q = db.session.query(Contact)
    a = request.args
    if a.get("q"):
        term = f"%{a['q'].strip()}%"
        digits = a["q"].replace(" ", "").lstrip("0+")
        q = q.filter(sa.or_(Contact.first_name.ilike(term), Contact.last_name.ilike(term), Contact.email.ilike(term),
                            Contact.phone.contains(digits) if digits else sa.false()))
    if a.get("group", "").isdigit():
        q = q.filter(Contact.id.in_(sa.select(contact_groups.c.contact_id)
                                    .where(contact_groups.c.group_id == int(a["group"]))))
    if a.get("consent"):
        q = q.filter(Contact.consent_status == a["consent"])
    return q


@bp.route("/contacts")
def contacts():
    q = _contacts_query()
    if request.args.get("export") == "xlsx":
        return _export_contacts(q.order_by(Contact.last_name).all())
    page = q.order_by(Contact.last_name, Contact.first_name).paginate(page=page_arg(), per_page=50, error_out=False)
    return render_template("contacts/list.html", page=page, groups=db.session.query(Group).order_by(Group.name).all())


def _export_contacts(items):
    wb = Workbook()
    ws = wb.active
    ws.title = "Contacts"
    custom_keys = sorted({k for c in items for k in (c.custom_fields or {})})
    ws.append(["Nom", "Prénom", "Téléphone", "Email", "Date de naissance", "Langue", "Groupes", "Consentement",
               *custom_keys])
    for c in items:
        ws.append([c.last_name, c.first_name, format_display(c.phone), c.email, c.birthday, c.locale,
                   ", ".join(g.name for g in c.groups), c.consent_status,
                   *[(c.custom_fields or {}).get(k) for k in custom_keys]])
    buf = io.BytesIO()
    wb.save(buf)
    audit("contacts.export", None, {"count": len(items)})
    db.session.commit()
    return send_file(io.BytesIO(buf.getvalue()), as_attachment=True, download_name="contacts.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@bp.get("/contacts/search")
def contacts_search():
    """Autocomplétion (JSON) : nom ou numéro."""
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    term = f"%{q}%"
    digits = q.replace(" ", "").lstrip("0+")
    items = (db.session.query(Contact)
             .filter(sa.or_(Contact.first_name.ilike(term), Contact.last_name.ilike(term),
                            Contact.phone.contains(digits) if digits else sa.false()))
             .order_by(Contact.last_name).limit(10).all())
    unpaid = nearest_unpaid([c.id for c in items])
    return jsonify([{
        "id": str(c.uuid), "name": c.full_name or format_display(c.phone), "phone": c.phone,
        "display": format_display(c.phone),
        "variables": {k: format_value(v) for k, v in contact_variables(c, unpaid.get(c.id)).items()},
    } for c in items])


def _contact_data_from_form() -> dict:
    f = request.form
    custom = {}
    for line in f.get("custom_fields", "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            if k.strip():
                custom[k.strip()] = v.strip()
    try:
        birthday = date.fromisoformat(f["birthday"]) if f.get("birthday") else None
    except ValueError:
        birthday = None
    return {"phone": f.get("phone"), "first_name": f.get("first_name", "").strip() or None,
            "last_name": f.get("last_name", "").strip() or None, "email": f.get("email", "").strip() or None,
            "birthday": birthday, "locale": f.get("locale") if f.get("locale") in ("fr", "ar") else "fr",
            "notes": f.get("notes", "").strip() or None, "groups": form_ids("groups"),
            "consent_status": f.get("consent_status") or None, "custom_fields": custom}


@bp.route("/contacts/new", methods=["GET", "POST"])
def contact_new():
    groups = db.session.query(Group).order_by(Group.name).all()
    if request.method == "POST":
        try:
            contact, _created = contact_service.create_contact(_contact_data_from_form(), source="formulaire")
        except contact_service.ContactError as exc:
            db.session.rollback()
            flash(_(CONTACT_ERRORS.get(exc.code, exc.code)), "danger")
            return render_template("contacts/edit.html", contact=None, form=request.form, groups=groups), 422
        db.session.commit()
        flash(_("Contact créé."), "success")
        return redirect(url_for("web.contact_detail", uid=contact.uuid))
    return render_template("contacts/edit.html", contact=None, form={"locale": "fr", "consent_status": "unknown"},
                           groups=groups)


@bp.route("/contacts/<uid>", methods=["GET", "POST"])
def contact_detail(uid):
    contact = get_or_404(Contact, uid)
    groups = db.session.query(Group).order_by(Group.name).all()
    if request.method == "POST":
        data = _contact_data_from_form()
        try:
            contact.custom_fields = {}  # remplacé intégralement par le formulaire
            contact_service.update_contact(contact, data, source="formulaire")
        except contact_service.ContactError as exc:
            db.session.rollback()
            flash(_(CONTACT_ERRORS.get(exc.code, exc.code)), "danger")
            return redirect(url_for("web.contact_detail", uid=uid))
        audit("contact.update", contact)
        db.session.commit()
        flash(_("Contact enregistré."), "success")
        return redirect(url_for("web.contact_detail", uid=uid))
    messages = (db.session.query(Message).filter_by(phone=contact.phone)
                .order_by(Message.created_at.desc()).limit(10).all())
    blacklisted = queue.is_blacklisted(contact.phone)
    custom_text = "\n".join(f"{k}={v}" for k, v in (contact.custom_fields or {}).items())
    form = {"phone": contact.phone, "first_name": contact.first_name, "last_name": contact.last_name,
            "email": contact.email, "birthday": contact.birthday.isoformat() if contact.birthday else "",
            "locale": contact.locale, "notes": contact.notes, "consent_status": contact.consent_status,
            "groups": [g.id for g in contact.groups], "custom_fields": custom_text}
    return render_template("contacts/edit.html", contact=contact, form=form, groups=groups, messages=messages,
                           blacklisted=blacklisted, today=date.today())


@bp.post("/contacts/<uid>/delete")
def contact_delete(uid):
    contact = get_or_404(Contact, uid)
    contact_service.erase_contact(contact)
    db.session.commit()
    flash(_("Contact et données associées supprimés."), "success")
    return redirect(url_for("web.contacts"))


@bp.get("/contacts/<uid>/export")
def contact_export(uid):
    contact = get_or_404(Contact, uid)
    data = contact_service.export_contact_data(contact)
    audit("contact.export_data", contact)
    db.session.commit()
    return send_file(io.BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")), as_attachment=True,
                     download_name=f"donnees_{contact.phone.lstrip('+')}.json", mimetype="application/json")


# --- Échéances ---------------------------------------------------------------------------

@bp.post("/contacts/<uid>/receivables")
def receivable_add(uid):
    contact = get_or_404(Contact, uid)
    amount = parse_amount(request.form.get("amount"))
    due = parse_date(request.form.get("due_date"))
    ref = request.form.get("reference", "").strip()[:60]
    if amount is None or due is None or not ref:
        flash(_("Montant, date d'échéance et référence sont obligatoires."), "danger")
    elif db.session.query(Receivable).filter_by(contact_id=contact.id, reference=ref).first():
        flash(_("Cette référence existe déjà pour ce contact."), "danger")
    else:
        db.session.add(Receivable(contact_id=contact.id, reference=ref, amount=amount, due_date=due, extra={}))
        db.session.commit()
        flash(_("Échéance ajoutée."), "success")
    return redirect(url_for("web.contact_detail", uid=uid) + "#receivables")


@bp.post("/receivables/<int:rid>/<action>")
def receivable_action(rid, action):
    rec = db.session.get(Receivable, rid)
    if rec is None:
        return redirect(url_for("web.contacts"))
    uid = rec.contact.uuid
    if action == "paid":
        rec.status, rec.paid_at = "paid", utcnow()
    elif action == "unpaid":
        rec.status, rec.paid_at = "unpaid", None
    elif action == "delete":
        db.session.delete(rec)
    db.session.commit()
    return redirect(url_for("web.contact_detail", uid=uid) + "#receivables")


# --- Groupes -----------------------------------------------------------------------------

@bp.route("/groups", methods=["GET", "POST"])
def groups():
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:100]
        gid = request.form.get("group_id", "")
        group = db.session.get(Group, int(gid)) if gid.isdigit() else None
        if not name:
            flash(_("Le nom du groupe est obligatoire."), "danger")
        elif db.session.query(Group).filter(Group.name == name, Group.id != (group.id if group else 0)).first():
            flash(_("Un groupe porte déjà ce nom."), "danger")
        else:
            if group is None:
                group = Group(name=name)
                db.session.add(group)
            group.name = name
            group.description = request.form.get("description", "").strip()[:255] or None
            group.color = request.form.get("color") or None
            db.session.commit()
            flash(_("Groupe enregistré."), "success")
        return redirect(url_for("web.groups"))
    counts = dict(db.session.query(contact_groups.c.group_id, sa.func.count()).group_by(contact_groups.c.group_id).all())
    return render_template("contacts/groups.html", groups=db.session.query(Group).order_by(Group.name).all(),
                           counts=counts)


@bp.post("/groups/<int:gid>/delete")
def group_delete(gid):
    group = db.session.get(Group, gid)
    if group is not None:
        db.session.delete(group)
        db.session.commit()
        flash(_("Groupe supprimé (les contacts sont conservés)."), "success")
    return redirect(url_for("web.groups"))


@bp.post("/contacts/bulk")
def contacts_bulk():
    """Actions groupées sur la sélection : ajout à un groupe, retrait, suppression."""
    ids = [i for i in request.form.getlist("ids") if len(i) == 36]
    action = request.form.get("action")
    if not ids:
        flash(_("Aucun contact sélectionné."), "warning")
        return redirect(request.referrer or url_for("web.contacts"))
    import uuid as uuidlib
    selected = db.session.query(Contact).filter(Contact.uuid.in_([uuidlib.UUID(i) for i in ids])).all()
    group = db.session.get(Group, int(request.form["group_id"])) if request.form.get("group_id", "").isdigit() else None
    if action in ("add_group", "remove_group") and group is None:
        flash(_("Choisissez un groupe."), "warning")
        return redirect(request.referrer or url_for("web.contacts"))
    for c in selected:
        if action == "add_group" and group not in c.groups:
            c.groups.append(group)
        elif action == "remove_group" and group in c.groups:
            c.groups.remove(group)
        elif action == "delete" and current_user.is_admin:
            contact_service.erase_contact(c)
    db.session.commit()
    flash(_("{n} contacts mis à jour.", n=len(selected)), "success")
    return redirect(request.referrer or url_for("web.contacts"))


# --- Liste noire -----------------------------------------------------------------------------

@bp.route("/blacklist", methods=["GET", "POST"])
def blacklist():
    if request.method == "POST":
        result = normalize_phone(request.form.get("phone", ""))
        if not result.valid:
            flash(_("Numéro de téléphone invalide."), "danger")
        else:
            queue.add_to_blacklist(result.e164, request.form.get("reason") or "manuel", user_id=current_user.id)
            contact = db.session.query(Contact).filter_by(phone=result.e164).one_or_none()
            if contact is not None:
                contact.consent_status, contact.opted_out_at = "opted_out", utcnow()
            audit("blacklist.add", None, {"phone": result.e164})
            db.session.commit()
            flash(_("Numéro ajouté à la liste noire."), "success")
        return redirect(url_for("web.blacklist"))
    q = db.session.query(Blacklist)
    if request.args.get("q"):
        q = q.filter(Blacklist.phone.contains(request.args["q"].replace(" ", "").lstrip("0+")))
    page = q.order_by(Blacklist.created_at.desc()).paginate(page=page_arg(), per_page=50, error_out=False)
    return render_template("contacts/blacklist.html", page=page)


@bp.post("/blacklist/<int:bid>/delete")
def blacklist_delete(bid):
    entry = db.session.get(Blacklist, bid)
    if entry is not None:
        audit("blacklist.remove", None, {"phone": entry.phone, "reason": entry.reason})
        db.session.delete(entry)
        db.session.commit()
        flash(_("Numéro retiré de la liste noire."), "success")
    return redirect(url_for("web.blacklist"))
