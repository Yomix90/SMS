"""Fuseau horaire (Africa/Casablanca) et fenêtre d'envoi autorisée."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context

from ..models.base import utcnow  # noqa: F401  (réexport pratique)


def tz() -> ZoneInfo:
    name = current_app.config["TIMEZONE"] if has_app_context() else "Africa/Casablanca"
    return ZoneInfo(name)


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz())


def local_now() -> datetime:
    return datetime.now(tz())


def local_today() -> date:
    return local_now().date()


def local_to_utc(d: date, t: time) -> datetime:
    """Combine une date et une heure locales (Casablanca) en datetime UTC."""
    return datetime.combine(d, t, tzinfo=tz()).astimezone(timezone.utc)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """Datetime naïf = heure locale Casablanca ; aware = converti en UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz())
    return dt.astimezone(timezone.utc)


def local_day_bounds(d: date) -> tuple[datetime, datetime]:
    """Début et fin (UTC) d'une journée locale."""
    start = local_to_utc(d, time.min)
    return start, local_to_utc(d + timedelta(days=1), time.min)


def _parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


class SendWindow:
    """Plage horaire d'envoi, ex. {"start": "08:00", "end": "21:00", "days": [1..7]}.

    Les plages qui traversent minuit (start > end) sont gérées. days : 1 = lundi.
    """

    def __init__(self, start: str = "08:00", end: str = "21:00", days: list[int] | None = None,
                 enabled: bool = True):
        self.start = _parse_hhmm(start)
        self.end = _parse_hhmm(end)
        self.days = set(days or range(1, 8))
        self.enabled = enabled

    @classmethod
    def from_setting(cls, value: dict | None) -> "SendWindow":
        value = value or {}
        return cls(value.get("start", "08:00"), value.get("end", "21:00"),
                   value.get("days"), value.get("enabled", True))

    def contains(self, dt_utc: datetime) -> bool:
        if not self.enabled:
            return True
        local = to_local(dt_utc)
        t = local.time()
        if self.start <= self.end:
            return local.isoweekday() in self.days and self.start <= t < self.end
        # Plage de nuit : le jour de référence est celui du début de plage
        if t >= self.start:
            return local.isoweekday() in self.days
        if t < self.end:
            return (local - timedelta(days=1)).isoweekday() in self.days
        return False

    def next_open(self, dt_utc: datetime) -> datetime:
        """Premier instant (UTC) >= dt_utc où l'envoi est autorisé."""
        if self.contains(dt_utc):
            return dt_utc
        local = to_local(dt_utc)
        for offset in range(0, 9):
            day = local.date() + timedelta(days=offset)
            if day.isoweekday() not in self.days:
                continue
            candidate = datetime.combine(day, self.start, tzinfo=tz())
            if candidate >= local:
                return candidate.astimezone(timezone.utc)
        return dt_utc  # aucun jour autorisé configuré : on ne bloque pas
