from app.models import Blacklist, Contact, Device, KeywordRule, Message
from app.services import devices, dispatcher, queue
from app.services.settings import set_setting


def test_pairing_flow(client, db):
    _pc, code = devices.create_pairing_code(device_name="Agence Casa")
    db.session.commit()
    r = client.post("/device/v1/pair", json={"code": code, "model": "Redmi 12", "android_version": "14",
                                             "sims": [{"slot": 0, "carrier": "IAM", "phone_number": "+212600000001"}]})
    assert r.status_code == 201, r.json
    token = r.json["token"]
    assert token.startswith("dev_") and r.json["device"]["name"] == "Agence Casa"
    device = db.session.query(Device).one()
    assert device.sims[0].carrier == "IAM" and device.token_hash != token
    # Le code est à usage unique
    assert client.post("/device/v1/pair", json={"code": code}).status_code == 400
    # Token valide
    r = client.post("/device/v1/heartbeat", json={"battery": 55, "charging": True},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    db.session.refresh(device)
    assert device.status == "online" and device.battery_level == 55 and device.is_charging


def test_invalid_token(client):
    assert client.get("/device/v1/tasks", headers={"Authorization": "Bearer dev_nope"}).status_code == 401
    assert client.get("/device/v1/tasks").json["error"]["code"] == "missing_token"


def test_polling_ack_and_status(client, db, device, device_headers):
    set_setting("send_window", {"enabled": False})
    msg = queue.enqueue("0612345678", "Bonjour")
    db.session.commit()
    dispatcher.tick()
    r = client.get("/device/v1/tasks", headers=device_headers)
    tasks = r.json["messages"]
    assert len(tasks) == 1 and tasks[0]["phone"] == "+212612345678" and tasks[0]["sim_slot"] == 0
    assert client.post("/device/v1/tasks/ack", json={"ids": [tasks[0]["id"]]}, headers=device_headers).json["acked"] == 1
    assert client.get("/device/v1/tasks", headers=device_headers).json["messages"] == []
    r = client.post("/device/v1/status", headers=device_headers, json={"statuses": [
        {"id": tasks[0]["id"], "status": "sent"}, {"id": tasks[0]["id"], "status": "delivered"},
        {"id": "00000000-0000-0000-0000-000000000000", "status": "sent"}]})
    assert r.json["applied"] == 2 and len(r.json["received"]) == 3
    db.session.refresh(msg)
    assert msg.status == "delivered"


def test_device_cannot_touch_other_device_messages(client, db, device, device_headers):
    other_pc, code = devices.create_pairing_code()
    db.session.commit()
    other, _ = devices.pair_device(code, {})
    msg = queue.enqueue("0612345678", "x", device=other)
    msg.status = "dispatched"
    db.session.commit()
    r = client.post("/device/v1/status", headers=device_headers, json={"statuses": [
        {"id": str(msg.uuid), "status": "sent"}]})
    assert r.json["applied"] == 0


def test_failure_report_retries(client, db, device, device_headers):
    set_setting("send_window", {"enabled": False})
    msg = queue.enqueue("0612345678", "x")
    db.session.commit()
    dispatcher.tick()
    client.post("/device/v1/status", headers=device_headers, json={"statuses": [
        {"id": str(msg.uuid), "status": "failed", "error_code": "RADIO_OFF"}]})
    db.session.refresh(msg)
    assert msg.status == "queued" and msg.error_code == "RADIO_OFF" and msg.attempts == 1


def test_inbound_and_dedupe(client, db, device, device_headers, emits):
    payload = {"messages": [{"uuid": "local-1", "phone": "0612345678", "body": "Bonjour",
                             "received_at": 1760000000000, "sim_slot": 1}]}
    assert client.post("/device/v1/inbound", json=payload, headers=device_headers).json["received"] == ["local-1"]
    client.post("/device/v1/inbound", json=payload, headers=device_headers)
    msgs = db.session.query(Message).filter_by(direction="in").all()
    assert len(msgs) == 1 and msgs[0].phone == "+212612345678" and msgs[0].sim_slot == 1
    assert msgs[0].conversation.unread_count == 1
    assert any(e["event"] == "inbox.new" for e in emits)


def test_stop_keyword_opt_out(client, db, device, device_headers):
    db.session.add(Contact(phone="+212612345678", first_name="Salma", consent_status="opt_in"))
    pending = queue.enqueue("0612345678", "Promo")
    db.session.commit()
    client.post("/device/v1/inbound", headers=device_headers,
                json={"messages": [{"uuid": "s1", "phone": "+212612345678", "body": " stop. "}]})
    assert db.session.query(Blacklist).filter_by(phone="+212612345678").count() == 1
    contact = db.session.query(Contact).one()
    assert contact.consent_status == "opted_out"
    db.session.refresh(pending)
    assert pending.status == "blocked"
    confirmation = db.session.query(Message).filter_by(source="auto_reply").one()
    assert confirmation.status == "queued" and "désinscrit" in confirmation.body
    assert confirmation.device_pinned and confirmation.ignore_window


def test_arabic_stop(client, db, device, device_headers):
    client.post("/device/v1/inbound", headers=device_headers,
                json={"messages": [{"uuid": "s2", "phone": "0700112233", "body": "توقف"}]})
    assert db.session.query(Blacklist).count() == 1


def test_keyword_auto_reply_with_cooldown(client, db, device, device_headers):
    db.session.add(KeywordRule(keywords=["INFO"], match_type="exact", reply_body="Horaires : 9h-18h",
                               cooldown_minutes=60))
    db.session.commit()
    for i in range(2):
        client.post("/device/v1/inbound", headers=device_headers,
                    json={"messages": [{"uuid": f"k{i}", "phone": "0612345678", "body": "info"}]})
    replies = db.session.query(Message).filter_by(source="auto_reply").all()
    assert len(replies) == 1 and replies[0].body == "Horaires : 9h-18h"


def test_offline_detection_requeues(db, device, app, emits):
    from datetime import timedelta

    from app.models import utcnow
    set_setting("send_window", {"enabled": False})
    dev, _ = device
    msg = queue.enqueue("0612345678", "x")
    db.session.commit()
    dispatcher.tick()
    dev.last_seen_at = utcnow() - timedelta(seconds=app.config["DEVICE_OFFLINE_AFTER_SEC"] + 10)
    db.session.commit()
    stale = devices.mark_offline_devices()
    db.session.commit()
    assert stale == [dev] and dev.status == "offline"
    db.session.refresh(msg)
    assert msg.status == "queued" and msg.device_id is None


def test_revoke_device(client, db, device, device_headers):
    dev, _ = device
    devices.revoke_device(dev)
    db.session.commit()
    assert client.get("/device/v1/config", headers=device_headers).status_code == 401
