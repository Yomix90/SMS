"""Fusion des variables {nom}, {prenom}, {montant}, {date_echeance}, {reference}, {colonne_quelconque}.

- Les noms de variables sont normalisés : minuscules, sans accents, espaces -> « _ ».
  Ainsi la colonne « Date échéance » est accessible via {date_echeance}.
- « {{ » et « }} » produisent des accolades littérales.
- Les nombres sont formatés à la française (1 250,50) et les dates en JJ/MM/AAAA.
"""
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal

VAR_RE = re.compile(r"(?<!\{)\{([^{}\n]{1,64})\}(?!\})")

# Alias usuels -> clé canonique
ALIASES = {
    "name": "nom", "last_name": "nom", "lastname": "nom", "nom_de_famille": "nom",
    "first_name": "prenom", "firstname": "prenom",
    "full_name": "nom_complet", "fullname": "nom_complet",
    "phone": "telephone", "tel": "telephone", "gsm": "telephone", "mobile": "telephone",
    "amount": "montant", "due_date": "date_echeance", "echeance": "date_echeance",
    "ref": "reference",
    "الاسم": "prenom", "النسب": "nom", "المبلغ": "montant", "المرجع": "reference",
}


def normalize_key(name: str) -> str:
    """« Date d'échéance » -> « date_d_echeance » ; l'arabe est conservé tel quel."""
    name = str(name).strip()
    decomposed = unicodedata.normalize("NFKD", name)
    no_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    key = re.sub(r"[^\w]+", "_", no_accents.lower(), flags=re.UNICODE).strip("_")
    return ALIASES.get(key, key)


def extract_variables(body: str) -> list[str]:
    """Liste ordonnée et dédoublonnée des variables utilisées dans un texte."""
    seen, result = set(), []
    for match in VAR_RE.finditer(body or ""):
        key = normalize_key(match.group(1))
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def format_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "oui" if value else "non"
    if isinstance(value, datetime):
        if value.hour == 0 and value.minute == 0 and value.second == 0:
            return value.strftime("%d/%m/%Y")
        return value.strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        # Espace simple comme séparateur de milliers (reste compatible GSM-7)
        return f"{value:,}".replace(",", " ")
    if isinstance(value, (float, Decimal)):
        q = Decimal(str(value)).quantize(Decimal("0.01"))
        if q == q.to_integral():
            return format_value(int(q))
        integer, frac = f"{q:.2f}".split(".")
        sign = "-" if integer.startswith("-") else ""
        integer = integer.lstrip("-")
        integer = f"{int(integer):,}".replace(",", " ")
        return f"{sign}{integer},{frac}"
    text = str(value).strip()
    iso = _ISO_DATE_RE.match(text)
    if iso:  # date ISO (CSV, ou valeur Excel relue depuis la base) -> JJ/MM/AAAA
        return f"{iso.group(3)}/{iso.group(2)}/{iso.group(1)}"
    return text


_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ]00:00(?::00)?)?$")


def normalize_variables(variables: dict | None) -> dict[str, str]:
    """Normalise les clés et formate les valeurs."""
    result = {}
    for k, v in (variables or {}).items():
        key = normalize_key(k)
        if key:
            if key in AMOUNT_KEYS and isinstance(v, str):
                v = _parse_amount_text(v)  # montant venu d'un CSV : « 1250.5 » -> 1 250,50
            result[key] = format_value(v)
    return result


AMOUNT_KEYS = {"montant", "montant_du", "solde", "total", "prix"}
_AMOUNT_RE = re.compile(r"^-?[\d\s.,]+$")


def _parse_amount_text(text: str):
    """Convertit un montant textuel (1 250,50 / 1.250,50 / 1250.5) en Decimal, sinon le laisse tel quel."""
    raw = text.strip()
    if not raw or not _AMOUNT_RE.match(raw):
        return text
    t = re.sub(r"[\s]", "", raw)
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    else:
        t = t.replace(",", ".")
    try:
        return Decimal(t)
    except Exception:
        return text


class RenderResult:
    __slots__ = ("text", "missing", "used")

    def __init__(self, text: str, missing: list[str], used: list[str]):
        self.text = text
        self.missing = missing
        self.used = used

    def __repr__(self) -> str:
        return f"RenderResult({self.text!r}, missing={self.missing})"


def render(body: str, variables: dict | None, keep_missing: bool = False) -> RenderResult:
    """Remplace les variables. Une variable absente ou vide est listée dans `missing`
    et remplacée par une chaîne vide (ou laissée telle quelle si keep_missing)."""
    values = normalize_variables(variables)
    missing, used = [], []

    def _sub(match: re.Match) -> str:
        key = normalize_key(match.group(1))
        value = values.get(key)
        if value in (None, ""):
            if key not in missing:
                missing.append(key)
            return match.group(0) if keep_missing else ""
        if key not in used:
            used.append(key)
        return value

    text = VAR_RE.sub(_sub, body or "")
    text = text.replace("{{", "{").replace("}}", "}")
    # Nettoyage des doubles espaces laissés par une variable vide
    if missing and not keep_missing:
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
    return RenderResult(text, missing, used)


def contact_variables(contact, receivable=None, extra: dict | None = None) -> dict:
    """Variables disponibles pour un contact (+ échéance éventuelle + valeurs additionnelles)."""
    from .phone import format_display

    data: dict = {}
    if contact is not None:
        data.update(contact.custom_fields or {})
        data.update({
            "nom": contact.last_name or "",
            "prenom": contact.first_name or "",
            "nom_complet": contact.full_name,
            "telephone": format_display(contact.phone),
            "email": contact.email or "",
        })
        if contact.birthday:
            data["date_naissance"] = contact.birthday
    if receivable is not None:
        data.update(receivable.extra or {})
        data.update({
            "montant": receivable.amount,
            "devise": receivable.currency,
            "date_echeance": receivable.due_date,
            "reference": receivable.reference,
        })
    if extra:
        data.update(extra)
    return data


def sample_variables() -> dict:
    """Valeurs fictives pour l'aperçu d'un modèle sans client sélectionné."""
    return {"nom": "Alaoui", "prenom": "Salma", "nom_complet": "Salma Alaoui", "montant": Decimal("1250.50"),
            "devise": "MAD", "date_echeance": date.today(), "reference": "FAC-2026-001",
            "telephone": "06 12 34 56 78"}
