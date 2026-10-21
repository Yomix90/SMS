"""Administration : utilisateurs, clés API, webhooks, paramètres, journal d'audit."""
from datetime import datetime, timezone

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import ApiKey, AuditLog, User, Webhook, WebhookDelivery
from ..models.webhook import WEBHOOK_EVENTS
from ..services import accounts, webhooks
from ..services.audit import audit
from ..services.settings import all_settings, set_setting
from . import admin_required, bp
from .helpers import form_bool, form_int, page_arg
from .i18n import translate as _

ACCOUNT_ERRORS = {"invalid_email": "Adresse email invalide.", "invalid_role": "Rôle invalide.",
                  "email_taken": "Cet email est déjà utilisé.",
                  "weak_password": "Mot de passe trop faible : 10 caractères minimum, majuscules, minuscules et chiffres."}
SCOPE_LABELS = {
    "messages:send": "Envoyer des SMS", "messages:read": "Lire les messages", "inbox:read": "Lire les SMS reçus",
    "contacts:read": "Lire les contacts", "contacts:write": "Modifier les contacts",
    "templates:read": "Lire les modèles", "templates:write": "Modifier les modèles",
    "schedules:read": "Lire les planifications", "schedules:write": "Modifier les planifications",
    "devices:read": "Lire les appareils", "campaigns:read": "Lire les campagnes",
    "campaigns:write": "Piloter les campagnes", "webhooks:manage": "Gérer les webhooks",
}


# --- Utilisateurs ------------------------------------------------------------------------

@bp.route("/admin/users", methods=["GET", "POST"])
@admin_required
def admin_users():
    if request.method == "POST":
        f = request.form
        try:
            user = accounts.create_user(f.get("email", ""), f.get("password", ""), f.get("role", "operator"),
                                        f.get("full_name", "").strip() or None, f.get("locale", "fr"))
        except accounts.AccountError as exc:
            db.session.rollback()
            flash(_(ACCOUNT_ERRORS.get(str(exc).split(":")[0], str(exc))), "danger")
            return redirect(url_for("web.admin_users"))
        db.session.flush()
        audit("user.create", user, {"email": user.email, "role": user.role})
        db.session.commit()
        flash(_("Utilisateur créé."), "success")
        return redirect(url_for("web.admin_users"))
    users = db.session.query(User).order_by(User.created_at).all()
    return render_template("admin/users.html", users=users)


@bp.post("/admin/users/<int:uid>")
@admin_required
def admin_user_update(uid):
    user = db.session.get(User, uid)
    if user is None:
        return redirect(url_for("web.admin_users"))
    f = request.form
    if user.id == current_user.id and (f.get("role") != "admin" or not form_bool("is_active")):
        flash(_("Vous ne pouvez pas retirer vos propres droits d'administrateur."), "danger")
        return redirect(url_for("web.admin_users"))
    user.role = f.get("role") if f.get("role") in ("admin", "operator") else user.role
    user.is_active_flag = form_bool("is_active")
    user.full_name = f.get("full_name", "").strip() or user.full_name
    if f.get("password"):
        from ..services.security import password_problems
        if password_problems(f["password"]):
            flash(_(ACCOUNT_ERRORS["weak_password"]), "danger")
            return redirect(url_for("web.admin_users"))
        user.set_password(f["password"])
        user.locked_until, user.failed_logins = None, 0
    audit("user.update", user, {"role": user.role, "active": user.is_active_flag, "password_reset": bool(f.get("password"))})
    db.session.commit()
    flash(_("Utilisateur mis à jour."), "success")
    return redirect(url_for("web.admin_users"))


# --- Clés API ------------------------------------------------------------------------------

@bp.route("/admin/api-keys", methods=["GET", "POST"])
@admin_required
def admin_api_keys():
    new_token = None
    if request.method == "POST":
        f = request.form
        scopes = ["*"] if form_bool("all_scopes") else f.getlist("scopes")
        expires = None
        if f.get("expires_at"):
            try:
                expires = datetime.fromisoformat(f["expires_at"]).replace(tzinfo=timezone.utc)
            except ValueError:
                expires = None
        try:
            key, new_token = accounts.create_api_key(
                f.get("name", "").strip() or "API", scopes, created_by=current_user.id,
                rate_limit_per_min=form_int("rate_limit_per_min", 60, 1, 10000),
                daily_quota=form_int("daily_quota", None, 1), expires_at=expires,
                allow_outside_window=form_bool("allow_outside_window"))
        except accounts.AccountError:
            db.session.rollback()
            flash(_("Choisissez au moins une permission."), "danger")
            return redirect(url_for("web.admin_api_keys"))
        db.session.commit()
    keys = db.session.query(ApiKey).order_by(ApiKey.revoked_at.isnot(None), ApiKey.created_at.desc()).all()
    hooks = db.session.query(Webhook).order_by(Webhook.created_at.desc()).all()
    return render_template("admin/api.html", keys=keys, hooks=hooks, new_token=new_token, scopes=SCOPE_LABELS,
                           events=WEBHOOK_EVENTS, new_secret=None)


