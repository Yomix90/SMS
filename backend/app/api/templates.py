"""Modèles de messages et planifications."""
from flask import g
from flask.views import MethodView
from flask_smorest import Blueprint

from ..extensions import db
from ..models import Device, Schedule, Template
from ..services.audit import audit
from ..services.schedules import compute_next_run
from ..services.templating import extract_variables
from . import get_by_uuid, paginate
from .auth import require_api_key
from .errors import ApiError
from .schemas import PageArgs, ScheduleSchema, TemplateListArgs, TemplateSchema

blp = Blueprint("templates", __name__, url_prefix="/templates", description="Modèles de messages (FR/AR)")
schedules_blp = Blueprint("schedules", __name__, url_prefix="/schedules", description="Envois planifiés")
BLUEPRINTS = (blp, schedules_blp)


@blp.route("")
class Templates(MethodView):
    @require_api_key("templates:read")
    @blp.arguments(TemplateListArgs, location="query")
    @blp.response(200, TemplateSchema(many=True))
    def get(self, args):
        """Lister les modèles"""
        q = db.session.query(Template).filter(Template.is_active.is_(True))
        if args["category"]:
            q = q.filter(Template.category == args["category"])
        if args["language"]:
            q = q.filter(Template.language == args["language"])
        items, _ = paginate(q.order_by(Template.category, Template.name), args["page"], args["per_page"])
        return items

    @require_api_key("templates:write")
    @blp.arguments(TemplateSchema)
    @blp.response(201, TemplateSchema)
    def post(self, data):
        """Créer un modèle"""
        if db.session.query(Template.id).filter_by(name=data["name"], language=data["language"]).first():
            raise ApiError(409, "template_exists", "Un modèle porte déjà ce nom dans cette langue")
        tpl = Template(**data, variables=extract_variables(data["body"]))
        db.session.add(tpl)
        db.session.commit()
        return tpl


@blp.route("/<uuid:template_id>")
@require_api_key("templates:read")
@blp.response(200, TemplateSchema)
def get_template(template_id):
    """Détail d'un modèle"""
    return get_by_uuid(Template, template_id, "Modèle")


@schedules_blp.route("")
class Schedules(MethodView):
    @require_api_key("schedules:read")
    @schedules_blp.arguments(PageArgs, location="query")
    @schedules_blp.response(200, ScheduleSchema(many=True))
    def get(self, args):
        """Lister les planifications"""
        items, _ = paginate(db.session.query(Schedule).order_by(Schedule.next_run_at), args["page"],
                            args["per_page"])
        return items

    @require_api_key("schedules:write")
    @schedules_blp.arguments(ScheduleSchema)
    @schedules_blp.response(201, ScheduleSchema)
    def post(self, data):
        """Créer une planification (unique ou récurrente)"""
        template_id = data.pop("template_id")
        device_id = data.pop("device_id")
        schedule = Schedule(**data)
        if template_id:
            schedule.template_id = get_by_uuid(Template, template_id, "Modèle").id
        if device_id:
            schedule.device_id = get_by_uuid(Device, device_id, "Appareil").id
        schedule.next_run_at = compute_next_run(schedule)
        if schedule.next_run_at is None:
            raise ApiError(422, "no_future_run", "Aucune exécution future avec ces paramètres")
        db.session.add(schedule)
        db.session.flush()
        audit("schedule.create", schedule, {"via": "api"}, api_key_id=g.api_key.id)
        db.session.commit()
        return schedule


@schedules_blp.route("/<uuid:schedule_id>")
class ScheduleItem(MethodView):
    @require_api_key("schedules:read")
    @schedules_blp.response(200, ScheduleSchema)
    def get(self, schedule_id):
        """Détail d'une planification"""
        return get_by_uuid(Schedule, schedule_id, "Planification")

    @require_api_key("schedules:write")
    @schedules_blp.response(204)
    def delete(self, schedule_id):
        """Désactiver une planification"""
        schedule = get_by_uuid(Schedule, schedule_id, "Planification")
        schedule.is_active = False
        schedule.next_run_at = None
        db.session.commit()
