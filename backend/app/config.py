"""Configuration de l'application, lue depuis les variables d'environnement."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _secret(name: str, default: str = "") -> str:
    """Lit un secret en retirant espaces, retours à la ligne et guillemets copiés par erreur
    depuis l'interface d'une plateforme (ex. "abc…" ou 'abc…')."""
    value = (os.environ.get(name) or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value or default


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _db_url() -> str:
    url = os.environ.get("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'instance' / 'sms.db'}"
    # Compatibilité : postgres:// (Heroku & co) -> pilote psycopg 3
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Config:
    SECRET_KEY = _secret("SECRET_KEY", "dev-insecure-change-me")
    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Clés Fernet (base64 32 octets) séparées par des virgules ; la première chiffre,
    # toutes déchiffrent (rotation). Générer : python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    APP_ENCRYPTION_KEYS = _secret("APP_ENCRYPTION_KEYS")

    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")
    TIMEZONE = os.environ.get("TIMEZONE", "Africa/Casablanca")
    DEFAULT_REGION = os.environ.get("DEFAULT_REGION", "MA")

    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "instance" / "uploads"))
    EXPORT_FOLDER = os.environ.get("EXPORT_FOLDER", str(BASE_DIR / "instance" / "exports"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "10")) * 1024 * 1024
    IMPORT_MAX_ROWS = int(os.environ.get("IMPORT_MAX_ROWS", "50000"))
    IMPORT_RETENTION_DAYS = int(os.environ.get("IMPORT_RETENTION_DAYS", "30"))

    SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)
    DEVICE_OFFLINE_AFTER_SEC = int(os.environ.get("DEVICE_OFFLINE_AFTER_SEC", "90"))
    DEVICE_ACK_TIMEOUT_SEC = int(os.environ.get("DEVICE_ACK_TIMEOUT_SEC", "120"))
    DISPATCH_BATCH_MAX = int(os.environ.get("DISPATCH_BATCH_MAX", "20"))
    PAIRING_CODE_TTL_MIN = int(os.environ.get("PAIRING_CODE_TTL_MIN", "10"))

    WEBHOOK_TIMEOUT_SEC = int(os.environ.get("WEBHOOK_TIMEOUT_SEC", "10"))
    WEBHOOK_ALLOW_HTTP = _bool("WEBHOOK_ALLOW_HTTP", False)

    # Sessions / cookies
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_HTTPONLY = True
    WTF_CSRF_TIME_LIMIT = None

    # i18n
    LANGUAGES = ["fr", "ar"]
    BABEL_DEFAULT_LOCALE = "fr"
    BABEL_DEFAULT_TIMEZONE = TIMEZONE

    # OpenAPI (flask-smorest)
    API_TITLE = "SMS Gateway API"
    API_VERSION = "v1"
    OPENAPI_VERSION = "3.0.3"
    OPENAPI_URL_PREFIX = "/api"
    OPENAPI_JSON_PATH = "openapi.json"
    OPENAPI_SWAGGER_UI_PATH = "/docs"
    OPENAPI_SWAGGER_UI_URL = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/"

    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")


class DevelopmentConfig(Config):
    DEBUG = True
    WEBHOOK_ALLOW_HTTP = True


class ProductionConfig(Config):
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    PREFERRED_URL_SCHEME = "https"


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    WTF_CSRF_ENABLED = False
    SCHEDULER_ENABLED = False
    WEBHOOK_ALLOW_HTTP = True
    SECRET_KEY = "test"
    APP_ENCRYPTION_KEYS = "ZmRzZmRzZmRzZmRzZmRzZmRzZmRzZmRzZmRzZmRzZmQ="


CONFIGS = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def get_config(name: str | None = None):
    name = name or os.environ.get("APP_ENV", "development")
    return CONFIGS.get(name, DevelopmentConfig)
