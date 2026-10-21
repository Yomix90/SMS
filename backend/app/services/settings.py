"""Réglages applicatifs stockés en base, avec valeurs par défaut."""
from copy import deepcopy

from ..extensions import db
from ..models import Setting

DEFAULTS: dict = {
    "send_window": {"start": "08:00", "end": "21:00", "days": [1, 2, 3, 4, 5, 6, 7], "enabled": True},
    "max_attempts": 3,
    "retry_backoff": [60, 300, 900],  # secondes
    "stop_keywords": ["STOP", "ARRET", "ARRÊT", "STOPP", "UNSUBSCRIBE", "توقف", "ستوب", "إيقاف", "ايقاف"],
    "stop_footer": {"fr": "STOP au {numero}", "ar": "للإلغاء أرسل STOP إلى {numero}"},
    "stop_reply": {"fr": "Vous êtes désinscrit(e). Vous ne recevrez plus nos SMS.",
                   "ar": "تم إلغاء اشتراككم. لن تتوصلوا برسائلنا بعد الآن."},
    "stop_reply_enabled": True,
    "abuse_threshold": {"count": 500, "minutes": 10},
    "default_country": "MA",
    "company_name": "",
}


def get_setting(key: str):
    row = db.session.get(Setting, key)
    if row is not None and row.value is not None:
        return row.value
    return deepcopy(DEFAULTS.get(key))


def set_setting(key: str, value, user_id: int | None = None) -> None:
    row = db.session.get(Setting, key)
    if row is None:
        row = Setting(key=key)
        db.session.add(row)
    row.value = value
    row.updated_by = user_id


def all_settings() -> dict:
    data = deepcopy(DEFAULTS)
    for row in db.session.query(Setting).all():
        data[row.key] = row.value
    return data
