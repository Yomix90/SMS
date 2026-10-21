"""Endpoints d'envoi et de suivi des messages, et des campagnes."""
import json

from flask import current_app, g, request, send_file
from flask.views import MethodView
from flask_smorest import Blueprint
from flask_smorest.fields import Upload
from marshmallow import fields as mfields

from ..extensions import db
from ..models import Campaign, Device, Import, Message, Template
from ..services import campaigns as campaign_service
from ..services import imports as import_service
from ..services import queue
from ..services.importer import ImportFileError, MESSAGE_FIELD, PHONE_FIELD
from ..services.reports import MessageFilters, filtered_messages
from ..services.templating import normalize_key
from . import get_by_uuid, paginate
from .auth import require_api_key
from .errors import ApiError
from .schemas import (BaseSchema, BulkResultSchema, BulkSchema, CampaignOutSchema, MessageBatchResultSchema,
                      MessageCreateSchema, MessageDetailSchema, MessageListArgs, MessageOutSchema,
                      MessagePageSchema, PageArgs, UploadFormSchema, UploadResultSchema)

blp = Blueprint("messages", __name__, url_prefix="/messages", description="Envoi et suivi des SMS")
campaign_blp = Blueprint("campaigns", __name__, url_prefix="/campaigns", description="Campagnes (envois groupés)")
BLUEPRINTS = (blp, campaign_blp)


class FileSchema(BaseSchema):
    file = Upload(required=True, metadata={"description": "Fichier .xlsx ou .csv (10 Mo max)"})


def _device(device_id):
    if device_id is None:
        return None
    device = get_by_uuid(Device, device_id, "Appareil")
    if not device.can_send:
        raise ApiError(409, "device_disabled", "Appareil désactivé ou révoqué")
    return device


def _check_window_permission(ignore_window: bool) -> None:
    if ignore_window and not g.api_key.allow_outside_window:
        raise ApiError(403, "window_override_forbidden",
                       "Cette clé n'est pas autorisée à envoyer hors de la fenêtre horaire")


def _enqueue_one(item: dict, idempotency_key: str | None = None) -> Message:
    _check_window_permission(item.get("ignore_window"))
    return queue.enqueue(item["to"], item["message"], device=_device(item.get("device_id")),
                         sim_slot=item.get("sim_slot"), scheduled_at=item.get("scheduled_at"),
                         priority=item.get("priority") or 0, source="api", api_key=g.api_key,
                         external_ref=item.get("external_ref"), idempotency_key=idempotency_key,
                         ignore_window=bool(item.get("ignore_window")))


@blp.route("")
class Messages(MethodView):
    @require_api_key("messages:read")
    @blp.arguments(MessageListArgs, location="query")
    @blp.response(200, MessagePageSchema)
    def get(self, args):
        """Lister les messages (filtrable)"""
        f = MessageFilters(date_from=args["date_from"], date_to=args["date_to"], status=args["status"],
                           direction=args["direction"], phone=args["phone"])
        if args["campaign_id"]:
            f.campaign_id = get_by_uuid(Campaign, args["campaign_id"], "Campagne").id
        if args["device_id"]:
            f.device_id = get_by_uuid(Device, args["device_id"], "Appareil").id
        q = filtered_messages(f)
        if args["external_ref"]:
            q = q.filter(Message.external_ref == args["external_ref"])
        items, meta = paginate(q.order_by(Message.created_at.desc()), args["page"], args["per_page"])
        return {"data": items, "meta": meta}

    @require_api_key("messages:send")
    @blp.arguments(MessageCreateSchema)
    @blp.response(202, MessageBatchResultSchema)
    @blp.alt_response(422, description="Numéro ou message invalide")
    @blp.alt_response(429, description="Débit, quota ou seuil anti-abus atteint")
    def post(self, data):
        """Envoyer un SMS ou un lot de SMS

        Envoi unique : `{"to": "0612345678", "message": "Bonjour"}`.
        Lot : `{"messages": [{"to": ..., "message": ...}, ...]}` (1000 max).
        L'en-tête `Idempotency-Key` (envoi unique) évite les doublons en cas de nouvelle tentative.
        """
        items = data["messages"] or [data]
        queue.check_api_quota(g.api_key, len(items))
        queue.check_abuse(len(items), api_key=g.api_key)
        idem = request.headers.get("Idempotency-Key") if not data["messages"] else None
        created, rejected = [], []
        for index, item in enumerate(items):
            try:
                created.append(_enqueue_one(item, idem))
            except queue.QueueError as exc:
                if not data["messages"]:
                    raise
                rejected.append({"index": index, "phone": item.get("to"), "error": exc.code,
                                 "message": exc.message})
        db.session.commit()
        return {"data": created, "rejected": rejected}


