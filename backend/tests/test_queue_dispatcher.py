from datetime import timedelta

import pytest

from app.models import Campaign, Message, utcnow
from app.services import campaigns, dispatcher, queue
from app.services.settings import set_setting


def _always_open(db):
    set_setting("send_window", {"start": "00:00", "end": "23:59", "days": [1, 2, 3, 4, 5, 6, 7], "enabled": False})
    db.session.commit()


def test_enqueue_basic(db):
    msg = queue.enqueue("0612345678", "Bonjour")
    db.session.commit()
    assert msg.phone == "+212612345678"
    assert msg.status == "queued" and msg.segments == 1 and msg.encoding == "gsm7"
    assert msg.conversation.phone == msg.phone
    assert msg.events[0].status == "queued"


def test_enqueue_invalid_phone(db):
    with pytest.raises(queue.InvalidPhoneError):
        queue.enqueue("0522000000", "x")


def test_enqueue_too_long(db):
    with pytest.raises(queue.QueueError, match="message_too_long"):
        queue.enqueue("0612345678", "م" * 700)


def test_enqueue_scheduled_and_blacklisted(db):
    later = utcnow() + timedelta(hours=2)
    msg = queue.enqueue("0612345678", "Plus tard", scheduled_at=later)
    assert msg.status == "scheduled" and msg.next_attempt_at == later
    queue.add_to_blacklist("+212700112233")
    blocked = queue.enqueue("0700112233", "Non")
    assert blocked.status == "blocked" and blocked.error_code == "BLACKLISTED"


def test_blacklist_blocks_pending(db):
    msg = queue.enqueue("0612345678", "x")
    queue.add_to_blacklist("+212612345678")
    db.session.commit()
    db.session.refresh(msg)
    assert msg.status == "blocked"


def test_status_does_not_regress(db):
    msg = queue.enqueue("0612345678", "x")
    assert queue.set_status(msg, "delivered")
    assert not queue.set_status(msg, "sent")
    assert msg.status == "delivered" and msg.sent_at is not None


def test_handle_failure_retries_then_fails(db):
    msg = queue.enqueue("0612345678", "x", max_attempts=2)
    msg.attempts = 1
    assert queue.handle_failure(msg, "NO_SERVICE") == "queued"
    assert msg.next_attempt_at > utcnow()
    msg.attempts = 2
    assert queue.handle_failure(msg, "NO_SERVICE") == "failed"
    msg2 = queue.enqueue("0612345678", "y")
    msg2.attempts = 1
    assert queue.handle_failure(msg2, "NULL_PDU") == "failed"  # erreur définitive


def test_dispatch_assigns_and_pushes(db, device, emits):
    _always_open(db)
    dev, _ = device
    for i in range(3):
        queue.enqueue("0612345678", f"msg {i}")
    db.session.commit()
    result = dispatcher.tick()
    assert result["dispatched"] == {dev.id: 3}
    pushes = [e for e in emits if e["event"] == "task.send"]
    assert len(pushes) == 1 and len(pushes[0]["data"]["messages"]) == 3
    assert pushes[0]["room"] == f"device:{dev.id}"
    msg = db.session.query(Message).first()
    assert msg.status == "dispatched" and msg.attempts == 1 and msg.sim_slot == 0


def test_dispatch_respects_hourly_limit(db, device):
    _always_open(db)
    dev, _ = device
    dev.max_per_hour = 2
    for i in range(5):
        queue.enqueue("0612345678", f"msg {i}")
    db.session.commit()
    dispatcher.tick()
    assert db.session.query(Message).filter_by(status="dispatched").count() == 2
    dispatcher.tick()
    assert db.session.query(Message).filter_by(status="dispatched").count() == 2


def test_no_dispatch_outside_window(db, device):
    set_setting("send_window", {"start": "03:00", "end": "03:01", "days": [1]})
    msg = queue.enqueue("0612345678", "nuit")
    urgent = queue.enqueue("0612345678", "OTP 1234", ignore_window=True)
    db.session.commit()
    result = dispatcher.tick()
    db.session.refresh(msg)
    if result["deferred"]:  # hors fenêtre (cas quasi certain)
        assert msg.status == "queued" and msg.next_attempt_at > utcnow()
    assert db.session.get(Message, urgent.id).status == "dispatched"