@bp.post("/admin/api-keys/<int:kid>/<action>")
@admin_required
def admin_api_key_action(kid, action):
    key = db.session.get(ApiKey, kid)
    if key is None or key.revoked_at:
        return redirect(url_for("web.admin_api_keys"))
    if action == "revoke":
        accounts.revoke_api_key(key, current_user.id)
        db.session.commit()
        flash(_("Clé révoquée."), "success")
        return redirect(url_for("web.admin_api_keys"))
    if action == "rotate":
        _new, token = accounts.rotate_api_key(key, current_user.id)
        db.session.commit()
        keys = db.session.query(ApiKey).order_by(ApiKey.revoked_at.isnot(None), ApiKey.created_at.desc()).all()
        hooks = db.session.query(Webhook).order_by(Webhook.created_at.desc()).all()
        return render_template("admin/api.html", keys=keys, hooks=hooks, new_token=token, scopes=SCOPE_LABELS,
                               events=WEBHOOK_EVENTS, new_secret=None)
    return redirect(url_for("web.admin_api_keys"))


# --- Webhooks ------------------------------------------------------------------------------

@bp.post("/admin/webhooks")
@admin_required
def admin_webhook_create():
    try:
        hook, secret = webhooks.create_webhook(request.form.get("url", ""), request.form.getlist("events"),
                                               user_id=current_user.id)
    except webhooks.WebhookUrlError as exc:
        db.session.rollback()
        flash(_({"https_required": "HTTPS obligatoire pour les webhooks.",
                 "private_address": "Adresse interne refusée.",
                 "invalid_events": "Choisissez au moins un événement.",
                 "unresolvable_host": "Nom de domaine introuvable."}.get(str(exc), "URL invalide.")), "danger")
        return redirect(url_for("web.admin_api_keys"))
    db.session.flush()
    audit("webhook.create", hook, {"url": hook.url})
    db.session.commit()
    keys = db.session.query(ApiKey).order_by(ApiKey.revoked_at.isnot(None), ApiKey.created_at.desc()).all()
    hooks = db.session.query(Webhook).order_by(Webhook.created_at.desc()).all()
    return render_template("admin/api.html", keys=keys, hooks=hooks, new_token=None, scopes=SCOPE_LABELS,
                           events=WEBHOOK_EVENTS, new_secret=secret)


@bp.post("/admin/webhooks/<int:wid>/<action>")
@admin_required
def admin_webhook_action(wid, action):
    hook = db.session.get(Webhook, wid)
    if hook is None:
        return redirect(url_for("web.admin_api_keys"))
    if action == "toggle":
        hook.is_active = not hook.is_active
        hook.failure_count = 0
    elif action == "test":
        webhooks.emit_event_to(hook, "sms.delivered", {"id": "00000000-0000-0000-0000-000000000000", "test": True,
                                                       "phone": "+212600000000", "status": "delivered"})
        flash(_("Événement de test envoyé."), "success")
    elif action == "delete":
        audit("webhook.delete", hook, {"url": hook.url})
        db.session.delete(hook)
    db.session.commit()
    return redirect(url_for("web.admin_webhook_log", wid=wid) if action == "test" else url_for("web.admin_api_keys"))


@bp.get("/admin/webhooks/<int:wid>/log")
@admin_required
def admin_webhook_log(wid):
    hook = db.session.get(Webhook, wid)
    if hook is None:
        return redirect(url_for("web.admin_api_keys"))
    page = (db.session.query(WebhookDelivery).filter_by(webhook_id=wid).order_by(WebhookDelivery.id.desc())
            .paginate(page=page_arg(), per_page=50, error_out=False))
    return render_template("admin/webhook_log.html", hook=hook, page=page)


# --- Paramètres ----------------------------------------------------------------------------

@bp.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        f = request.form
        uid = current_user.id
        set_setting("send_window", {"start": f.get("window_start", "08:00"), "end": f.get("window_end", "21:00"),
                                    "days": [int(d) for d in f.getlist("window_days") if d.isdigit()] or list(range(1, 8)),
                                    "enabled": form_bool("window_enabled")}, uid)
        set_setting("max_attempts", form_int("max_attempts", 3, 1, 10), uid)
        backoff = [int(x) * 60 for x in f.get("retry_backoff", "1,5,15").replace(" ", "").split(",") if x.isdigit()]
        set_setting("retry_backoff", backoff or [60, 300, 900], uid)
        keywords = [k.strip() for k in f.get("stop_keywords", "").replace("\n", ",").split(",") if k.strip()]
        set_setting("stop_keywords", keywords or ["STOP"], uid)
        set_setting("stop_footer", {"fr": f.get("stop_footer_fr", "").strip(), "ar": f.get("stop_footer_ar", "").strip()}, uid)
        set_setting("stop_reply", {"fr": f.get("stop_reply_fr", "").strip(), "ar": f.get("stop_reply_ar", "").strip()}, uid)
        set_setting("stop_reply_enabled", form_bool("stop_reply_enabled"), uid)
        set_setting("abuse_threshold", {"count": form_int("abuse_count", 500, 10), "minutes": form_int("abuse_minutes", 10, 1, 1440)}, uid)
        set_setting("company_name", f.get("company_name", "").strip()[:100], uid)
        audit("settings.update", None)
        db.session.commit()
        flash(_("Paramètres enregistrés."), "success")
        return redirect(url_for("web.admin_settings"))
    return render_template("admin/settings.html", s=all_settings())


# --- Journal d'audit -------------------------------------------------------------------------

@bp.route("/admin/audit")
@admin_required
def admin_audit():
    q = db.session.query(AuditLog)
    if request.args.get("action"):
        q = q.filter(AuditLog.action.ilike(f"%{request.args['action']}%"))
    if request.args.get("user", "").isdigit():
        q = q.filter(AuditLog.user_id == int(request.args["user"]))
    page = q.order_by(AuditLog.created_at.desc()).paginate(page=page_arg(), per_page=50, error_out=False)
    return render_template("admin/audit.html", page=page, users=db.session.query(User).order_by(User.email).all())
