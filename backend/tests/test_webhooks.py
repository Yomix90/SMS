import json
from unittest import mock

import requests

from app.models import WebhookDelivery
from app.services import queue, webhooks
from app.services.security import hmac_sha256


class FakeResponse:
    def __init__(self, status):
        self.status_code = status
        self.text = "ok"


def _hook(db, events=("sms.sent", "sms.delivered", "sms.failed")):
    hook, secret = webhooks.create_webhook("http://hooks.example/receive", list(events))
    db.session.commit()
    return hook, secret


def test_event_creates_delivery_and_signature(db):
    hook, secret = _hook(db)
    msg = queue.enqueue("0612345678", "x")
    queue.set_status(msg, "sent")
    db.session.commit()
    delivery = db.session.query(WebhookDelivery).one()
    assert delivery.event == "sms.sent" and delivery.payload["data"]["id"] == str(msg.uuid)

    with mock.patch("app.services.webhooks.requests.post", return_value=FakeResponse(200)) as post:
        assert webhooks.deliver_pending() == 1
    kwargs = post.call_args.kwargs
    headers = kwargs["headers"]
    body = kwargs["data"].decode()
    expected = "sha256=" + hmac_sha256(secret, f"{headers['X-Webhook-Timestamp']}.{body}")
    assert headers["X-Webhook-Signature"] == expected
    assert json.loads(body)["event"] == "sms.sent"
    assert delivery.status == "success" and delivery.attempts == 1


def test_unsubscribed_event_ignored(db):
    _hook(db, events=("sms.received",))
    msg = queue.enqueue("0612345678", "x")
    queue.set_status(msg, "sent")
    db.session.commit()
    assert db.session.query(WebhookDelivery).count() == 0


def test_retry_backoff_then_failed(db):
    hook, _ = _hook(db)
    webhooks.emit_event("sms.failed", {"id": "x"})
    db.session.commit()
    delivery = db.session.query(WebhookDelivery).one()
    with mock.patch("app.services.webhooks.requests.post", side_effect=requests.ConnectionError("down")):
        for attempt in range(1, webhooks.MAX_ATTEMPTS + 1):
            webhooks.deliver_one(delivery)
            if attempt < webhooks.MAX_ATTEMPTS:
                assert delivery.status == "pending" and delivery.next_attempt_at is not None
    assert delivery.status == "failed" and hook.failure_count == webhooks.MAX_ATTEMPTS


def test_https_required_in_prod(app):
    app.config["WEBHOOK_ALLOW_HTTP"] = False
    import pytest
    with pytest.raises(webhooks.WebhookUrlError):
        webhooks.validate_url("http://example.com/x")
    with pytest.raises(webhooks.WebhookUrlError):
        webhooks.validate_url("https://127.0.0.1/x")
