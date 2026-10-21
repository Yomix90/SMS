"""Tests du tableau de bord : authentification, rendu de toutes les pages (FR/AR), formulaires clés."""
import io
import re
from datetime import time
from pathlib import Path

import pytest

from app.models import (Automation, Campaign, Contact, Group, Import, KeywordRule, Message, Schedule, Template, User,
                        Webhook)
from app.services import campaigns, queue
from app.services.accounts import create_user
from app.services.sample import build_sample_xlsx

PASSWORD = "Secret12345x"


def _csrf_off(app):
    app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture()
def admin(db):
    user = create_user("admin@test.ma", PASSWORD, role="admin", full_name="Admin Test")
    db.session.commit()
    return user


@pytest.fixture()
def logged(client, admin):
    r = client.post("/login", data={"email": "admin@test.ma", "password": PASSWORD})
    assert r.status_code == 302
    return client


@pytest.fixture()
def data(db, device):
    """Jeu de données couvrant toutes les pages."""
    g = Group(name="Clients", color="#0d6efd")
    c = Contact(phone="+212612345678", first_name="Salma", last_name="Alaoui", custom_fields={"ville": "Casa"})
    c.groups.append(g)
    db.session.add_all([g, c, Template(name="Rappel", language="fr", body="Bonjour {prenom}", variables=["prenom"]),
                        KeywordRule(keywords=["INFO"], reply_body="Horaires")])
    db.session.flush()
    result = campaigns.create_campaign("Test", body="Bonjour {prenom}", audience={"groups": [g.id]})
    msg = queue.enqueue("0700112233", "Unitaire")
    from app.services import inbound
    inbound.receive(device[0], {"uuid": "w1", "phone": "+212612345678", "body": "Salut"})
    db.session.add(Automation(name="Anniv", trigger="birthday", run_time=time(9, 0)))
    db.session.add(Schedule(name="Hebdo", body="x", audience={"groups": [g.id]}, recurrence="weekly",
                            run_time=time(9, 0), days_of_week=[1]))
    db.session.commit()
    return {"contact": c, "group": g, "campaign": result.campaign, "message": msg, "device": device[0]}


def test_login_required(client):
    r = client.get("/messages")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_login_failure_and_lockout(client, admin, db):
    for _ in range(5):
        assert client.post("/login", data={"email": "admin@test.ma", "password": "bad"}).status_code == 401
    r = client.post("/login", data={"email": "admin@test.ma", "password": PASSWORD})
    assert r.status_code == 423  # verrouillé après 5 échecs


def test_open_redirect_blocked(client, admin):
    r = client.post("/login?next=https://evil.example", data={"email": "admin@test.ma", "password": PASSWORD})
    assert r.headers["Location"] == "/"


def test_csrf_enforced(app, client, admin):
    app.config["WTF_CSRF_ENABLED"] = True
    r = client.post("/login", data={"email": "admin@test.ma", "password": PASSWORD})
    assert r.status_code == 400


PAGES = ["/", "/send", "/messages", "/campaigns", "/campaigns/new", "/imports", "/inbox", "/contacts",
         "/contacts/new", "/groups", "/templates", "/templates/new", "/blacklist", "/automations",
         "/automations?tab=triggers", "/automations?tab=keywords", "/schedules/new", "/automations/new",
         "/keywords/new", "/reports", "/devices", "/notifications", "/profile", "/admin/users", "/admin/api-keys",
         "/admin/settings", "/admin/audit", "/messages?status=queued&direction=out"]


@pytest.mark.parametrize("lang", ["fr", "ar"])
def test_all_pages_render(logged, data, lang):
    logged.get(f"/lang/{lang}")
    dynamic = [f"/contacts/{data['contact'].uuid}", f"/campaigns/{data['campaign'].uuid}",
               f"/messages/{data['message'].uuid}", f"/devices/{data['device'].uuid}",
               f"/inbox?c={data['message'].conversation_id}", f"/automations/1/edit", "/keywords/1/edit"]
    for url in PAGES + dynamic:
        r = logged.get(url)
        assert r.status_code == 200, (url, r.status_code, r.data[:300])
        html = r.get_data(as_text=True)
        assert f'lang="{lang}"' in html
        if lang == "ar":
            assert 'dir="rtl"' in html and "bootstrap.rtl" in html


def test_operator_cannot_access_admin(client, db):
    create_user("op@test.ma", PASSWORD, role="operator")
    db.session.commit()
    client.post("/login", data={"email": "op@test.ma", "password": PASSWORD})
    assert client.get("/admin/users").status_code == 403
    assert client.get("/messages").status_code == 200


def test_send_single(logged, db, data):
    r = logged.post("/send", data={"phone": "06 61 23 45 67", "body": "Bonjour {prenom}", "contact_id": ""})
    assert r.status_code == 302
    assert db.session.query(Message).filter_by(phone="+212661234567").one().body == "Bonjour"
    r = logged.post("/send", data={"phone": "0522000000", "body": "x"})
    assert r.status_code == 422


def test_send_with_contact_variables(logged, db, data):
    logged.post("/send", data={"phone": "+212612345678", "body": "Bonjour {prenom}",
                               "contact_id": str(data["contact"].uuid)})
    assert db.session.query(Message).filter_by(source="web", body="Bonjour Salma").count() == 1


def test_campaign_preview_and_create(logged, db, data):
    gid = data["group"].id
    r = logged.post("/campaigns/preview", data={"groups": [gid], "body": "Salut {prenom} de {ville}",
                                                "numbers": "0700112233\nabc"})
    d = r.get_json()
    assert d["count"] == 2 and d["text"] == "Salut Salma de Casa" and d["invalid"] == ["abc"]
    r = logged.post("/campaigns/new", data={"name": "Promo", "groups": [gid], "body": "Salut {prenom}",
                                            "when": "now", "add_stop_footer": "on"})
    assert r.status_code == 302
    c = db.session.query(Campaign).filter_by(name="Promo").one()
    assert c.total == 1
    r = logged.post(f"/campaigns/{c.uuid}/pause")
    assert db.session.get(Campaign, c.id).status == "paused"


def test_import_wizard(logged, db, data):
    r = logged.post("/imports/upload", data={"file": (io.BytesIO(build_sample_xlsx()), "clients.xlsx")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    imp = db.session.query(Import).one()
    r = logged.get(f"/imports/{imp.uuid}/map")
    assert r.status_code == 200
    form = {}
    for i, col in enumerate(imp.columns):
        value = imp.mapping[col]
        if value in ("phone", "message", ""):
            form[f"map_{i}"] = value
        else:
            form[f"map_{i}"], form[f"var_{i}"] = "__var__", value
    form.update({"body": "Bonjour {prenom}, {montant} MAD le {date_echeance}", "dedupe": "on"})
    r = logged.post(f"/imports/{imp.uuid}/map", data=form)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Bonjour Salma, 1 250,50 MAD le" in html
    db.session.refresh(imp)
    assert imp.valid_rows == 5 and imp.duplicate_rows == 1
    assert logged.get(f"/imports/{imp.uuid}/report").status_code == 200
    r = logged.post(f"/imports/{imp.uuid}/send", data={"body": form["body"], "when": "now", "save_contacts": "on",
                                                       "new_group": "Import octobre"})
    assert r.status_code == 302
    assert db.session.query(Campaign).filter_by(source="import").one().total == 5
    assert db.session.query(Group).filter_by(name="Import octobre").one().contacts
    # Double envoi impossible
    logged.post(f"/imports/{imp.uuid}/send", data={"body": form["body"], "when": "now"})
    assert db.session.query(Campaign).filter_by(source="import").count() == 1


def test_contact_crud_and_receivable(logged, db, data):
    r = logged.post("/contacts/new", data={"phone": "0661234567", "first_name": "Omar", "locale": "ar",
                                           "custom_fields": "agence=Maarif", "groups": [data["group"].id]})
    assert r.status_code == 302
    c = db.session.query(Contact).filter_by(phone="+212661234567").one()
    assert c.custom_fields == {"agence": "Maarif"} and c.groups[0].name == "Clients"
    logged.post(f"/contacts/{c.uuid}/receivables", data={"reference": "F1", "amount": "1 200,50", "due_date": "2026-12-01"})
    assert str(c.receivables[0].amount) == "1200.50"
    assert logged.get(f"/contacts/{c.uuid}/export").get_json()["contact"]["phone"] == "+212661234567"
    logged.post(f"/contacts/{c.uuid}/delete")
    assert db.session.query(Contact).filter_by(phone="+212661234567").count() == 0


def test_blacklist(logged, db):
    logged.post("/blacklist", data={"phone": "0612345678", "reason": "plainte"})
    assert queue.is_blacklisted("+212612345678")


def test_inbox_reply(logged, db, data):
    conv_id = data["message"].conversation_id
    inbound_conv = db.session.query(Message).filter_by(direction="in").one().conversation_id
    r = logged.post(f"/inbox/{inbound_conv}/reply", data={"body": "Merci"}, headers={"X-Requested-With": "fetch"})
    assert r.status_code == 200 and "Merci" in r.get_data(as_text=True)
    assert conv_id


def test_templates_and_automations_forms(logged, db, data):
    assert logged.post("/templates/new", data={"name": "Promo", "body": "Hello {prenom}", "language": "fr",
                                               "category": "promo"}).status_code == 302
    assert logged.post("/templates/preview", data={"body": "Hi {prenom}"}).get_json()["text"] == "Hi Salma"
    tpl = db.session.query(Template).filter_by(name="Promo").one()
    r = logged.post("/automations/new", data={"trigger": "due_reminder", "days_before": "5, 2",
                                              "template_fr_id": str(tpl.uuid), "run_time": "10:00", "is_active": "on"})
    assert r.status_code == 302
    assert db.session.query(Automation).filter_by(trigger="due_reminder").one().config == {"days_before": [2, 5]}
    r = logged.post("/schedules/new", data={"name": "Mensuel", "body": "x", "groups": [data["group"].id],
                                            "recurrence": "monthly", "run_time": "09:00", "day_of_month": "31"})
    assert r.status_code == 302
    r = logged.post("/keywords/new", data={"keywords": "PRIX", "match_type": "contains", "action": "reply",
                                           "reply_body": "Nos prix", "is_active": "on"})
    assert r.status_code == 302


def test_admin_api_keys_and_webhooks(logged, db):
    r = logged.post("/admin/api-keys", data={"name": "ERP", "scopes": ["messages:send"], "rate_limit_per_min": "30"})
    assert r.status_code == 200 and re.search(r"sk_[\w-]{20,}", r.get_data(as_text=True))
    r = logged.post("/admin/webhooks", data={"url": "https://hooks.example.com/x", "events": ["sms.received"]})
    assert "whsec_" in r.get_data(as_text=True)
    hook = db.session.query(Webhook).one()
    assert logged.post(f"/admin/webhooks/{hook.id}/test").status_code == 302


def test_admin_settings_and_users(logged, db):
    r = logged.post("/admin/settings", data={"window_enabled": "on", "window_start": "09:00", "window_end": "20:00",
                                             "window_days": ["1", "2", "3", "4", "5", "6"], "max_attempts": "4",
                                             "retry_backoff": "2,10", "stop_keywords": "STOP, توقف",
                                             "stop_footer_fr": "STOP", "stop_footer_ar": "STOP", "abuse_count": "300",
                                             "abuse_minutes": "10"})
    assert r.status_code == 302
    from app.services.settings import get_setting
    assert get_setting("retry_backoff") == [120, 600] and get_setting("send_window")["days"] == [1, 2, 3, 4, 5, 6]
    logged.post("/admin/users", data={"email": "op2@test.ma", "password": PASSWORD, "role": "operator"})
    assert db.session.query(User).filter_by(email="op2@test.ma").one().role == "operator"
    me = db.session.query(User).filter_by(email="admin@test.ma").one()
    logged.post(f"/admin/users/{me.id}", data={"role": "operator", "is_active": "on"})
    assert db.session.get(User, me.id).role == "admin"  # protection contre l'auto-rétrogradation


def test_device_pairing_endpoints(logged, db):
    r = logged.post("/devices/pair", data={"name": "A14"})
    d = r.get_json()
    assert "<svg" in d["svg"] and '"code"' in d["payload"]
    assert logged.get(d["status_url"]).get_json()["state"] == "waiting"


def test_reports_exports(logged, data):
    assert logged.get("/reports?export=xlsx").data[:2] == b"PK"
    assert logged.get("/messages?export=pdf").data[:4] == b"%PDF"
    assert logged.get("/contacts?export=xlsx").data[:2] == b"PK"
    assert logged.get("/imports/sample.xlsx").data[:2] == b"PK"


# --- Couverture des traductions ----------------------------------------------------------

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"
WEB_DIR = Path(__file__).resolve().parent.parent / "app" / "web"
LITERAL = re.compile(r"""_\(\s*(["'])((?:(?!\1).)+?)\1""")


def _source_strings() -> set[str]:
    found = set()
    for path in list(TEMPLATE_DIR.rglob("*.html")) + list(WEB_DIR.glob("*.py")):
        if path.name == "translations_ar.py":
            continue
        for m in LITERAL.finditer(path.read_text(encoding="utf-8")):
            found.add(m.group(2).replace("\\'", "'"))
    return found


def test_every_ui_string_has_arabic_translation():
    from app.web.translations_ar import AR
    missing = sorted(s for s in _source_strings() if s not in AR)
    assert not missing, f"{len(missing)} chaînes sans traduction : {missing[:30]}"
