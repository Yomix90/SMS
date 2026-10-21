"""Validation et normalisation des numéros de téléphone (E.164), règles marocaines.

Formats marocains acceptés pour un mobile (6XXXXXXXX ou 7XXXXXXXX) :
    0612345678, 06 12 34 56 78, 06.12.34.56.78, 06-12-34-56-78,
    612345678 (zéro supprimé par Excel), +212612345678, 00212612345678,
    212612345678, +212 0612345678 (zéro superflu après l'indicatif).
Les fixes (05XXXXXXXX) et numéros VoIP (08XXXXXXXX) ne reçoivent pas de SMS : ils sont refusés.
Les numéros étrangers en format international sont validés par la librairie phonenumbers.
"""
import re
from dataclasses import dataclass

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberType

_STRIP_RE = re.compile(r"[\s ‎‏‪-‮.\-()/_]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_MA_MOBILE_RE = re.compile(r"^\+212[67]\d{8}$")
_MA_FIXED_RE = re.compile(r"^\+212[58]\d{8}$")

ERR_EMPTY = "empty"
ERR_FORMAT = "invalid_format"
ERR_LANDLINE = "landline"
ERR_INVALID = "invalid_number"


@dataclass(frozen=True)
class PhoneResult:
    raw: str
    e164: str | None
    error: str | None = None

    @property
    def valid(self) -> bool:
        return self.e164 is not None


ERROR_LABELS = {
    ERR_EMPTY: {"fr": "Numéro vide", "ar": "رقم فارغ"},
    ERR_FORMAT: {"fr": "Format de numéro invalide", "ar": "صيغة الرقم غير صحيحة"},
    ERR_LANDLINE: {"fr": "Numéro fixe : ne peut pas recevoir de SMS", "ar": "رقم ثابت: لا يستقبل الرسائل"},
    ERR_INVALID: {"fr": "Numéro inexistant ou invalide", "ar": "رقم غير صالح"},
}


def error_label(code: str | None, lang: str = "fr") -> str:
    if not code:
        return ""
    return ERROR_LABELS.get(code, {}).get(lang, code)


def _to_text(raw) -> str:
    """Les cellules Excel numériques arrivent en int/float (612345678.0)."""
    if raw is None:
        return ""
    if isinstance(raw, float):
        if raw != raw:  # NaN
            return ""
        raw = int(raw) if raw.is_integer() else raw
    return str(raw).strip()


def normalize_phone(raw, default_region: str = "MA") -> PhoneResult:
    text = _to_text(raw)
    original = text
    if not text:
        return PhoneResult(original, None, ERR_EMPTY)

    text = text.translate(_ARABIC_DIGITS)
    text = _STRIP_RE.sub("", text)
    if text.startswith("00"):
        text = "+" + text[2:]
    if not re.fullmatch(r"\+?\d{6,15}", text):
        return PhoneResult(original, None, ERR_FORMAT)

    if default_region == "MA":
        candidate = _normalize_ma(text)
        if candidate is not None:
            if _MA_MOBILE_RE.match(candidate):
                return PhoneResult(original, candidate)
            if _MA_FIXED_RE.match(candidate):
                return PhoneResult(original, None, ERR_LANDLINE)
            return PhoneResult(original, None, ERR_INVALID)

    # Cas général : numéro international ou autre pays par défaut
    try:
        parsed = phonenumbers.parse(text, None if text.startswith("+") else default_region)
    except NumberParseException:
        return PhoneResult(original, None, ERR_FORMAT)
    if not phonenumbers.is_valid_number(parsed):
        return PhoneResult(original, None, ERR_INVALID)
    if phonenumbers.number_type(parsed) == PhoneNumberType.FIXED_LINE:
        return PhoneResult(original, None, ERR_LANDLINE)
    return PhoneResult(original, phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164))


def _normalize_ma(text: str) -> str | None:
    """Retourne un +212XXXXXXXXX si le numéro ressemble à un numéro marocain, sinon None."""
    digits = text.lstrip("+")
    has_plus = text.startswith("+")
    if digits.startswith("212"):
        national = digits[3:]
        if national.startswith("0") and len(national) == 10:  # +212 0612… (zéro en trop)
            national = national[1:]
        return "+212" + national if len(national) == 9 else ("+212" + national if has_plus else None)
    if has_plus:
        return None  # autre indicatif international
    if digits.startswith("0") and len(digits) == 10:
        return "+212" + digits[1:]
    if len(digits) == 9 and digits[0] in "5678":  # zéro initial perdu (Excel)
        return "+212" + digits
    return None


def is_valid_phone(raw, default_region: str = "MA") -> bool:
    return normalize_phone(raw, default_region).valid


def format_display(e164: str) -> str:
    """+212612345678 -> 06 12 34 56 78 (affichage local), sinon format international."""
    if e164 and e164.startswith("+212") and len(e164) == 13:
        n = "0" + e164[4:]
        return " ".join([n[0:2], n[2:4], n[4:6], n[6:8], n[8:10]])
    try:
        return phonenumbers.format_number(phonenumbers.parse(e164, None),
                                          phonenumbers.PhoneNumberFormat.INTERNATIONAL)
    except NumberParseException:
        return e164 or ""
