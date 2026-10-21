"""Format d'erreur unique de l'API :

    {"error": {"code": "invalid_phone", "message": "…", "details": {...}}}

Codes HTTP : 400 requête invalide, 401 clé absente/invalide, 403 permission manquante,
404 introuvable, 409 conflit d'état, 413 fichier trop gros, 422 validation, 429 limite atteinte.
"""
from flask import jsonify
from werkzeug.exceptions import HTTPException

HTTP_CODES = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
    409: "conflict", 413: "payload_too_large", 415: "unsupported_media_type", 422: "validation_error",
    429: "rate_limited", 500: "internal_error",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", details=None, headers=None):
        super().__init__(message or code)
        self.status, self.code, self.message, self.details, self.headers = status, code, message or code, \
            details, headers or {}


def error_response(status: int, code: str, message: str, details=None, headers=None):
    body = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    resp = jsonify(body)
    resp.status_code = status
    for k, v in (headers or {}).items():
        resp.headers[k] = v
    return resp


def http_exception_response(error: HTTPException):
    status = error.code or 500
    data = getattr(error, "data", None) or {}
    details = data.get("messages")
    # webargs : {"json": {"to": ["Missing data…"]}} -> on retire le niveau « location »
    if isinstance(details, dict) and len(details) == 1 and next(iter(details)) in ("json", "query", "form", "files"):
        details = next(iter(details.values()))
    message = "Données invalides" if status == 422 else (error.description or HTTP_CODES.get(status, "error"))
    return error_response(status, HTTP_CODES.get(status, "error"), message, details, data.get("headers"))
