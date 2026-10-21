"""Boîte de réception : conversations par contact, réponse directe, étiquettes, recherche, temps réel."""
import sqlalchemy as sa
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..extensions import db
from ..models import Contact, Conversation, Label, Message
from ..models.message import DIR_IN
from ..services import queue
from . import bp, flash_error
from .helpers import form_int, get_or_404
from .i18n import translate as _
from .messages import active_devices


def _conversations():
    a = request.args
    q = (db.session.query(Conversation).outerjoin(Contact, Conversation.contact_id == Contact.id)
         .filter(Conversation.last_message_at.isnot(None)))
    view = a.get("view", "all")
    if view == "unread":
        q = q.filter(Conversation.unread_count > 0)
    elif view == "archived":
        q = q.filter(Conversation.is_archived.is_(True))
    else:
        q = q.filter(Conversation.is_archived.is_(False))
    if a.get("label", "").isdigit():
        q = q.filter(Conversation.labels.any(Label.id == int(a["label"])))
    if a.get("q"):
        term = f"%{a['q'].strip()}%"
        digits = a["q"].replace(" ", "").lstrip("0+")
        body_match = sa.select(Message.conversation_id).where(Message.body.ilike(term))
        q = q.filter(sa.or_(Contact.first_name.ilike(term), Contact.last_name.ilike(term),
                            Conversation.phone.contains(digits) if digits else sa.false(),
                            Conversation.id.in_(body_match)))
    return q.order_by(Conversation.last_message_at.desc()).limit(100).all()


def _thread(conv: Conversation):
    msgs = (db.session.query(Message).filter_by(conversation_id=conv.id)
            .order_by(Message.created_at.desc()).limit(200).all())
    msgs.reverse()
    return msgs


def _mark_read(conv: Conversation) -> None:
    if conv.unread_count:
        conv.unread_count = 0
        db.session.query(Message).filter_by(conversation_id=conv.id, direction=DIR_IN, is_read=False) \
            .update({Message.is_read: True}, synchronize_session=False)
        db.session.commit()


@bp.route("/inbox")
def inbox():
    conv = None
    if request.args.get("c", "").isdigit():
        conv = db.session.get(Conversation, int(request.args["c"]))
        if conv is not None:
            _mark_read(conv)
    conversations = _conversations()
    last_in = None
    if conv is not None:
        last_in = (db.session.query(Message).filter_by(conversation_id=conv.id, direction=DIR_IN)
                   .order_by(Message.created_at.desc()).first())
    return render_template("inbox/index.html", conversations=conversations, conv=conv,
                           thread=_thread(conv) if conv else [], labels=db.session.query(Label).order_by(Label.name).all(),
                           devices=active_devices(), last_in=last_in)


@bp.get("/inbox/list")
def inbox_list_partial():
    current = request.args.get("c", type=int)
    return render_template("inbox/_list.html", conversations=_conversations(), current=current)


@bp.get("/inbox/<int:cid>/thread")
def inbox_thread_partial(cid):
    conv = db.session.get(Conversation, cid)
    if conv is None:
        return "", 404
    _mark_read(conv)
    return render_template("inbox/_thread.html", thread=_thread(conv))


@bp.post("/inbox/<int:cid>/reply")
def inbox_reply(cid):
    conv = db.session.get(Conversation, cid)
    if conv is None:
        return redirect(url_for("web.inbox"))
    device_uid = request.form.get("device_id")
    from ..models import Device
    try:
        queue.enqueue(conv.phone, request.form.get("body", ""), contact=conv.contact,
                      device=get_or_404(Device, device_uid) if device_uid else None,
                      sim_slot=form_int("sim_slot", None, 0, 1), priority=8, source="web", user_id=current_user.id,
                      ignore_window=True)
    except queue.QueueError as exc:
        db.session.rollback()
        flash_error(exc)
        return redirect(url_for("web.inbox", c=cid))
    db.session.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return render_template("inbox/_thread.html", thread=_thread(conv))
    return redirect(url_for("web.inbox", c=cid))


@bp.post("/inbox/<int:cid>/labels")
def inbox_labels(cid):
    conv = db.session.get(Conversation, cid)
    if conv is not None:
        ids = [int(i) for i in request.form.getlist("labels") if i.isdigit()]
        conv.labels = db.session.query(Label).filter(Label.id.in_(ids)).all() if ids else []
        db.session.commit()
    return redirect(url_for("web.inbox", c=cid))


@bp.post("/inbox/<int:cid>/archive")
def inbox_archive(cid):
    conv = db.session.get(Conversation, cid)
    if conv is not None:
        conv.is_archived = not conv.is_archived
        db.session.commit()
        flash(_("Conversation archivée.") if conv.is_archived else _("Conversation restaurée."), "success")
    return redirect(url_for("web.inbox"))


@bp.post("/inbox/<int:cid>/unread")
def inbox_unread(cid):
    conv = db.session.get(Conversation, cid)
    if conv is not None:
        conv.unread_count = max(conv.unread_count, 1)
        db.session.commit()
    return redirect(url_for("web.inbox"))


@bp.post("/labels")
def label_create():
    name = request.form.get("name", "").strip()[:60]
    if name and not db.session.query(Label).filter_by(name=name).first():
        db.session.add(Label(name=name, color=request.form.get("color") or "#6c757d"))
        db.session.commit()
    return redirect(request.referrer or url_for("web.inbox"))


@bp.post("/labels/<int:lid>/delete")
def label_delete(lid):
    label = db.session.get(Label, lid)
    if label is not None:
        db.session.delete(label)
        db.session.commit()
    return redirect(url_for("web.inbox"))
