"""Contacts et groupes."""
import sqlalchemy as sa
from flask import current_app
from flask.views import MethodView
from flask_smorest import Blueprint

from ..extensions import db
from ..models import Contact, Group
from ..models.contact import contact_groups
from ..services import contacts as contact_service
from ..services.phone import normalize_phone
from . import get_by_uuid, paginate
from .auth import require_api_key
from .errors import ApiError
from .schemas import (ContactListArgs, ContactPageSchema, ContactSchema, ContactUpdateSchema, GroupMembersSchema,
                      GroupSchema, PageArgs)

blp = Blueprint("contacts", __name__, url_prefix="/contacts", description="Contacts")
groups_blp = Blueprint("groups", __name__, url_prefix="/groups", description="Groupes de contacts")
BLUEPRINTS = (blp, groups_blp)


def _contact_error(exc: contact_service.ContactError):
    status = 409 if exc.code == "contact_exists" else 422
    return ApiError(status, exc.code, exc.code.replace("_", " "), exc.details)


@blp.route("")
class Contacts(MethodView):
    @require_api_key("contacts:read")
    @blp.arguments(ContactListArgs, location="query")
    @blp.response(200, ContactPageSchema)
    def get(self, args):
        """Lister / rechercher les contacts"""
        q = db.session.query(Contact)
        if args["q"]:
            term = f"%{args['q'].strip()}%"
            digits = args["q"].replace(" ", "").lstrip("0")
            q = q.filter(sa.or_(Contact.first_name.ilike(term), Contact.last_name.ilike(term),
                                Contact.phone.contains(digits) if digits else sa.false()))
        if args["group_id"]:
            q = q.filter(Contact.id.in_(sa.select(contact_groups.c.contact_id)
                                        .where(contact_groups.c.group_id == args["group_id"])))
        if args["consent_status"]:
            q = q.filter(Contact.consent_status == args["consent_status"])
        items, meta = paginate(q.order_by(Contact.last_name, Contact.first_name), args["page"], args["per_page"])
        return {"data": items, "meta": meta}

    @require_api_key("contacts:write")
    @blp.arguments(ContactSchema)
    @blp.response(201, ContactSchema)
    def post(self, data):
        """Créer un contact (409 si le numéro existe déjà)"""
        try:
            contact, _ = contact_service.create_contact(data, source="api")
        except contact_service.ContactError as exc:
            raise _contact_error(exc)
        db.session.commit()
        return contact


@blp.route("/<uuid:contact_id>")
class ContactItem(MethodView):
    @require_api_key("contacts:read")
    @blp.response(200, ContactSchema)
    def get(self, contact_id):
        """Détail d'un contact"""
        return get_by_uuid(Contact, contact_id, "Contact")

    @require_api_key("contacts:write")
    @blp.arguments(ContactUpdateSchema(partial=True))
    @blp.response(200, ContactSchema)
    def patch(self, data, contact_id):
        """Modifier un contact"""
        contact = get_by_uuid(Contact, contact_id, "Contact")
        try:
            contact_service.update_contact(contact, data)
        except contact_service.ContactError as exc:
            raise _contact_error(exc)
        db.session.commit()
        return contact

    @require_api_key("contacts:write")
    @blp.response(204)
    def delete(self, contact_id):
        """Supprimer un contact et ses données (droit à l'effacement)"""
        contact_service.erase_contact(get_by_uuid(Contact, contact_id, "Contact"))
        db.session.commit()


@blp.route("/<uuid:contact_id>/export")
@require_api_key("contacts:read")
@blp.response(200)
def export_contact(contact_id):
    """Exporter toutes les données d'un contact (droit d'accès, loi 09-08)"""
    return contact_service.export_contact_data(get_by_uuid(Contact, contact_id, "Contact"))


# --- Groupes ------------------------------------------------------------------------------

@groups_blp.route("")
class Groups(MethodView):
    @require_api_key("contacts:read")
    @groups_blp.arguments(PageArgs, location="query")
    @groups_blp.response(200, GroupSchema(many=True))
    def get(self, args):
        """Lister les groupes"""
        items, _ = paginate(db.session.query(Group).order_by(Group.name), args["page"], args["per_page"])
        return items

    @require_api_key("contacts:write")
    @groups_blp.arguments(GroupSchema)
    @groups_blp.response(201, GroupSchema)
    def post(self, data):
        """Créer un groupe"""
        if db.session.query(Group.id).filter_by(name=data["name"]).first():
            raise ApiError(409, "group_exists", "Un groupe porte déjà ce nom")
        group = Group(**data)
        db.session.add(group)
        db.session.commit()
        return group


@groups_blp.route("/<int:group_id>/contacts", methods=["POST"])
@require_api_key("contacts:write")
@groups_blp.arguments(GroupMembersSchema)
@groups_blp.response(200, GroupSchema)
def add_members(data, group_id):
    """Ajouter des contacts à un groupe (UUID ou numéros ; numéros inconnus créés)"""
    group = db.session.get(Group, group_id)
    if group is None:
        raise ApiError(404, "not_found", "Groupe introuvable")
    region = current_app.config.get("DEFAULT_REGION", "MA")
    invalid = []
    for ref in data["contacts"]:
        contact = None
        if len(ref) == 36 and ref.count("-") == 4:
            contact = get_by_uuid(Contact, ref, "Contact")
        else:
            phone = normalize_phone(ref, region)
            if not phone.valid:
                invalid.append(ref)
                continue
            contact = db.session.query(Contact).filter_by(phone=phone.e164).one_or_none()
            if contact is None:
                contact, _ = contact_service.create_contact({"phone": phone.e164}, source="api")
        if group not in contact.groups:
            contact.groups.append(group)
    if invalid and len(invalid) == len(data["contacts"]):
        raise ApiError(422, "invalid_phone", "Aucun numéro valide", {"invalid": invalid})
    db.session.commit()
    return group
