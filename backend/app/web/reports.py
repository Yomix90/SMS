"""Rapports : indicateurs, graphiques, exports Excel / PDF."""
import io
from datetime import date, timedelta

from flask import render_template, request, send_file

from ..extensions import db
from ..models import Campaign, Device
from ..services.reports import (STATUS_LABELS, MessageFilters, daily_series, export_filename, export_pdf,
                                export_xlsx, per_device, summary)
from ..services.timeutil import local_today
from . import bp
from .helpers import lang
from .messages import _uuid


@bp.route("/reports")
def reports():
    a = request.args

    def _d(key, default):
        try:
            return date.fromisoformat(a[key]) if a.get(key) else default
        except ValueError:
            return default
    today = local_today()
    f = MessageFilters(date_from=_d("from", today - timedelta(days=29)), date_to=_d("to", today))
    if a.get("device"):
        d = db.session.query(Device).filter_by(uuid=_uuid(a["device"])).one_or_none()
        f.device_id = d.id if d else None
    if a.get("campaign"):
        c = db.session.query(Campaign).filter_by(uuid=_uuid(a["campaign"])).one_or_none()
        f.campaign_id = c.id if c else None
    if a.get("export") == "xlsx":
        return send_file(io.BytesIO(export_xlsx(f, lang())), as_attachment=True, download_name=export_filename("xlsx"),
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if a.get("export") == "pdf":
        return send_file(io.BytesIO(export_pdf(f, lang())), as_attachment=True, download_name=export_filename("pdf"),
                         mimetype="application/pdf")
    stats = summary(f)
    labels = STATUS_LABELS.get(lang(), STATUS_LABELS["fr"])
    by_status = [{"key": k, "label": labels.get(k, k), "value": v} for k, v in sorted(stats["by_status"].items())]
    return render_template("reports.html", f=f, stats=stats, series=daily_series(f=f), by_status=by_status,
                           devices_stats=per_device(f), devices=db.session.query(Device).order_by(Device.name).all(),
                           campaigns=db.session.query(Campaign).order_by(Campaign.created_at.desc()).limit(100).all())
