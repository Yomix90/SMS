from app.extensions import socketio
from app.models import Message
from app.services import dispatcher, queue
from app.services.settings import set_setting


def _client(app, token):
    return socketio.test_client(app, namespace="/device", auth={"token": token})


def test_refuses_bad_token(app):
    client = _client(app, "dev_invalid")
    assert not client.is_connected("/device")


def test_device_socket_flow(app, db, device):
    app.config["SOCKETIO_IN_TESTS"] = True  # émissions réelles via Socket.IO
    set_setting("send_window", {"enabled": False})
    dev, token = device
    client = _client(app, token)
    assert client.is_connected("/device")
    received = client.get_received("/device")
    assert received[0]["name"] == "config.update" and received[0]["args"][0]["max_per_hour"] == 100

    ack = client.emit("heartbeat", {"battery": 42, "charging": False}, namespace="/device", callback=True)
    assert ack == {"ok": True}
    db.session.refresh(dev)
    assert dev.battery_level == 42

    msg = queue.enqueue("0612345678", "Socket")
    db.session.commit()
    dispatcher.tick()
    pushed = [e for e in client.get_received("/device") if e["name"] == "task.send"]
    assert pushed and pushed[0]["args"][0]["messages"][0]["id"] == str(msg.uuid)

    assert client.emit("task.ack", {"ids": [str(msg.uuid)]}, namespace="/device", callback=True)["acked"] == 1
    res = client.emit("sms.status", {"statuses": [{"id": str(msg.uuid), "status": "delivered"}]},
                      namespace="/device", callback=True)
    assert res["ok"] and res["received"] == [str(msg.uuid)]
    res = client.emit("sms.received", {"messages": [{"uuid": "x1", "phone": "0700112233", "body": "Salut"}]},
                      namespace="/device", callback=True)
    assert res["received"] == ["x1"]
    db.session.expire_all()
    assert db.session.get(Message, msg.id).status == "delivered"
    assert db.session.query(Message).filter_by(direction="in").count() == 1
    client.disconnect("/device")
