"""Filtres Jinja, variables globales des gabarits et utilitaires de formulaires."""
import json
import uuid as uuidlib
from datetime import datetime

from flask import abort, request
from flask_babel import get_locale
from flask_login import current_user
from markupsafe import Markup

from ..extensions import db
from ..services.phone import format_display
from ..services.reports import STATUS_LABELS
from ..services.timeutil import ensure_utc, to_local
from .i18n import translate

STATUS_COLORS = {
    "scheduled": "info", "queued": "secondary", "paused": "warning", "dispatched": "primary",
    "sent": "primary", "delivered": "success", "failed": "danger", "canceled": "dark", "blocked": "dark",
    "received": "success",
    # campagnes
    "draft": "secondary", "running": "primary", "completed": "success",
    # appareils
    "online": "success", "offline": "danger", "disabled": "dark",
}


def lang() -> str:
    return str(get_locale() or "fr")


def fmt_dt(value, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Date locale, isolée en LTR pour ne pas être réordonnée dans une page RTL."""
    if not value:
        return ""
    return Markup(f'<bdi dir="ltr">{to_local(value).strftime(fmt)}</bdi>')


# Statuts propres au tableau de bord (campagnes, appareils), en plus de ceux des messages
EXTRA_STATUS_LABELS = {
    "fr": {"draft": "Brouillon", "running": "En cours", "completed": "Terminée", "online": "En ligne",
           "offline": "Hors ligne", "disabled": "Désactivé"},
    "ar": {"draft": "مسودة", "running": "جارية", "completed": "منتهية", "online": "متصل",
           "offline": "غير متصل", "disabled": "معطل"},
}
SOURCE_LABELS = {"manual": "Manuelle", "import": "Import", "api": "API", "schedule": "Planifiée",
                 "automation": "Automatique", "web": "Tableau de bord", "campaign": "Campagne",
                 "auto_reply": "Réponse automatique", "device": "Téléphone"}


def source_label(source: str) -> str:
    return translate(SOURCE_LABELS.get(source, source))


def fmt_relative(value) -> str:
    if not value:
        return translate("jamais")
    from ..models import utcnow
    seconds = int((utcnow() - value).total_seconds())
    if seconds < 60:
        return translate("à l'instant")
    if seconds < 3600:
        return translate("il y a {n} min", n=seconds // 60)
    if seconds < 86400:
        return translate("il y a {n} h", n=seconds // 3600)
    return fmt_dt(value, "%d/%m/%Y")


def status_badge(status: str) -> Markup:
    current = lang() if lang() in STATUS_LABELS else "fr"
    label = STATUS_LABELS[current].get(status) or EXTRA_STATUS_LABELS[current].get(status) or status
    color = STATUS_COLORS.get(status, "secondary")
    return Markup(f'<span class="badge text-bg-{color} status-badge" data-status="{status}">{Markup.escape(label)}</span>')


def nav_counts() -> dict:
    """Compteurs du menu latéral (non lus, appareils en ligne, notifications)."""
    if not current_user.is_authenticated:
        return {}
    import sqlalchemy as sa

    from ..models import Conversation, Device, Notification
    unread = db.session.query(sa.func.coalesce(sa.func.sum(Conversation.unread_count), 0)).scalar()
    online = db.session.query(Device).filter_by(status="online").count()
    devices = db.session.query(Device).filter(Device.revoked_at.is_(None)).count()
    q = db.session.query(Notification).filter(Notification.is_read.is_(False))
    q = q.filter((Notification.user_id == current_user.id) |
                 (Notification.user_id.is_(None) if current_user.is_admin else sa.false()))
    return {"unread": int(unread or 0), "online": online, "devices": devices, "notifications": q.count()}


def register(app):
    env = app.jinja_env
    from ..models import utcnow
    env.globals.update(_=translate, status_badge=status_badge, lang=lang, nav_counts=nav_counts, now=utcnow,
                       source_label=source_label)
    env.filters["dt"] = fmt_dt
    env.filters["ago"] = fmt_relative
    env.filters["phone"] = lambda v: format_display(v) if v else ""
    env.filters["tojson_attr"] = lambda v: json.dumps(v, ensure_ascii=False)

    @app.context_processor
    def _ctx():
        current = lang()
        theme = getattr(current_user, "theme", "auto") if current_user.is_authenticated else "auto"
        return {"current_lang": current, "text_dir": "rtl" if current == "ar" else "ltr", "user_theme": theme}


# --- Utilitaires de formulaires ----------------------------------------------------

def get_or_404(model, uid):
    try:
        value = uid if isinstance(uid, uuidlib.UUID) else uuidlib.UUID(str(uid))
    except ValueError:
        abort(404)
    obj = db.session.query(model).filter_by(uuid=value).one_or_none()
    if obj is None:
        abort(404)
    return obj


def form_int(name: str, default=None, min_value=None, max_value=None):
    raw = request.form.get(name, "").strip()
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if min_value is not None and value < min_value:
        value = min_value
    if max_value is not None and value > max_value:
        value = max_value
    return value


def form_bool(name: str) -> bool:
    return request.form.get(name) in ("1", "on", "true", "yes")


def form_datetime(name: str) -> datetime | None:
    """<input type="datetime-local"> : heure locale Casablanca -> UTC."""
    raw = request.form.get(name, "").strip()
    if not raw:
        return None
    try:
        return ensure_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def form_ids(name: str) -> list[int]:
    out = []
    for v in request.form.getlist(name):
        if str(v).isdigit():
            out.append(int(v))
    return out


def page_arg() -> int:
    return max(request.args.get("page", 1, type=int), 1)
