"""Vérification des secrets au démarrage en production."""
import importlib

import pytest
from cryptography.fernet import Fernet

VALID_KEY = Fernet.generate_key().decode()


def make_app(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SMS_NO_SCHEDULER", "1")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path.as_posix()}/p.db")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://sms.digitlab.dev")
    monkeypatch.setenv("APP_ENCRYPTION_KEYS", VALID_KEY)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    import app.config as config
    importlib.reload(config)          # les attributs de Config sont lus à l'import
    import app as app_pkg
    importlib.reload(app_pkg)
    application = app_pkg.create_app("production")
    return application


@pytest.fixture(autouse=True)
def _restore_config():
    yield
    import app.config as config
    importlib.reload(config)


def test_missing_secret_key_is_generated_and_persisted(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    a1 = make_app(monkeypatch, tmp_path)
    key = a1.config["SECRET_KEY"]
    assert len(key) == 64
    a2 = make_app(monkeypatch, tmp_path)
    assert a2.config["SECRET_KEY"] == key       # même clé après redémarrage


def test_quoted_and_spaced_secrets_are_cleaned(monkeypatch, tmp_path):
    secret = "a" * 64
    a = make_app(monkeypatch, tmp_path, SECRET_KEY=f'  "{secret}"\n', APP_ENCRYPTION_KEYS=f"'{VALID_KEY}' ")
    assert a.config["SECRET_KEY"] == secret
    assert a.config["APP_ENCRYPTION_KEYS"] == VALID_KEY


def test_short_secret_key_gives_precise_error(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="trop courte : 10 caractères"):
        make_app(monkeypatch, tmp_path, SECRET_KEY="0123456789")


def test_default_secret_key_refused(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="valeur par défaut"):
        make_app(monkeypatch, tmp_path, SECRET_KEY="change-me")


def test_invalid_fernet_key_refused(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="clé Fernet invalide"):
        make_app(monkeypatch, tmp_path, SECRET_KEY="b" * 64, APP_ENCRYPTION_KEYS="pas-une-cle")


def test_http_public_url_refused(monkeypatch, tmp_path):
    with pytest.raises(RuntimeError, match="https requis"):
        make_app(monkeypatch, tmp_path, SECRET_KEY="c" * 64, PUBLIC_BASE_URL="http://sms.digitlab.dev")
