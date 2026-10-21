"""Contacts : création, mise à jour, export et effacement (droits d'accès / suppression, loi 09-08)."""
from flask import current_app

from ..extensions import db
from ..models import Contact, Conversation, Group, Message, Receivable, utcnow
from ..models.contact import CONSENT_OPT_IN, CONSENT_OPTED_OUT, CONSENT_UNKNOWN
from . import queue
from .audit import audit
from .phone import normalize_phone

EDITABLE = ("first_name", "last_name", "email", "birthday", "locale", "notes")


class ContactError(ValueError):
    def __init__(self, code: str, details=None):
        super().__init__(code)
        self.code, self.details = code, details


def _phone(raw) -> str:
    result = normalize_phone(raw, current_app.config.get("DEFAULT_REGION", "MA"))
    if not result.valid:
        raise ContactError("invalid_phone", {"phone": str(raw), "reason": result.error})
    return result.e164


def _groups(values) -> list[Group]:
    """Accepte des ids ou des noms ; les groupes inconnus (par nom) sont créés."""
    groups = []
    for v in values or []:
        g = db.session.get(Group, int(v)) if isinstance(v, int) or str(v).isdigit() else \
            db.session.query(Group).filter_by(name=str(v).strip()).one_or_none()
        if g is None and not str(v).isdigit():
            g = Group(name=str(v).strip()[:100])
            db.session.add(g)
        if g is not None and g not in groups:
            groups.append(g)
    return groups


def apply_consent(contact: Contact, status: str | None, source: str) -> None:
    if not status or status == contact.consent_status:
        return
    if status not in (CONSENT_OPT_IN, CONSENT_UNKNOWN, CONSENT_OPTED_OUT):
        raise ContactError("invalid_consent_status")
    contact.consent_status = status
    contact.consent_source = source
    if status == CONSENT_OPT_IN:
        contact.consent_at = utcnow()
        contact.opted_out_at = None
    elif status == CONSENT_OPTED_OUT:
        contact.opted_out_at = utcnow()
        queue.add_to_blacklist(contact.phone, "manuel")


def create_contact(data: dict, source: str = "api", upsert: bool = False) -> tuple[Contact, bool]:
    phone = _phone(data.get("phone"))
    contact = db.session.query(Contact).filter_by(phone=phone).one_or_none()
    created = contact is None
    if not created and not upsert:
        raise ContactError("contact_exists", {"id": str(contact.uuid)})
    if created:
        contact = Contact(phone=phone, custom_fields={}, consent_source=source)
        db.session.add(contact)
    update_contact(contact, {k: v for k, v in data.items() if k != "phone"}, source)
    db.session.flush()
    audit("contact.create" if created else "contact.update", contact, {"source": source})
    return contact, created


def update_contact(contact: Contact, data: dict, source: str = "api") -> Contact:
    if "phone" in data and data["phone"]:
        phone = _phone(data["phone"])
        if phone != contact.phone and db.session.query(Contact.id).filter_by(phone=phone).first():
            raise ContactError("contact_exists")
        contact.phone = phone
    for field in EDITABLE:
        if field in data:
            setattr(contact, field, data[field])
    if "custom_fields" in data and data["custom_fields"] is not None:
        merged = dict(contact.custom_fields or {})
        merged.update(data["custom_fields"])
        contact.custom_fields = {k: v for k, v in merged.items() if v is not None}
    if "groups" in data and data["groups"] is not None:
        contact.groups = _groups(data["groups"])
    apply_consent(contact, data.get("consent_status"), source)
    return contact


def export_contact_data(contact: Contact) -> dict:
    """Droit d'accès : toutes les données détenues sur un contact."""
    msgs = db.session.query(Message).filter_by(phone=contact.phone).order_by(Message.created_at).all()
    return {
        "contact": {
            "phone": contact.phone, "first_name": contact.first_name, "last_name": contact.last_name,
            "email": contact.email, "birthday": contact.birthday.isoformat() if contact.birthday else None,
            "locale": contact.locale, "custom_fields": contact.custom_fields,
            "consent_status": contact.consent_status, "consent_source": contact.consent_source,
            "consent_at": contact.consent_at.isoformat() if contact.consent_at else None,
            "groups": [g.name for g in contact.groups], "created_at": contact.created_at.isoformat(),
        },
        "receivables": [{"reference": r.reference, "amount": str(r.amount), "due_date": r.due_date.isoformat(),
                         "status": r.status} for r in contact.receivables],
        "messages": [{"direction": m.direction, "body": m.body, "status": m.status,
                      "date": (m.received_at or m.created_at).isoformat()} for m in msgs],
    }


def erase_contact(contact: Contact) -> None:
    """Droit à l'effacement : supprime le contact, ses échéances, messages et conversation.
    L'entrée de liste noire est conservée (obligation de respecter le STOP)."""
    phone = contact.phone
    db.session.query(Message).filter_by(phone=phone).delete(synchronize_session=False)
    db.session.query(Conversation).filter_by(phone=phone).delete(synchronize_session=False)
    db.session.query(Receivable).filter_by(contact_id=contact.id).delete(synchronize_session=False)
    audit("contact.erase", contact, {"phone_suffix": phone[-4:]})
    db.session.delete(contact)
