"""Imports Excel/CSV côté base : stockage, mappage, validation, création des contacts, envoi."""
import logging
import re
import uuid as uuidlib
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

import sqlalchemy as sa
from flask import current_app
from werkzeug.utils import secure_filename

from ..extensions import db
from ..models import Contact, Group, Import, ImportRow, Receivable, utcnow
from ..models.campaign import IMPORT_FAILED, IMPORT_MAPPED, IMPORT_UPLOADED, IMPORT_VALIDATED
from ..models.contact import CONSENT_OPT_IN
from . import campaigns, importer, queue
from .importer import ImportFileError, Table, ValidationReport

log = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".csv", ".txt"}

# Variables qui alimentent les champs natifs du contact
CONTACT_FIELDS = {
    "nom": "last_name", "prenom": "first_name", "email": "email", "e_mail": "email",
    "date_naissance": "birthday", "date_de_naissance": "birthday", "anniversaire": "birthday",
    "naissance": "birthday", "langue": "locale", "locale": "locale",
}
RECEIVABLE_FIELDS = {"montant", "date_echeance", "reference", "devise"}


# --- Conversions -----------------------------------------------------------------

def parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and 20000 < float(value) < 80000:  # numéro de série Excel
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date()
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(value) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = re.sub(r"[^\d,.\-]", "", str(value))
    if "," in text and "." in text:  # 1.250,50 ou 1,250.50
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") \
            else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


# --- Étape 1 : upload ---------------------------------------------------------------

def _upload_dir() -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_import(data: bytes, filename: str, user_id: int | None = None,
                  sheet_name: str | None = None) -> tuple[Import, Table]:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ImportFileError("unsupported_format")
    table = importer.read_table(data, filename, sheet_name, current_app.config["IMPORT_MAX_ROWS"])
    stored = _upload_dir() / f"{uuidlib.uuid4().hex}{ext}"
    stored.write_bytes(data)

    region = current_app.config.get("DEFAULT_REGION", "MA")
    imp = Import(original_filename=secure_filename(filename) or f"import{ext}", stored_path=str(stored),
                 sheet_name=table.sheet_name, header_row=table.header_row, columns=table.columns,
                 mapping=importer.suggest_mapping(table, region), total_rows=len(table.rows),
                 created_by=user_id, status=IMPORT_UPLOADED)
    db.session.add(imp)
    db.session.flush()
    if table.rows:
        db.session.execute(sa.insert(ImportRow), [
            {"import_id": imp.id, "row_number": table.line_of(i),
             "raw": {k: importer._json_safe(v) for k, v in row.items()},
             "status": "pending", "variables": {}}
            for i, row in enumerate(table.rows)
        ])
    return imp, table


def load_table(imp: Import) -> Table:
    stored = imp.rows.order_by(ImportRow.row_number).all()
    return Table(list(imp.columns), [r.raw for r in stored], imp.sheet_name, imp.header_row,
                 line_numbers=[r.row_number for r in stored])


# --- Étape 2 : mappage et validation ---------------------------------------------------

def validate_import(imp: Import, mapping: dict[str, str], body: str | None = None, *,
                    dedupe: bool = True, lang: str = "fr") -> ValidationReport:
    unknown = set(mapping) - set(imp.columns)
    if unknown:
        raise ValueError(f"unknown_columns:{','.join(sorted(unknown))}")
    table = load_table(imp)
    report = importer.validate_rows(table, mapping, body, blacklist=queue.blacklisted_phones(),
                                    dedupe=dedupe, default_region=current_app.config.get("DEFAULT_REGION", "MA"),
                                    lang=lang)
    by_number = {r.row_number: r for r in report.rows}
    for row in imp.rows:
        v = by_number.get(row.row_number)
        if v is None:
            continue
        row.phone_raw = (v.phone_raw or "")[:60] or None
        row.phone_e164 = v.phone_e164
        row.variables = v.variables
        row.custom_message = v.custom_message
        row.status = v.status
        row.error = (v.error or "")[:255] or None

    summary = report.summary()
    imp.mapping = mapping
    imp.valid_rows = summary["valid"]
    imp.invalid_rows = summary["invalid"] + summary["blacklisted"]
    imp.duplicate_rows = summary["duplicate"]
    imp.status = IMPORT_VALIDATED if summary["valid"] else IMPORT_MAPPED

    if report.rejected:
        path = _upload_dir() / f"rapport_{imp.uuid.hex}.xlsx"
        path.write_bytes(importer.build_error_report(report, imp.columns, lang))
        imp.error_report_path = str(path)
    else:
        imp.error_report_path = None
    return report


