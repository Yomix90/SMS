"""SMS reçus."""
from flask_smorest import Blueprint

from ..extensions import db
from ..models import Message
from ..models.message import DIR_IN
from ..services.timeutil import ensure_utc
from . import paginate
from .auth import require_api_key
from .schemas import InboxArgs, MessagePageSchema

blp = Blueprint("inbox", __name__, url_prefix="/inbox", description="SMS reçus")
BLUEPRINTS = (blp,)


@blp.route("")
@require_api_key("inbox:read")
@blp.arguments(InboxArgs, location="query")
@blp.response(200, MessagePageSchema)
def list_inbox(args):
    """Lister les SMS reçus

    `since` permet une synchronisation incrémentale (messages reçus après cette date).
    """
    q = db.session.query(Message).filter(Message.direction == DIR_IN)
    if args["phone"]:
        q = q.filter(Message.phone.contains(args["phone"].replace(" ", "")))
    if args["since"]:
        q = q.filter(Message.received_at > ensure_utc(args["since"]))
    if args["unread"] is not None:
        q = q.filter(Message.is_read.is_(not args["unread"]))
    items, meta = paginate(q.order_by(Message.received_at.desc()), args["page"], args["per_page"])
    return {"data": items, "meta": meta}
