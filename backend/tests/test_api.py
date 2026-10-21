import io
from datetime import datetime

from openpyxl import Workbook

from app.models import Campaign, Contact, Message
from app.services.accounts import create_api_key


def test_auth_errors(client, db):
    r = client.get("/api/v1/messages")
    assert r.status_code == 401 and r.json["error"]["code"] == "missing_api_key"
    r = client.get("/api/v1/messages", headers={"Authorization": "Bearer sk_bad"})
    assert r.status_code == 401 and r.json["error"]["code"] == "invalid_api_key"
    _k, token = create_api_key("lecture", ["messages:read"])
    db.session.commit()
    r = client.post("/api/v1/messages", json={"to": "0612345678", "message": "x"},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and r.json["error"]["details"]["required"] == "messages:send"


def test_revoked_key(client, db):
    from app.services.accounts import revoke_api_key
    key, token = create_api_key("tmp", ["*"])
    revoke_api_key(key)
    db.session.commit()
    assert client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_rate_limit(client, db):
    _k, token = create_api_key("lent", ["*"], rate_limit_per_min=2)
    db.session.commit()
    h = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/devices", headers=h).headers["X-RateLimit-Remaining"] == "1"
    client.get("/api/v1/devices", headers=h)
    r = client.get("/api/v1/devices", headers=h)
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_send_single_and_get(client, auth):
    r = client.post("/api/v1/messages", json={"to": "06 12 34 56 78", "message": "Bonjour",
                                              "external_ref": "CMD-1"}, headers=auth)
    assert r.status_code == 202, r.json
    data = r.json["data"][0]
    assert data["phone"] == "+212612345678" and data["status"] == "queued"
    r = client.get(f"/api/v1/messages/{data['id']}", headers=auth)
    assert r.status_code == 200 and r.json["events"][0]["status"] == "queued"
    r = client.get("/api/v1/messages?external_ref=CMD-1", headers=auth)
    assert r.json["meta"]["total"] == 1


def test_send_validation_errors(client, auth):
    r = client.post("/api/v1/messages", json={"to": "0522000000", "message": "x"}, headers=auth)
    assert r.status_code == 422 and r.json["error"]["code"] == "invalid_phone"
    r = client.post("/api/v1/messages", json={"message": "x"}, headers=auth)
    assert r.status_code == 422 and r.json["error"]["code"] == "validation_error"
    r = client.post("/api/v1/messages", json={"to": "0612345678", "message": "x", "sim_slot": 5}, headers=auth)
    assert r.status_code == 422 and "sim_slot" in r.json["error"]["details"]


def test_idempotency(client, auth, db):
    h = {**auth, "Idempotency-Key": "abc-123"}
    first = client.post("/api/v1/messages", json={"to": "0612345678", "message": "x"}, headers=h).json
    second = client.post("/api/v1/messages", json={"to": "0612345678", "message": "x"}, headers=h).json
    assert first["data"][0]["id"] == second["data"][0]["id"]
    assert db.session.query(Message).count() == 1


def test_batch_with_rejections(client, auth):
    r = client.post("/api/v1/messages", headers=auth, json={"messages": [
        {"to": "0612345678", "message": "a"}, {"to": "123", "message": "b"}, {"to": "0700112233", "message": "c"}]})
    assert r.status_code == 202
    assert len(r.json["data"]) == 2 and r.json["rejected"][0]["index"] == 1


def test_window_override_needs_permission(client, db):
    _k, token = create_api_key("std", ["messages:send"])
    db.session.commit()
    r = client.post("/api/v1/messages", json={"to": "0612345678", "message": "OTP", "ignore_window": True},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_bulk_personalized(client, auth, db):
    r = client.post("/api/v1/messages/bulk", headers=auth, json={
        "name": "Relances",
        "message": "Bonjour {prenom}, {montant} MAD dus le {date_echeance}.",
        "recipients": [
            {"phone": "0612345678", "variables": {"prenom": "Salma", "montant": 1250.5, "date_echeance": "2026-11-05"}},
            {"phone": "0700112233", "variables": {"prenom": "Omar"}, "message": "Message perso"},
            {"phone": "invalide"},
        ]})
    assert r.status_code == 202, r.json
    assert r.json["created"] == 2 and len(r.json["rejected"]) == 1
    assert r.json["campaign"]["total"] == 2
    bodies = {m.phone: m.body for m in db.session.query(Message)}
    assert bodies["+212612345678"] == "Bonjour Salma, 1 250,50 MAD dus le 05/11/2026."
    assert bodies["+212700112233"] == "Message perso"
    cid = r.json["campaign"]["id"]
    assert client.post(f"/api/v1/campaigns/{cid}/pause", headers=auth).json["status"] == "paused"
    assert client.post(f"/api/v1/campaigns/{cid}/pause", headers=auth).status_code == 409
    assert client.post(f"/api/v1/campaigns/{cid}/cancel", headers=auth).json["status"] == "canceled"


def _xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["Nom", "Prénom", "GSM", "Montant", "Date échéance", "Référence"])
    ws.append(["Alaoui", "Salma", "0612345678", 1250, datetime(2026, 11, 5), "FAC-1"])
    ws.append(["Tazi", "Omar", "0612345678", 300, datetime(2026, 11, 7), "FAC-2"])
    ws.append(["Fixe", "X", "0522000000", 1, datetime(2026, 11, 7), "FAC-3"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_upload_dry_run_then_send(client, auth, db):
    form = {"message": "Bonjour {prenom}, échéance {reference} : {montant} MAD le {date_echeance}",
            "dry_run": "true"}
    r = client.post("/api/v1/messages/upload", headers=auth, content_type="multipart/form-data",
                    data={**form, "file": (io.BytesIO(_xlsx()), "clients.xlsx")})
    assert r.status_code == 202, r.json
    assert r.json["summary"] == {"total": 3, "valid": 1, "invalid": 1, "duplicate": 1, "blacklisted": 0}
    assert r.json["preview"][0]["text"] == "Bonjour Salma, échéance FAC-1 : 1 250 MAD le 05/11/2026"
    assert r.json["campaign"] is None and db.session.query(Message).count() == 0
    report = client.get(r.json["error_report_url"], headers=auth)
    assert report.status_code == 200 and report.data[:2] == b"PK"

    form["dry_run"] = "false"
    form["save_contacts"] = "true"
    r = client.post("/api/v1/messages/upload", headers=auth, content_type="multipart/form-data",
                    data={**form, "file": (io.BytesIO(_xlsx()), "clients.xlsx")})
    assert r.json["campaign"]["total"] == 1
    contact = db.session.query(Contact).one()
    assert contact.first_name == "Salma" and contact.receivables[0].reference == "FAC-1"


def test_upload_bad_file(client, auth):
    r = client.post("/api/v1/messages/upload", headers=auth, content_type="multipart/form-data",
                    data={"file": (io.BytesIO(b"nope"), "x.pdf"), "message": "x"})
    assert r.status_code == 422


def test_contacts_crud_and_groups(client, auth, db):
    r = client.post("/api/v1/contacts", headers=auth, json={
        "phone": "0612345678", "first_name": "Salma", "groups": ["Clients"], "custom_fields": {"ville": "Rabat"}})
    assert r.status_code == 201, r.json
    cid = r.json["id"]
    assert r.json["phone"] == "+212612345678" and r.json["groups"] == ["Clients"]
    assert client.post("/api/v1/contacts", headers=auth, json={"phone": "+212612345678"}).status_code == 409
    r = client.patch(f"/api/v1/contacts/{cid}", headers=auth, json={"last_name": "Alaoui", "consent_status": "opted_out"})
    assert r.json["last_name"] == "Alaoui" and r.json["consent_status"] == "opted_out"
    assert client.get("/api/v1/contacts?q=alaoui", headers=auth).json["meta"]["total"] == 1
    groups = client.get("/api/v1/groups", headers=auth).json
    gid = groups[0]["id"]
    r = client.post(f"/api/v1/groups/{gid}/contacts", headers=auth, json={"contacts": ["0700112233"]})
    assert r.json["contacts_count"] == 2
    export = client.get(f"/api/v1/contacts/{cid}/export", headers=auth).json
    assert export["contact"]["phone"] == "+212612345678"
    assert client.delete(f"/api/v1/contacts/{cid}", headers=auth).status_code == 204
    assert client.get(f"/api/v1/contacts/{cid}", headers=auth).status_code == 404


def test_templates_and_schedules(client, auth):
    r = client.post("/api/v1/templates", headers=auth, json={
        "name": "Rappel", "category": "rappel", "language": "ar", "body": "مرحبا {prenom}"})
    assert r.status_code == 201 and r.json["variables"] == ["prenom"] and r.json["sms"]["encoding"] == "ucs2"
    r = client.post("/api/v1/schedules", headers=auth, json={
        "name": "Hebdo", "body": "Bonjour {prenom}", "audience": {"groups": [1]},
        "recurrence": "weekly", "run_time": "09:30", "days_of_week": [1, 4]})
    assert r.status_code == 201, r.json
    assert r.json["next_run_at"] is not None
    r = client.post("/api/v1/schedules", headers=auth, json={
        "name": "X", "body": "x", "audience": {}, "recurrence": "weekly", "run_time": "09:30"})
    assert r.status_code == 422


def test_devices_and_webhooks(client, auth, device):
    r = client.get("/api/v1/devices", headers=auth)
    assert r.json[0]["status"] == "online" and len(r.json[0]["sims"]) == 2
    r = client.post("/api/v1/webhooks", headers=auth, json={"url": "https://example.com/hook",
                                                            "events": ["sms.delivered", "sms.received"]})
    assert r.status_code == 201 and r.json["secret"].startswith("whsec_")
    assert "secret" not in client.get("/api/v1/webhooks", headers=auth).json[0]
    r = client.post("/api/v1/webhooks", headers=auth, json={"url": "https://x.com", "events": ["nope"]})
    assert r.status_code == 422
    assert client.delete(f"/api/v1/webhooks/{client.get('/api/v1/webhooks', headers=auth).json[0]['id']}",
                         headers=auth).status_code == 204


def test_openapi_and_404_format(client):
    spec = client.get("/api/openapi.json").json
    assert "/api/v1/messages/bulk" in spec["paths"]
    assert spec["components"]["securitySchemes"]["bearerAuth"]["scheme"] == "bearer"
    r = client.get("/api/v1/nope")
    assert r.status_code == 404 and r.json["error"]["code"] == "not_found"