@blp.route("/<uuid:message_id>")
@require_api_key("messages:read")
@blp.response(200, MessageDetailSchema)
def get_message(message_id):
    """Statut et historique d'un message"""
    return get_by_uuid(Message, message_id, "Message")


@blp.route("/<uuid:message_id>/cancel", methods=["POST"])
@require_api_key("messages:send")
@blp.response(200, MessageOutSchema)
def cancel_message(message_id):
    """Annuler un message pas encore envoyé"""
    msg = get_by_uuid(Message, message_id, "Message")
    if msg.api_key_id != g.api_key.id and not g.api_key.has_scope("*"):
        raise ApiError(403, "forbidden", "Message créé par une autre clé")
    if not queue.cancel_message(msg):
        raise ApiError(409, "not_cancelable", f"Message déjà au statut « {msg.status} »")
    db.session.commit()
    return msg


@blp.route("/bulk", methods=["POST"])
@require_api_key("messages:send")
@blp.arguments(BulkSchema)
@blp.response(202, BulkResultSchema)
def bulk(data):
    """Envoi groupé personnalisé (variables par destinataire)

    Crée une campagne. Le texte commun (`message` ou `template_id`) peut contenir des variables
    `{nom}`, `{prenom}`, `{montant}`, `{date_echeance}`, `{reference}` ou toute clé de `variables`.
    Un destinataire peut avoir son propre `message`.
    """
    template = get_by_uuid(Template, data["template_id"], "Modèle") if data["template_id"] else None
    recipients = [campaign_service.Recipient(r["phone"], r["variables"], r["message"],
                                             external_ref=r.get("external_ref")) for r in data["recipients"]]
    result = campaign_service.create_campaign(
        data["name"] or f"API — {len(recipients)} destinataires", body=data["message"], template=template,
        recipients=recipients, device=_device(data["device_id"]), sim_slot=data["sim_slot"],
        scheduled_at=data["scheduled_at"], add_stop_footer=data["add_stop_footer"], source="api",
        api_key=g.api_key, priority=data["priority"])
    db.session.commit()
    return {"campaign": result.campaign, "created": result.created, "blocked": result.blocked,
            "rejected": result.rejected}


