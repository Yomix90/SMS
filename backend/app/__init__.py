"""Fabrique de l'application Flask."""
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, session
from sqlalchemy import event
from sqlalchemy.engine import Engine
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

from .config import get_config  # noqa: E402
from .extensions import babel, csrf, db, login_manager, migrate  # noqa: E402

__version__ = "1.0.0"


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    """SQLite : clés étrangères (ON DELETE CASCADE) et WAL pour la concurrence lecture/écriture."""
    if type(dbapi_conn).__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


def select_locale():
    from flask_login import current_user
    lang = request.args.get("lang")
    if lang in ("fr", "ar"):
        session["lang"] = lang
        return lang
    if session.get("lang") in ("fr", "ar"):
        return session["lang"]
    if getattr(current_user, "is_authenticated", False):
        return current_user.locale
    return request.accept_languages.best_match(["fr", "ar"]) or "fr"


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(get_config(config_name))
    _check_production_secrets(app)
    logging.basicConfig(level=app.config["LOG_LEVEL"],
                        format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    for folder in (app.instance_path, app.config["UPLOAD_FOLDER"], app.config["EXPORT_FOLDER"]):
        Path(folder).mkdir(parents=True, exist_ok=True)

    # Derrière Nginx : IP client et schéma HTTPS corrects
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from . import models  # noqa: F401  (enregistre les modèles)
    db.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)  # batch : ALTER TABLE compatibles SQLite
    csrf.init_app(app)
    babel.init_app(app, locale_selector=select_locale)
    login_manager.init_app(app)
    login_manager.login_view = "web.login"
    login_manager.session_protection = "strong"

    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        user = db.session.get(User, int(user_id))
        return user if user is not None and user.is_active else None

    from .api import init_api
    init_api(app)

    from .device_api import bp as device_bp
    app.register_blueprint(device_bp)
    # API et appareil : authentification par jeton (pas de cookie), donc pas de CSRF
    for blp in list(app.blueprints.values()):
        csrf.exempt(blp)

    _register_web(app)
    _register_error_handlers(app)
    _register_security_headers(app)

    from .sockets import init_sockets
    init_sockets(app)

    from .cli import register_cli
    register_cli(app)

    if app.config["SCHEDULER_ENABLED"] and _should_start_scheduler(app):
        from .tasks import start_scheduler
        start_scheduler(app)

    @app.get("/healthz")
    def healthz():
        db.session.execute(db.text("SELECT 1"))
        return jsonify({"status": "ok", "version": __version__})

    return app


def _check_production_secrets(app) -> None:
    """Refuse de démarrer en production avec des secrets absents ou laissés par défaut."""
    if app.debug or app.testing or os.environ.get("APP_ENV") != "production":
        return
    problems = []
    if app.config["SECRET_KEY"] in ("", "dev-insecure-change-me", "change-me") or len(app.config["SECRET_KEY"]) < 32:
        problems.append("SECRET_KEY")
    if not app.config.get("APP_ENCRYPTION_KEYS") or "change-me" in app.config["APP_ENCRYPTION_KEYS"]:
        problems.append("APP_ENCRYPTION_KEYS")
    if not app.config["PUBLIC_BASE_URL"].startswith("https://"):
        problems.append("PUBLIC_BASE_URL (https requis)")
    if problems:
        raise RuntimeError("Configuration de production invalide : " + ", ".join(problems))


def _should_start_scheduler(app) -> bool:
    # Avec le reloader de Flask en dev, seul le process enfant démarre le scheduler
    if app.debug and os.environ.get("FLASK_RUN_FROM_CLI") == "true":
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    # Pas de scheduler pour les commandes CLI (flask db upgrade, create-admin…)
    return os.environ.get("SMS_NO_SCHEDULER") != "1"


def _register_web(app):
    """Tableau de bord (blueprint web, livré à l'étape suivante)."""
    try:
        from .web import bp as web_bp
    except ImportError:
        @app.get("/")
        def index():
            return redirect("/api/docs")
        return
    app.register_blueprint(web_bp)


def _register_error_handlers(app):
    from .api.errors import ApiError, error_response
    from .services.queue import QueueError

    def _is_api() -> bool:
        return request.path.startswith("/api/") or request.path.startswith("/device/")

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.details, exc.headers)

    @app.errorhandler(QueueError)
    def _queue_error(exc: QueueError):
        db.session.rollback()
        if _is_api():
            return error_response(exc.status, exc.code, exc.message, exc.details)
        return exc.message, exc.status

    @app.errorhandler(413)
    def _too_large(exc):
        if _is_api():
            return error_response(413, "payload_too_large",
                                  f"Fichier trop volumineux (max {app.config['MAX_CONTENT_LENGTH'] // 1048576} Mo)")
        return "Fichier trop volumineux", 413

    @app.errorhandler(500)
    def _server_error(exc):
        db.session.rollback()
        if _is_api():
            return error_response(500, "internal_error", "Erreur interne du serveur")
        return "Erreur interne du serveur", 500


def _register_security_headers(app):
    @app.after_request
    def _headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.path.startswith("/api/v1") or request.path.startswith("/device/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp
