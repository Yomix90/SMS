import os

import pytest

os.environ["SMS_NO_SCHEDULER"] = "1"

from app import create_app  # noqa: E402
from app.extensions import db as _db  # noqa: E402
from app.services.security import rate_limiter  # noqa: E402


@pytest.fixture()
def app(tmp_path):
    app = create_app("testing")
    app.config.update(UPLOAD_FOLDER=str(tmp_path / "uploads"), EXPORT_FOLDER=str(tmp_path / "exports"))
    with app.app_context():
        _db.create_all()
        rate_limiter.reset()
        yield app
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def db(app):
    return _db


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def emits(app):
    """Événements Socket.IO émis (capturés en mode test)."""
    store = app.extensions.setdefault("test_emits", [])
    store.clear()
    return store


@pytest.fixture()
def api_token(db):
    from app.services.accounts import create_api_key
    _key, token = create_api_key("tests", ["*"], rate_limit_per_min=1000)
    db.session.commit()
    return token


@pytest.fixture()
def auth(api_token):
    return {"Authorization": f"Bearer {api_token}"}


@pytest.fixture()
def device(db):
    """Appareil appairé et en ligne. Retourne (device, token)."""
    from app.services import devices
    _pc, code = devices.create_pairing_code(device_name="Test A14")
    db.session.commit()
    dev, token = devices.pair_device(code, {"model": "SM-A145", "sims": [{"slot": 0, "carrier": "inwi"},
                                                                         {"slot": 1, "carrier": "Orange"}]})
    devices.heartbeat(dev, {"battery": 80})
    db.session.commit()
    return dev, token


@pytest.fixture()
def device_headers(device):
    return {"Authorization": f"Bearer {device[1]}"}
