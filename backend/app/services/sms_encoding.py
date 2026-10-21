"""Calcul de l'encodage SMS (GSM-7 / UCS-2) et du nombre de segments.

- GSM-7 : 160 caractères en 1 SMS, 153 par segment en multipart.
  Les caractères de la table d'extension (€ [ ] { } ^ ~ | \\ saut de page) comptent double
  et ne peuvent pas être coupés entre deux segments.
- UCS-2 (dès qu'un caractère hors GSM est présent, ex. arabe, emoji) : 70 en 1 SMS,
  67 par segment. Les emojis (paires de substitution UTF-16) comptent double et ne sont pas coupés.
"""
import math
from dataclasses import asdict, dataclass

GSM7_BASIC = (
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENDED = "^{}\\[~]|€\f"
_GSM7_BASIC_SET = frozenset(GSM7_BASIC)
_GSM7_EXT_SET = frozenset(GSM7_EXTENDED)

GSM7 = "gsm7"
UCS2 = "ucs2"

LIMITS = {
    GSM7: {"single": 160, "multi": 153},
    UCS2: {"single": 70, "multi": 67},
}


@dataclass
class SmsInfo:
    encoding: str
    characters: int       # caractères visibles
    units: int            # septets (GSM) ou unités UTF-16 (UCS-2)
    segments: int
    per_segment: int
    remaining: int        # unités restantes dans le dernier segment
    non_gsm_chars: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def is_gsm7(text: str) -> bool:
    return all(c in _GSM7_BASIC_SET or c in _GSM7_EXT_SET for c in text)


def _char_units(c: str, encoding: str) -> int:
    if encoding == GSM7:
        return 2 if c in _GSM7_EXT_SET else 1
    return 2 if ord(c) > 0xFFFF else 1  # paire de substitution UTF-16


def analyze(text: str) -> SmsInfo:
    text = text or ""
    encoding = GSM7 if is_gsm7(text) else UCS2
    units_list = [_char_units(c, encoding) for c in text]
    total = sum(units_list)
    single, multi = LIMITS[encoding]["single"], LIMITS[encoding]["multi"]

    if total == 0:
        segments, per_segment, remaining = 0, single, single
    elif total <= single:
        segments, per_segment, remaining = 1, single, single - total
    else:
        # Remplissage glouton : un caractère multi-unités n'est jamais coupé
        segments, used = 1, 0
        for u in units_list:
            if used + u > multi:
                segments += 1
                used = 0
            used += u
        per_segment, remaining = multi, multi - used

    non_gsm = sorted({c for c in text if c not in _GSM7_BASIC_SET and c not in _GSM7_EXT_SET}) \
        if encoding == UCS2 else []
    return SmsInfo(encoding, len(text), total, segments, per_segment, remaining, non_gsm[:20])


def segment_count(text: str) -> int:
    return analyze(text).segments


# Substitutions proposées pour rester en GSM-7 (texte français)
GSM_REPLACEMENTS = {
    "’": "'", "‘": "'", "“": '"', "”": '"', "«": '"', "»": '"', "–": "-", "—": "-",
    "…": "...", " ": " ", "â": "a", "ê": "e", "î": "i", "ô": "o", "û": "u",
    "ë": "e", "ï": "i", "ç": "c", "œ": "oe", "Â": "A", "Ê": "E", "Î": "I", "Ô": "O",
    "Û": "U", "È": "E", "À": "A", "Ù": "U", "Œ": "OE",
}


def to_gsm_friendly(text: str) -> str:
    """Remplace les caractères courants qui forcent l'UCS-2 (sans toucher à l'arabe)."""
    return "".join(GSM_REPLACEMENTS.get(c, c) for c in text)


def estimate_max_chars(segments: int, encoding: str) -> int:
    lim = LIMITS[encoding]
    return lim["single"] if segments <= 1 else lim["multi"] * segments


def segments_for_units(units: int, encoding: str) -> int:
    lim = LIMITS[encoding]
    if units <= lim["single"]:
        return 1 if units else 0
    return math.ceil(units / lim["multi"])
