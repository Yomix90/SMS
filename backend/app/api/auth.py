"""Authentification par clé API (Authorization: Bearer sk_…), permissions et limitation de débit."""
from datetime import timedelta
from functools import wraps

from flask import g, request

from ..extensions import db
from ..models import utcnow
from ..services.accounts import find_api_key
from ..services.security import rate_limiter
from .errors import ApiError


def _client_ip() -> str | None:
    return request.headers.get("X-Real-IP") or request.remote_addr


def require_api_key(scope: str | None = None):
    """Décorateur : authentifie la clé, vérifie la permission et le débit (par minute)."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            header = request.headers.get("Authorization", "")
            token = header[7:].strip() if header.lower().startswith("bearer ") else request.headers.get("X-API-Key")
            if not token:
                raise ApiError(401, "missing_api_key", "En-tête Authorization: Bearer <clé> requis",
                               headers={"WWW-Authenticate": "Bearer"})
            key = find_api_key(token)
            if key is None or not key.is_valid:
                raise ApiError(401, "invalid_api_key", "Clé API invalide, expirée ou révoquée",
                               headers={"WWW-Authenticate": "Bearer"})
            if scope and not key.has_scope(scope):
                raise ApiError(403, "insufficient_scope", f"Permission requise : {scope}", {"required": scope})

            allowed, remaining, reset = rate_limiter.hit(f"api:{key.id}", key.rate_limit_per_min, 60)
            g.rate_limit = (key.rate_limit_per_min, remaining, reset)
            if not allowed:
                raise ApiError(429, "rate_limited", "Trop de requêtes, réessayez plus tard",
                               {"limit_per_min": key.rate_limit_per_min},
                               headers={"Retry-After": str(reset)})
            # Évite une écriture en base à chaque requête : au plus une fois par minute
            now = utcnow()
            if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
                key.last_used_at = now
                key.last_used_ip = _client_ip()
                db.session.commit()
            g.api_key = key
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def add_rate_limit_headers(response):
    info = getattr(g, "rate_limit", None)
    if info:
        limit, remaining, reset = info
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset)
    return response