@blp.route("/upload", methods=["POST"])
@require_api_key("messages:send")
@blp.arguments(FileSchema, location="files")
@blp.arguments(UploadFormSchema, location="form")
@blp.response(202, UploadResultSchema)
def upload(files, form):
    """Envoi depuis un fichier Excel/CSV

    La colonne téléphone est détectée automatiquement (ou `phone_column`). Une colonne « message »
    permet un texte différent par ligne. Les autres colonnes deviennent des variables
    (ex. « Date échéance » → `{date_echeance}`). `dry_run=true` valide sans envoyer.
    """
    file = files["file"]
    try:
        imp, _table = import_service.create_import(file.read(), file.filename or "import.xlsx",
                                                   sheet_name=form["sheet"])
    except ImportFileError as exc:
        raise ApiError(422, str(exc), "Fichier illisible, vide, trop volumineux ou format non supporté")

    mapping = dict(imp.mapping)
    if form["mapping"]:
        try:
            custom = json.loads(form["mapping"])
            assert isinstance(custom, dict)
        except (ValueError, AssertionError):
            raise ApiError(422, "invalid_mapping", "mapping doit être un objet JSON {colonne: variable}")
        mapping.update(custom)
    for col, fld in ((form["phone_column"], PHONE_FIELD), (form["message_column"], MESSAGE_FIELD)):
        if col:
            if col not in imp.columns:
                raise ApiError(422, "unknown_column", f"Colonne inconnue : {col}", {"columns": imp.columns})
            mapping = {c: (v if v != fld else normalize_key(c)) for c, v in mapping.items()}
            mapping[col] = fld
    if PHONE_FIELD not in mapping.values():
        raise ApiError(422, "phone_column_not_found", "Colonne téléphone introuvable : précisez phone_column",
                       {"columns": imp.columns})

    body = form["message"]
    if form["template_id"]:
        body = get_by_uuid(Template, form["template_id"], "Modèle").body
    try:
        report = import_service.validate_import(imp, mapping, body)
    except ValueError as exc:
        raise ApiError(422, "invalid_mapping", str(exc))

    campaign = None
    if not form["dry_run"] and report.valid:
        result = import_service.send_import(imp, body, form["name"], device=_device(form["device_id"]),
                                            sim_slot=form["sim_slot"], scheduled_at=form["scheduled_at"],
                                            add_stop_footer=form["add_stop_footer"],
                                            save_contacts=form["save_contacts"], api_key=g.api_key, source="api")
        campaign = result.campaign
    db.session.commit()
    return {
        "import_id": imp.uuid, "columns": imp.columns, "mapping": mapping, "summary": report.summary(),
        "preview": [{"row": r.row_number, "phone": r.phone_e164, "text": r.rendered} for r in report.valid[:5]],
        "rejected": [r for r in report.rejected[:500]],
        "campaign": campaign,
        "error_report_url": f"/api/v1/messages/upload/{imp.uuid}/report" if imp.error_report_path else None,
    }


@blp.route("/upload/<uuid:import_id>/report")
@require_api_key("messages:send")
@blp.response(200, description="Rapport d'erreurs au format xlsx")
def upload_report(import_id):
    """Télécharger le rapport d'erreurs d'un import"""
    imp = get_by_uuid(Import, import_id, "Import")
    if not imp.error_report_path:
        raise ApiError(404, "not_found", "Aucun rapport d'erreurs pour cet import")
    return send_file(imp.error_report_path, as_attachment=True,
                     download_name=f"erreurs_{imp.original_filename.rsplit('.', 1)[0]}.xlsx")


# --- Campagnes -------------------------------------------------------------------------

class CampaignListArgs(PageArgs):
    status = mfields.String(load_default=None)


@campaign_blp.route("")
@require_api_key("campaigns:read")
@campaign_blp.arguments(CampaignListArgs, location="query")
@campaign_blp.response(200, CampaignOutSchema(many=True))
def list_campaigns(args):
    """Lister les campagnes"""
    q = db.session.query(Campaign)
    if args["status"]:
        q = q.filter(Campaign.status == args["status"])
    items, _ = paginate(q.order_by(Campaign.created_at.desc()), args["page"], args["per_page"])
    return items


@campaign_blp.route("/<uuid:campaign_id>")
@require_api_key("campaigns:read")
@campaign_blp.response(200, CampaignOutSchema)
def get_campaign(campaign_id):
    """Détail et progression d'une campagne"""
    return get_by_uuid(Campaign, campaign_id, "Campagne")


@campaign_blp.route("/<uuid:campaign_id>/<any(pause,resume,cancel):action>", methods=["POST"])
@require_api_key("campaigns:write")
@campaign_blp.response(200, CampaignOutSchema)
def campaign_action(campaign_id, action):
    """Mettre en pause, reprendre ou annuler une campagne"""
    campaign = get_by_uuid(Campaign, campaign_id, "Campagne")
    {"pause": campaign_service.pause_campaign, "resume": campaign_service.resume_campaign,
     "cancel": campaign_service.cancel_campaign}[action](campaign)
    db.session.commit()
    return campaign