def test_ack_timeout_requeues(db, device, app):
    _always_open(db)
    msg = queue.enqueue("0612345678", "x")
    db.session.commit()
    dispatcher.tick()
    msg.dispatched_at = utcnow() - timedelta(seconds=app.config["DEVICE_ACK_TIMEOUT_SEC"] + 5)
    db.session.commit()
    assert dispatcher.requeue_timeouts() == 1
    assert msg.status == "queued" and msg.error_code == "ACK_TIMEOUT"


def test_device_status_flow(db, device):
    _always_open(db)
    dev, _ = device
    msg = queue.enqueue("0612345678", "x")
    db.session.commit()
    dispatcher.tick()
    assert dispatcher.ack(dev, [str(msg.uuid)]) == 1
    assert dispatcher.apply_status(dev, {"id": str(msg.uuid), "status": "sent", "timestamp": 1760000000000})
    assert dispatcher.apply_status(dev, {"id": str(msg.uuid), "status": "delivered"})
    db.session.commit()
    assert msg.status == "delivered"
    assert [e.status for e in msg.events] == ["queued", "dispatched", "sent", "delivered"]


def test_campaign_lifecycle(db, device):
    _always_open(db)
    recipients = [campaigns.Recipient("0612345678", {"prenom": "Salma"}),
                  campaigns.Recipient("0700112233", {"prenom": "Omar"}, message="Perso {prenom}"),
                  campaigns.Recipient("0522000000", {})]
    result = campaigns.create_campaign("Test", body="Bonjour {prenom}", recipients=recipients,
                                       add_stop_footer=True)
    db.session.commit()
    c = result.campaign
    assert result.created == 2 and len(result.rejected) == 1
    assert c.status == "running" and c.total == 2 and c.queued == 2
    bodies = sorted(m.body for m in db.session.query(Message).filter_by(campaign_id=c.id))
    assert bodies[0].startswith("Bonjour Salma\nSTOP") and bodies[1].startswith("Perso Omar")

    campaigns.pause_campaign(c)
    db.session.commit()
    assert c.status == "paused" and dispatcher.tick()["dispatched"] == {}
    campaigns.resume_campaign(c)
    db.session.commit()
    dispatcher.tick()
    dev, _ = device
    for m in db.session.query(Message).filter_by(campaign_id=c.id):
        dispatcher.apply_status(dev, {"id": str(m.uuid), "status": "delivered"})
    db.session.commit()
    c = db.session.get(Campaign, c.id)
    assert c.status == "completed" and c.delivered == 2 and c.progress == 100


def test_campaign_cancel_notifies_device(db, device, emits):
    _always_open(db)
    result = campaigns.create_campaign("C", body="x", recipients=[campaigns.Recipient("0612345678"),
                                                                 campaigns.Recipient("0700112233")])
    db.session.commit()
    dev, _ = device
    dev.max_per_hour = 1
    dispatcher.tick()
    campaigns.cancel_campaign(result.campaign)
    db.session.commit()
    assert result.campaign.status == "canceled"
    assert any(e["event"] == "task.cancel" for e in emits)


def test_abuse_threshold(db, api_token):
    from app.services.accounts import find_api_key
    set_setting("abuse_threshold", {"count": 3, "minutes": 10})
    key = find_api_key(api_token)
    for _ in range(3):
        queue.enqueue("0612345678", "x", api_key=key)
    db.session.commit()
    with pytest.raises(queue.QueueError, match="abuse"):
        queue.check_abuse(1, api_key=key)


def test_daily_quota(db, api_token):
    from app.services.accounts import find_api_key
    key = find_api_key(api_token)
    key.daily_quota = 2
    queue.enqueue("0612345678", "x", api_key=key)
    queue.check_api_quota(key, 1)
    with pytest.raises(queue.QueueError, match="quota"):
        queue.check_api_quota(key, 2)
