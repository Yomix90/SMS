"""Envois automatiques : planifications, déclencheurs (échéance, impayés, anniversaire, bienvenue), mots-clés."""
from datetime import date, time

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Automation, AutomationRun, Device, Group, KeywordRule, Schedule, Template
from ..models.automation import RECURRENCES, TRIGGERS
from ..services import automations as automation_service
from ..services.audit import audit
from ..services.schedules import compute_next_run
from . import bp
from .helpers import form_bool, form_ids, form_int, get_or_404
from .i18n import translate as _
from .messages import active_devices

TRIGGER_LABELS = {"due_reminder": "Rappel avant échéance", "unpaid_followup": "Relance des impayés",
                  "birthday": "Anniversaire", "welcome": "Message de bienvenue"}
RECURRENCE_LABELS = {"once": "Une seule fois", "daily": "Tous les jours", "weekly": "Chaque semaine",
                     "monthly": "Chaque mois"}
DAYS = [(1, "Lun"), (2, "Mar"), (3, "Mer"), (4, "Jeu"), (5, "Ven"), (6, "Sam"), (7, "Dim")]
MATCH_LABELS = {"exact": "Message exact", "starts_with": "Commence par", "contains": "Contient", "regex": "Expression régulière"}
ACTION_LABELS = {"reply": "Répondre", "opt_out": "Désinscrire (STOP)", "opt_in": "Réinscrire",
                 "label": "Ajouter une étiquette", "add_group": "Ajouter à un groupe"}


def _parse_time(value: str, default: time) -> time:
    try:
        h, m = (value or "").split(":")[:2]
        return time(int(h), int(m))
    except (ValueError, TypeError):
        return default


def _parse_date(value: str):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _int_list(value: str) -> list[int]:
    return sorted({int(x) for x in (value or "").replace(";", ",").split(",") if x.strip().lstrip("-").isdigit()})


def _ctx():
    return {"templates": db.session.query(Template).filter_by(is_active=True).order_by(Template.name).all(),
            "groups": db.session.query(Group).order_by(Group.name).all(), "devices": active_devices(),
            "trigger_labels": TRIGGER_LABELS, "recurrence_labels": RECURRENCE_LABELS, "days": DAYS,
            "match_labels": MATCH_LABELS, "action_labels": ACTION_LABELS}


@bp.route("/automations")
def automations():
    tab = request.args.get("tab", "schedules")
    schedules = db.session.query(Schedule).order_by(Schedule.is_active.desc(), Schedule.next_run_at).all()
    autos = db.session.query(Automation).order_by(Automation.name).all()
    run_counts = {a.id: db.session.query(AutomationRun).filter_by(automation_id=a.id).count() for a in autos}
    rules = db.session.query(KeywordRule).order_by(KeywordRule.priority.desc(), KeywordRule.id).all()
    return render_template("automations/index.html", tab=tab, schedules=schedules, autos=autos,
                           run_counts=run_counts, rules=rules, **_ctx())


@bp.get("/schedules")
def schedules_redirect():
    return redirect(url_for("web.automations", tab="schedules"))


# --- Planifications ------------------------------------------------------------------

@bp.route("/schedules/new", methods=["GET", "POST"], defaults={"uid": None})
@bp.route("/schedules/<uid>/edit", methods=["GET", "POST"])
def schedule_edit(uid):
    s = get_or_404(Schedule, uid) if uid else None
    if request.method == "POST":
        f = request.form
        s = s or Schedule(created_by=current_user.id)
        s.name = f.get("name", "").strip()[:150] or _("Planification")
        s.body = f.get("body", "").strip() or None
        s.template_id = get_or_404(Template, f["template_id"]).id if f.get("template_id") else None
        s.audience = {"groups": form_ids("groups"), "exclude_groups": form_ids("exclude_groups"),
                      "all": form_bool("all_contacts")}
        s.device_id = get_or_404(Device, f["device_id"]).id if f.get("device_id") else None
        s.sim_slot = form_int("sim_slot", None, 0, 1)
        s.add_stop_footer = form_bool("add_stop_footer")
        s.recurrence = f.get("recurrence") if f.get("recurrence") in RECURRENCES else "once"
        s.run_time = _parse_time(f.get("run_time"), time(9, 0))
        s.run_date = _parse_date(f.get("run_date"))
        s.days_of_week = form_ids("days_of_week")
        s.day_of_month = form_int("day_of_month", None, 1, 31)
        s.start_date, s.end_date = _parse_date(f.get("start_date")), _parse_date(f.get("end_date"))
        s.is_active = form_bool("is_active") if uid else True
        errors = []
        if not s.body and not s.template_id:
            errors.append(_("Saisissez un texte ou choisissez un modèle."))
        if not (s.audience["groups"] or s.audience["all"]):
            errors.append(_("Choisissez au moins un groupe de destinataires."))
        if s.recurrence == "once" and not s.run_date:
            errors.append(_("Indiquez la date d'envoi."))
        if s.recurrence == "weekly" and not s.days_of_week:
            errors.append(_("Choisissez au moins un jour de la semaine."))
        s.next_run_at = compute_next_run(s) if s.is_active else None
        if s.is_active and s.next_run_at is None and not errors:
            errors.append(_("Aucune date d'envoi future avec ces paramètres."))
        if errors:
            db.session.rollback()
            for e in errors:
                flash(e, "danger")
            return render_template("automations/schedule_edit.html", s=s, **_ctx()), 422
        db.session.add(s)
        db.session.flush()
        audit("schedule.save", s, {"recurrence": s.recurrence})
        db.session.commit()
        flash(_("Planification enregistrée."), "success")
        return redirect(url_for("web.automations", tab="schedules"))
    return render_template("automations/schedule_edit.html", s=s, **_ctx())