# --- Étape 3 : contacts et envoi ---------------------------------------------------------

def upsert_contacts(imp: Import, group: Group | None = None, consent: bool = False) -> dict[str, Contact]:
    """Crée / met à jour les contacts des lignes valides. Retourne {e164: contact}."""
    rows = imp.rows.filter(ImportRow.status == importer.ROW_VALID).all()
    phones = [r.phone_e164 for r in rows]
    existing = {c.phone: c for c in db.session.query(Contact).filter(Contact.phone.in_(phones)).all()} \
        if phones else {}
    result: dict[str, Contact] = {}
    now = utcnow()
    for row in rows:
        contact = existing.get(row.phone_e164)
        if contact is None:
            contact = Contact(phone=row.phone_e164, custom_fields={}, consent_source="import")
            if consent:
                contact.consent_status, contact.consent_at = CONSENT_OPT_IN, now
            db.session.add(contact)
            existing[row.phone_e164] = contact
        custom = dict(contact.custom_fields or {})
        for key, value in (row.variables or {}).items():
            if value in (None, ""):
                continue
            target = CONTACT_FIELDS.get(key)
            if target == "birthday":
                contact.birthday = parse_date(value) or contact.birthday
            elif target == "locale":
                contact.locale = "ar" if str(value).strip().lower() in ("ar", "arabe", "العربية") else "fr"
            elif target:
                setattr(contact, target, str(value)[:255])
            elif key not in RECEIVABLE_FIELDS:
                custom[key] = value
        contact.custom_fields = custom
        if group is not None and group not in contact.groups:
            contact.groups.append(group)
        db.session.flush()
        _upsert_receivable(contact, row.variables or {})
        row.contact_id = contact.id
        result[row.phone_e164] = contact
    return result


def _upsert_receivable(contact: Contact, variables: dict) -> None:
    amount = parse_amount(variables.get("montant"))
    due = parse_date(variables.get("date_echeance"))
    ref = variables.get("reference")
    if amount is None or due is None or not ref:
        return
    ref = str(ref)[:60]
    rec = db.session.query(Receivable).filter_by(contact_id=contact.id, reference=ref).one_or_none()
    if rec is None:
        rec = Receivable(contact_id=contact.id, reference=ref, amount=amount, due_date=due, extra={})
        db.session.add(rec)
    else:
        rec.amount, rec.due_date = amount, due
    if variables.get("devise"):
        rec.currency = str(variables["devise"])[:3].upper()


def send_import(imp: Import, body: str | None, name: str | None = None, *, device=None, sim_slot=None,
                scheduled_at=None, add_stop_footer=False, save_contacts=False, group: Group | None = None,
                user=None, api_key=None, source: str = "import") -> campaigns.CampaignResult:
    if imp.status not in (IMPORT_VALIDATED,):
        raise queue.QueueError("import_not_validated", "L'import doit être validé avant l'envoi", 409)
    contacts = upsert_contacts(imp, group) if save_contacts else {}
    imp.save_contacts = save_contacts
    recipients = [
        campaigns.Recipient(r.phone_e164, r.variables or {}, r.custom_message, contacts.get(r.phone_e164))
        for r in imp.rows.filter(ImportRow.status == importer.ROW_VALID).order_by(ImportRow.row_number)
    ]
    try:
        return campaigns.create_campaign(name or f"Import {imp.original_filename}", body=body,
                                         recipients=recipients, import_=imp, device=device, sim_slot=sim_slot,
                                         scheduled_at=scheduled_at, add_stop_footer=add_stop_footer,
                                         source=source, user=user, api_key=api_key)
    except queue.QueueError:
        imp.status = IMPORT_FAILED
        raise


def purge_old_imports() -> int:
    """Loi 09-08 : suppression des fichiers et lignes importés au-delà de la durée de conservation."""
    limit = utcnow() - timedelta(days=current_app.config["IMPORT_RETENTION_DAYS"])
    old = db.session.query(Import).filter(Import.created_at < limit, Import.stored_path.isnot(None)).all()
    for imp in old:
        for p in (imp.stored_path, imp.error_report_path):
            if p:
                Path(p).unlink(missing_ok=True)
        imp.stored_path = None
        imp.error_report_path = None
        imp.rows.delete(synchronize_session=False)
    return len(old)
