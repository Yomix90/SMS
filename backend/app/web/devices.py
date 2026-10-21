"""Appareils : liste, appairage par QR code, réglages, réappairage, révocation."""
import sqlalchemy as sa
from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Device, Message, PairingCode
from ..models.message import DIR_IN, DIR_OUT
from ..services import devices as device_service
from ..services.audit import audit
from . import admin_required, bp
from .helpers import form_bool, form_int, get_or_404
from .i18n import translate as _


@bp.route("/devices")
def devices():
    items = db.session.query(Device).filter(Device.revoked_at.is_(None)).order_by(Device.name).all()
    revoked = db.session.query(Device).filter(Device.revoked_at.isnot(None)).order_by(Device.name).all()
    usage = {d.id: device_service.usage(d) for d in items}
    return render_template("devices/list.html", devices=items, revoked=revoked, usage=usage)


@bp.post("/devices/pair")
@admin_required
def device_pair():
    """Crée un code d'appairage (AJAX) : renvoie le QR SVG à afficher."""
    target = None
    if request.form.get("device_id"):
        target = get_or_404(Device, request.form["device_id"])
    name = (request.form.get("name") or "").strip()[:100] or None
    pc, code = device_service.create_pairing_code(current_user.id, name, device=target)
    db.session.commit()
    return jsonify({"pairing_id": pc.id, "svg": device_service.pairing_qr_svg(code),
                    "payload": device_service.pairing_payload(code),
                    "expires_at": pc.expires_at.isoformat(), "status_url": url_for("web.device_pair_status", pid=pc.id)})


@bp.get("/devices/pair/<int:pid>")
@admin_required
def device_pair_status(pid):
    pc = db.session.get(PairingCode, pid)
    if pc is None:
        return jsonify({"state": "unknown"}), 404
    if pc.used_at:
        device = db.session.get(Device, pc.device_id)
        return jsonify({"state": "paired", "url": url_for("web.device_detail", uid=device.uuid),
                        "name": device.name})
    return jsonify({"state": "waiting" if pc.is_usable else "expired"})


@bp.route("/devices/<uid>", methods=["GET", "POST"])
def device_detail(uid):
    device = get_or_404(Device, uid)
    if request.method == "POST":
        if not current_user.is_admin:
            flash(_("Action réservée aux administrateurs."), "danger")
            return redirect(url_for("web.device_detail", uid=uid))
        device.name = (request.form.get("name") or device.name).strip()[:100]
        device.default_sim_slot = form_int("default_sim_slot", device.default_sim_slot, 0, 1)
        device.send_interval_sec = form_int("send_interval_sec", device.send_interval_sec, 1, 3600)
        device.max_per_hour = form_int("max_per_hour", device.max_per_hour, 1, 10000)
        device.max_per_day = form_int("max_per_day", device.max_per_day, 1, 100000)
        device.is_enabled = form_bool("is_enabled")
        if not device.is_enabled and device.status == "online":
            device.status = "disabled"
            device_service.requeue_unsent(device, include_acked=False)
        elif device.is_enabled and device.status == "disabled":
            device.status = "offline"
        device_service.push_config(device)
        audit("device.update", device, {"limits": [device.send_interval_sec, device.max_per_hour, device.max_per_day]})
        db.session.commit()
        flash(_("Réglages enregistrés et envoyés au téléphone."), "success")
        return redirect(url_for("web.device_detail", uid=uid))

    counts = dict(db.session.query(Message.status, sa.func.count(Message.id))
                  .filter(Message.device_id == device.id, Message.direction == DIR_OUT)
                  .group_by(Message.status).all())
    received = db.session.query(Message).filter_by(device_id=device.id, direction=DIR_IN).count()
    recent = (db.session.query(Message).filter(Message.device_id == device.id)
              .order_by(Message.created_at.desc()).limit(15).all())
    return render_template("devices/detail.html", device=device, usage=device_service.usage(device),
                           counts=counts, received=received, recent=recent)


@bp.post("/devices/<uid>/revoke")
@admin_required
def device_revoke(uid):
    device = get_or_404(Device, uid)
    device_service.revoke_device(device)
    db.session.commit()
    flash(_("Appareil révoqué : il ne peut plus se connecter."), "warning")
    return redirect(url_for("web.devices"))