@bp.post("/schedules/<uid>/<action>")
def schedule_action(uid, action):
    s = get_or_404(Schedule, uid)
    if action == "toggle":
        s.is_active = not s.is_active
        s.next_run_at = compute_next_run(s) if s.is_active else None
        if s.is_active and s.next_run_at is None:
            s.is_active = False
            flash(_("Aucune date d'envoi future avec ces paramètres."), "warning")
    elif action == "delete":
        db.session.delete(s)
    db.session.commit()
    return redirect(url_for("web.automations", tab="schedules"))


# --- Déclencheurs -----------------------------------------------------------------------

@bp.route("/automations/new", methods=["GET", "POST"], defaults={"aid": None})
@bp.route("/automations/<int:aid>/edit", methods=["GET", "POST"])
def automation_edit(aid):
    auto = db.session.get(Automation, aid) if aid else None
    if request.method == "POST":
        f = request.form
        auto = auto or Automation(created_by=current_user.id)
        auto.name = f.get("name", "").strip()[:150] or _(TRIGGER_LABELS.get(f.get("trigger"), "Automatisation"))
        auto.trigger = f.get("trigger") if f.get("trigger") in TRIGGERS else "due_reminder"
        auto.template_fr_id = get_or_404(Template, f["template_fr_id"]).id if f.get("template_fr_id") else None
        auto.template_ar_id = get_or_404(Template, f["template_ar_id"]).id if f.get("template_ar_id") else None
        auto.run_time = _parse_time(f.get("run_time"), time(10, 0))
        auto.audience = {"groups": form_ids("groups")}
        auto.is_active = form_bool("is_active")
        if auto.trigger == "due_reminder":
            auto.config = {"days_before": _int_list(f.get("days_before")) or [3]}
        elif auto.trigger == "unpaid_followup":
            auto.config = {"days_after": _int_list(f.get("days_after")) or [1, 7, 15],
                           "max": form_int("max", 3, 1, 20)}
        elif auto.trigger == "welcome":
            auto.config = {"group_id": form_int("welcome_group", None)}
        else:
            auto.config = {}
        if not auto.template_fr_id and not auto.template_ar_id:
            db.session.rollback()
            flash(_("Choisissez au moins un modèle (FR ou AR)."), "danger")
            return render_template("automations/automation_edit.html", auto=auto, **_ctx()), 422
        db.session.add(auto)
        db.session.flush()
        audit("automation.save", auto, {"trigger": auto.trigger})
        db.session.commit()
        flash(_("Automatisation enregistrée."), "success")
        return redirect(url_for("web.automations", tab="triggers"))
    return render_template("automations/automation_edit.html", auto=auto, **_ctx())


@bp.post("/automations/<int:aid>/<action>")
def automation_action(aid, action):
    auto = db.session.get(Automation, aid)
    if auto is None:
        return redirect(url_for("web.automations", tab="triggers"))
    if action == "toggle":
        auto.is_active = not auto.is_active
    elif action == "run":
        n = automation_service.run_automation(auto)
        flash(_("{n} messages créés.", n=n), "success")
    elif action == "delete":
        db.session.delete(auto)
    db.session.commit()
    return redirect(url_for("web.automations", tab="triggers"))


# --- Réponses automatiques par mots-clés --------------------------------------------------

@bp.route("/keywords/new", methods=["GET", "POST"], defaults={"rid": None})
@bp.route("/keywords/<int:rid>/edit", methods=["GET", "POST"])
def keyword_edit(rid):
    rule = db.session.get(KeywordRule, rid) if rid else None
    if request.method == "POST":
        f = request.form
        rule = rule or KeywordRule()
        rule.keywords = [k.strip() for k in f.get("keywords", "").replace("\n", ",").split(",") if k.strip()][:20]
        rule.match_type = f.get("match_type") if f.get("match_type") in MATCH_LABELS else "exact"
        rule.action = f.get("action") if f.get("action") in ACTION_LABELS else "reply"
        rule.reply_body = f.get("reply_body", "").strip() or None
        rule.action_value = f.get("action_value", "").strip()[:100] or None
        rule.priority = form_int("priority", 0, -100, 100)
        rule.cooldown_minutes = form_int("cooldown_minutes", 60, 0, 10080)
        rule.is_active = form_bool("is_active")
        if rule.match_type == "regex":
            import re
            try:
                for k in rule.keywords:
                    re.compile(k)
            except re.error:
                db.session.rollback()
                flash(_("Expression régulière invalide."), "danger")
                return render_template("automations/keyword_edit.html", rule=rule, **_ctx()), 422
        if not rule.keywords or (rule.action == "reply" and not rule.reply_body):
            db.session.rollback()
            flash(_("Indiquez au moins un mot-clé et le texte de la réponse."), "danger")
            return render_template("automations/keyword_edit.html", rule=rule, **_ctx()), 422
        db.session.add(rule)
        db.session.commit()
        flash(_("Règle enregistrée."), "success")
        return redirect(url_for("web.automations", tab="keywords"))
    return render_template("automations/keyword_edit.html", rule=rule, **_ctx())


@bp.post("/keywords/<int:rid>/<action>")
def keyword_action(rid, action):
    rule = db.session.get(KeywordRule, rid)
    if rule is not None:
        if action == "toggle":
            rule.is_active = not rule.is_active
        elif action == "delete":
            db.session.delete(rule)
        db.session.commit()
    return redirect(url_for("web.automations", tab="keywords"))
