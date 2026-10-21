"""Appareils (lecture) et webhooks."""
from flask import g
from flask.views import MethodView
from flask_smorest import Blueprint

from ..extensions import db
from ..models import Device, Webhook
from ..services import webhooks as webhook_service
from ..services.audit import audit
from . import get_by_uuid
from .auth import require_api_key
from .errors import ApiError
from .schemas import DeviceOutSchema, WebhookCreatedSchema, WebhookSchema

blp = Blueprint("devices", __name__, url_prefix="/devices", description="Téléphones passerelles")
webhooks_blp = Blueprint("webhooks", __name__, url_prefix="/webhooks", description="Webhooks sortants")
BLUEPRINTS = (blp, webhooks_blp)


@blp.route("")
@require_api_key("devices:read")
@blp.response(200, DeviceOutSchema(many=True))
def list_devices():
    """Lister les appareils et leur état (en ligne, batterie, SIM)"""
    return db.session.query(Device).filter(Device.revoked_at.is_(None)).order_by(Device.name).all()


@blp.route("/<uuid:device_id>")
@require_api_key("devices:read")
@blp.response(200, DeviceOutSchema)
def get_device(device_id):
    """Détail d'un appareil"""
    return get_by_uuid(Device, device_id, "Appareil")


@webhooks_blp.route("")
class Webhooks(MethodView):
    @require_api_key("webhooks:manage")
    @webhooks_blp.response(200, WebhookSchema(many=True))
    def get(self):
        """Lister les webhooks de cette clé"""
        return db.session.query(Webhook).filter_by(api_key_id=g.api_key.id).all()

    @require_api_key("webhooks:manage")
    @webhooks_blp.arguments(WebhookSchema)
    @webhooks_blp.response(201, WebhookCreatedSchema)
    def post(self, data):
        """Créer un webhook

        Le `secret` renvoyé (une seule fois) sert à vérifier l'en-tête `X-Webhook-Signature`.
        """
        try:
            hook, secret = webhook_service.create_webhook(data["url"], data["events"], api_key_id=g.api_key.id)
        except webhook_service.WebhookUrlError as exc:
            raise ApiError(422, str(exc), "URL de webhook refusée (HTTPS public requis)")
        db.session.flush()
        audit("webhook.create", hook, {"url": hook.url, "events": hook.events})
        db.session.commit()
        return {"uuid": hook.uuid, "url": hook.url, "events": hook.events, "is_active": hook.is_active,
                "failure_count": 0, "created_at": hook.created_at, "secret": secret}


@webhooks_blp.route("/<uuid:webhook_id>", methods=["DELETE"])
@require_api_key("webhooks:manage")
@webhooks_blp.response(204)
def delete_webhook(webhook_id):
    """Supprimer un webhook"""
    hook = get_by_uuid(Webhook, webhook_id, "Webhook")
    if hook.api_key_id != g.api_key.id:
        raise ApiError(404, "not_found", "Webhook introuvable")
    audit("webhook.delete", hook, {"url": hook.url})
    db.session.delete(hook)
    db.session.commit()
