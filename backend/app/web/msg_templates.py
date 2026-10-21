"""Modèles de messages réutilisables (FR/AR, par catégorie)."""
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Template
from ..services.audit import audit
from ..services.templating import extract_variables, render, sample_variables
from . import bp
from .helpers import form_bool, get_or_404
from .i18n import translate as _

CATEGORIES = ["general", "rappel", "relance", "promo", "bienvenue", "fidelite", "transactionnel"]
CATEGORY_LABELS = {"general": "Général", "rappel": "Rappel", "relance": "Relance", "promo": "Promotion",
                   "bienvenue": "Bienvenue", "fidelite": "Fidélité", "transactionnel": "Transactionnel"}


@bp.route("/templates")
def templates_list():
    q = db.session.query(Template)
    if request.args.get("category"):
        q = q.filter(Template.category == request.args["category"])
    if request.args.get("language"):
        q = q.filter(Template.language == request.args["language"])
    items = q.order_by(Template.category, Template.name, Template.language).all()
    return render_template("templates_/list.html", items=items, categories=CATEGORIES, labels=CATEGORY_LABELS)


@bp.route("/templates/new", methods=["GET", "POST"], defaults={"uid": None})
@bp.route("/templates/<uid>/edit", methods=["GET", "POST"])
def template_edit(uid):
    tpl = get_or_404(Template, uid) if uid else None
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:120]
        body = request.form.get("body", "").strip()
        language = request.form.get("language") if request.form.get("language") in ("fr", "ar") else "fr"
        category = request.form.get("category") if request.form.get("category") in CATEGORIES else "general"
        if not name or not body:
            flash(_("Le nom et le texte sont obligatoires."), "danger")
            return render_template("templates_/edit.html", tpl=tpl, form=request.form, categories=CATEGORIES,
                                   labels=CATEGORY_LABELS), 422
        duplicate = db.session.query(Template).filter_by(name=name, language=language).first()
        if duplicate is not None and duplicate is not tpl:
            flash(_("Un modèle porte déjà ce nom dans cette langue."), "danger")
            return render_template("templates_/edit.html", tpl=tpl, form=request.form, categories=CATEGORIES,
                                   labels=CATEGORY_LABELS), 422
        if tpl is None:
            tpl = Template(created_by=current_user.id)
            db.session.add(tpl)
        tpl.name, tpl.body, tpl.language, tpl.category = name, body, language, category
        tpl.variables = extract_variables(body)
        tpl.is_active = form_bool("is_active") if uid else True
        db.session.flush()
        audit("template.save", tpl, {"name": name})
        db.session.commit()
        flash(_("Modèle enregistré."), "success")
        return redirect(url_for("web.templates_list"))
    form = {"name": tpl.name, "body": tpl.body, "language": tpl.language, "category": tpl.category,
            "is_active": tpl.is_active} if tpl else {"language": request.args.get("language", "fr"), "is_active": True}
    return render_template("templates_/edit.html", tpl=tpl, form=form, categories=CATEGORIES, labels=CATEGORY_LABELS)


@bp.post("/templates/<uid>/delete")
def template_delete(uid):
    tpl = get_or_404(Template, uid)
    audit("template.delete", tpl, {"name": tpl.name})
    db.session.delete(tpl)
    db.session.commit()
    flash(_("Modèle supprimé."), "success")
    return redirect(url_for("web.templates_list"))


@bp.post("/templates/preview")
def template_preview():
    res = render(request.form.get("body", ""), sample_variables())
    return jsonify({"text": res.text, "missing": res.missing})
