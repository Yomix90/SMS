"""API REST v1 documentée (OpenAPI 3 / Swagger UI sur /api/docs)."""
import math
import uuid as uuidlib

from flask import request
from flask_smorest import Api
from werkzeug.exceptions import HTTPException

from ..extensions import db
from .errors import ApiError, http_exception_response

API_DESCRIPTION = """
API de la passerelle SMS : envoi de SMS (unitaire, lot, campagne personnalisée, fichier Excel/CSV),
suivi des statuts, boîte de réception, contacts, modèles, planifications, appareils et webhooks.

**Authentification** : `Authorization: Bearer sk_…` (clé créée depuis le tableau de bord, permissions par clé).

**Erreurs** : `{"error": {"code": "...", "message": "...", "details": {...}}}`.

**Limitation de débit** : en-têtes `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`.

**Dates** : ISO 8601. Une date sans fuseau est interprétée en heure de Casablanca (Africa/Casablanca).
"""


class GatewayApi(Api):
    """Api flask-smorest dont les erreurs HTTP suivent notre format, uniquement sous /api."""

    def handle_http_exception(self, error: HTTPException):
        if request.path.startswith("/api/") or request.path.startswith("/device/"):
            return http_exception_response(error)
        return error  # pages web : rendu HTML standard de Werkzeug


def make_api() -> GatewayApi:
    return GatewayApi(spec_kwargs={
        "info": {"description": API_DESCRIPTION, "contact": {"name": "SMS Gateway"}},
        "components": {"securitySchemes": {"bearerAuth": {"type": "http", "scheme": "bearer",
                                                          "bearerFormat": "sk_…"}}},
        "security": [{"bearerAuth": []}],
    })


def paginate(query, page: int, per_page: int):
    total = query.order_by(None).count()
    items = query.limit(per_page).offset((page - 1) * per_page).all()
    return items, {"page": page, "per_page": per_page, "total": total,
                   "pages": math.ceil(total / per_page) if per_page else 0}


def get_by_uuid(model, value, name: str = "resource"):
    try:
        uid = value if isinstance(value, uuidlib.UUID) else uuidlib.UUID(str(value))
    except (ValueError, TypeError):
        raise ApiError(404, "not_found", f"{name} introuvable")
    obj = db.session.query(model).filter_by(uuid=uid).one_or_none()
    if obj is None:
        raise ApiError(404, "not_found", f"{name} introuvable")
    return obj


def init_api(app) -> GatewayApi:
    """Une instance Api par application (permet plusieurs apps, ex. tests)."""
    from flask import request as _request

    from . import contacts, devices, inbox, messages, templates
    from .auth import add_rate_limit_headers

    api = make_api()
    api.init_app(app)
    for module in (messages, inbox, contacts, templates, devices):
        for blp in module.BLUEPRINTS:
            api.register_blueprint(blp, url_prefix=f"/api/v1{blp.url_prefix or ''}")

    @app.after_request
    def _rate_headers(response):
        if _request.path.startswith("/api/v1"):
            add_rate_limit_headers(response)
        return response

    app.extensions["gateway_api"] = api
    return api
