"""Tableau de bord web (Jinja + Bootstrap 5, FR/AR avec RTL, mode sombre)."""
from functools import wraps

from flask import Blueprint, abort, flash, redirect, request, url_for
from flask_login import current_user

bp = Blueprint("web", __name__)

PUBLIC_ENDPOINTS = {"web.login", "web.set_language", "static"}


@bp.before_request
def _require_login():
    if request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if not current_user.is_authenticated:
        return redirect(url_for("web.login", next=request.full_path.rstrip("?") if request.method == "GET" else None))
    return None


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


# Messages par code d'erreur métier (QueueError.code) quand le texte contient des valeurs variables
ERROR_MESSAGES = {
    "invalid_phone": "Numéro de téléphone invalide.",
    "message_too_long": "Message trop long (10 SMS maximum).",
    "quota_exceeded": "Quota journalier atteint.",
}


def flash_error(exc_or_msg) -> None:
    from .i18n import translate as _
    code = getattr(exc_or_msg, "code", None)
    message = ERROR_MESSAGES.get(code) or getattr(exc_or_msg, "message", None) or str(exc_or_msg)
    flash(_(message), "danger")


@bp.record_once
def _setup(state):
    from . import helpers
    helpers.register(state.app)


# Import des vues (enregistrement des routes)
from . import (admin, auth, automations, campaigns, contacts, dashboard, devices, imports,  # noqa: E402,F401
               inbox, messages, msg_templates, reports)
