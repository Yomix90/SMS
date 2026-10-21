"""Connexion, déconnexion, profil, langue et thème."""
from datetime import timedelta
from urllib.parse import urlparse

from flask import flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..extensions import db
from ..models import User, utcnow
from ..services.audit import audit
from ..services.security import password_problems, rate_limiter
from . import bp
from .i18n import translate as _

MAX_FAILED = 5
LOCK_MINUTES = 15


def _safe_next(target: str | None) -> str:
    """Évite les redirections ouvertes : uniquement des chemins locaux."""
    if not target:
        return url_for("web.dashboard")
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/") or target.startswith("//"):
        return url_for("web.dashboard")
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("web.dashboard"))
    if request.method == "POST":
        ip = request.headers.get("X-Real-IP") or request.remote_addr
        allowed, _r, _reset = rate_limiter.hit(f"login:{ip}", 10, 300)
        if not allowed:
            flash(_("Trop de tentatives. Réessayez dans quelques minutes."), "danger")
            return render_template("auth/login.html"), 429
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = db.session.query(User).filter_by(email=email).one_or_none()
        now = utcnow()
        if user is not None and user.locked_until and user.locked_until > now:
            flash(_("Compte temporairement verrouillé après plusieurs échecs. Réessayez plus tard."), "danger")
            return render_template("auth/login.html", email=email), 423
        if user is None or not user.is_active or not user.check_password(password):
            if user is not None:
                user.failed_logins += 1
                if user.failed_logins >= MAX_FAILED:
                    user.locked_until = now + timedelta(minutes=LOCK_MINUTES)
                    user.failed_logins = 0
                audit("user.login_failed", user, {"email": email}, user_id=user.id)
            db.session.commit()
            flash(_("Email ou mot de passe incorrect."), "danger")
            return render_template("auth/login.html", email=email), 401
        user.failed_logins = 0
        user.locked_until = None
        user.last_login_at = now
        login_user(user, remember=request.form.get("remember") == "on", duration=timedelta(days=14))
        session["lang"] = user.locale
        audit("user.login", user, user_id=user.id)
        db.session.commit()
        return redirect(_safe_next(request.args.get("next")))
    return render_template("auth/login.html")


@bp.post("/logout")
@login_required
def logout():
    audit("user.logout", current_user)
    db.session.commit()
    logout_user()
    return redirect(url_for("web.login"))


@bp.route("/lang/<code>")
def set_language(code):
    if code in ("fr", "ar"):
        session["lang"] = code
        if current_user.is_authenticated:
            current_user.locale = code
            db.session.commit()
    return redirect(_safe_next(request.args.get("next")))


@bp.route("/profile", methods=["GET", "POST"])
def profile():
    if request.method == "POST":
        current_user.full_name = request.form.get("full_name", "").strip()[:120] or None
        if request.form.get("locale") in ("fr", "ar"):
            current_user.locale = request.form["locale"]
            session["lang"] = current_user.locale
        if request.form.get("theme") in ("auto", "light", "dark"):
            current_user.theme = request.form["theme"]
        new = request.form.get("new_password", "")
        if new:
            if not current_user.check_password(request.form.get("current_password", "")):
                flash(_("Mot de passe actuel incorrect."), "danger")
                return redirect(url_for("web.profile"))
            if new != request.form.get("confirm_password"):
                flash(_("Les deux mots de passe ne correspondent pas."), "danger")
                return redirect(url_for("web.profile"))
            if password_problems(new):
                flash(_("Mot de passe trop faible : 10 caractères minimum, majuscules, minuscules et chiffres."), "danger")
                return redirect(url_for("web.profile"))
            current_user.set_password(new)
            audit("user.password_change", current_user)
        db.session.commit()
        flash(_("Profil enregistré."), "success")
        return redirect(url_for("web.profile"))
    return render_template("auth/profile.html")


@bp.post("/profile/theme")
def set_theme():
    theme = request.form.get("theme")
    if theme in ("light", "dark", "auto"):
        current_user.theme = theme
        db.session.commit()
    return jsonify({"ok": True})
