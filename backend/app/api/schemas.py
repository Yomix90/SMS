"""Schémas marshmallow : validation des entrées et documentation OpenAPI."""
from marshmallow import EXCLUDE, Schema, ValidationError, fields, validate, validates_schema

from ..models.automation import RECURRENCES
from ..models.webhook import WEBHOOK_EVENTS


class BaseSchema(Schema):
    class Meta:
        unknown = EXCLUDE
        ordered = True


def _uuid_of(attr):
    """Sérialise l'UUID public d'une relation (ex. message.campaign -> uuid)."""
    def getter(obj):
        rel = getattr(obj, attr, None)
        return str(rel.uuid) if rel is not None else None
    return getter


# --- Pagination ------------------------------------------------------------------

class PageArgs(BaseSchema):
    page = fields.Integer(load_default=1, validate=validate.Range(min=1))
    per_page = fields.Integer(load_default=50, validate=validate.Range(min=1, max=200))


class MetaSchema(BaseSchema):
    page = fields.Integer()
    per_page = fields.Integer()
    total = fields.Integer()
    pages = fields.Integer()


# --- Messages --------------------------------------------------------------------

class MessageInSchema(BaseSchema):
    to = fields.String(required=True, validate=validate.Length(min=1, max=30),
                       metadata={"description": "Numéro du destinataire (06…, +212…)", "example": "0612345678"})
    message = fields.String(required=True, validate=validate.Length(min=1, max=1600),
                            metadata={"example": "Bonjour, votre commande est prête."})
    device_id = fields.UUID(load_default=None, metadata={"description": "Appareil imposé (sinon automatique)"})
    sim_slot = fields.Integer(load_default=None, validate=validate.Range(min=0, max=1))
    scheduled_at = fields.DateTime(load_default=None,
                                   metadata={"description": "ISO 8601 ; sans fuseau = heure de Casablanca"})
    priority = fields.Integer(load_default=0, validate=validate.Range(min=0, max=10))
    external_ref = fields.String(load_default=None, validate=validate.Length(max=100))
    ignore_window = fields.Boolean(load_default=False,
                                   metadata={"description": "SMS transactionnel hors fenêtre horaire (clé autorisée)"})


class MessageCreateSchema(MessageInSchema):
    """Envoi unique (champs à la racine) ou lot (`messages`)."""
    to = fields.String(load_default=None, validate=validate.Length(min=1, max=30))
    message = fields.String(load_default=None, validate=validate.Length(min=1, max=1600))
    messages = fields.List(fields.Nested(MessageInSchema), load_default=None,
                           validate=validate.Length(min=1, max=1000))

    @validates_schema
    def _single_or_batch(self, data, **kwargs):
        if data.get("messages"):
            return
        if not data.get("to") or not data.get("message"):
            raise ValidationError("Fournir « to » et « message », ou une liste « messages »", "_schema")


class MessageOutSchema(BaseSchema):
    id = fields.UUID(attribute="uuid", dump_only=True)
    direction = fields.String()
    phone = fields.String()
    body = fields.String()
    status = fields.String()
    encoding = fields.String()
    segments = fields.Integer()
    priority = fields.Integer()
    source = fields.String()
    campaign_id = fields.Function(_uuid_of("campaign"))
    device_id = fields.Function(_uuid_of("device"))
    sim_slot = fields.Integer(allow_none=True)
    attempts = fields.Integer()
    error_code = fields.String(allow_none=True)
    error_message = fields.String(allow_none=True)
    external_ref = fields.String(allow_none=True)
    scheduled_at = fields.DateTime(allow_none=True)
    sent_at = fields.DateTime(allow_none=True)
    delivered_at = fields.DateTime(allow_none=True)
    failed_at = fields.DateTime(allow_none=True)
    received_at = fields.DateTime(allow_none=True)
    created_at = fields.DateTime()


class MessageEventOutSchema(BaseSchema):
    status = fields.String()
    error_code = fields.String(allow_none=True)
    details = fields.Dict(allow_none=True)
    created_at = fields.DateTime()


class MessageDetailSchema(MessageOutSchema):
    events = fields.List(fields.Nested(MessageEventOutSchema))


class RejectedSchema(BaseSchema):
    index = fields.Integer()
    phone = fields.String()
    error = fields.String()
    message = fields.String()


class MessageBatchResultSchema(BaseSchema):
    data = fields.List(fields.Nested(MessageOutSchema))
    rejected = fields.List(fields.Nested(RejectedSchema))


class MessageListArgs(PageArgs):
    status = fields.String(load_default=None, metadata={"description": "Un ou plusieurs statuts séparés par des virgules"})
    direction = fields.String(load_default=None, validate=validate.OneOf(["out", "in"]))
    phone = fields.String(load_default=None)
    campaign_id = fields.UUID(load_default=None)
    device_id = fields.UUID(load_default=None)
    external_ref = fields.String(load_default=None)
    date_from = fields.Date(load_default=None)
    date_to = fields.Date(load_default=None)


class MessagePageSchema(BaseSchema):
    data = fields.List(fields.Nested(MessageOutSchema))
    meta = fields.Nested(MetaSchema)


class RecipientSchema(BaseSchema):
    phone = fields.String(required=True, validate=validate.Length(min=6, max=30))
    variables = fields.Dict(keys=fields.String(), load_default=dict,
                            metadata={"example": {"nom": "Alaoui", "montant": 1250, "date_echeance": "2026-11-05"}})
    message = fields.String(load_default=None, validate=validate.Length(max=1600),
                            metadata={"description": "Message spécifique à ce destinataire (remplace le texte commun)"})
    external_ref = fields.String(load_default=None, validate=validate.Length(max=100))


class BulkSchema(BaseSchema):
    name = fields.String(load_default=None, validate=validate.Length(max=150))
    message = fields.String(load_default=None, validate=validate.Length(max=1600),
                            metadata={"example": "Bonjour {prenom}, votre échéance de {montant} MAD est due le {date_echeance}."})
    template_id = fields.UUID(load_default=None)
    recipients = fields.List(fields.Nested(RecipientSchema), required=True,
                             validate=validate.Length(min=1, max=10000))
    device_id = fields.UUID(load_default=None)
    sim_slot = fields.Integer(load_default=None, validate=validate.Range(min=0, max=1))
    scheduled_at = fields.DateTime(load_default=None)
    add_stop_footer = fields.Boolean(load_default=False)
    priority = fields.Integer(load_default=0, validate=validate.Range(min=0, max=10))


class CampaignOutSchema(BaseSchema):
    id = fields.UUID(attribute="uuid", dump_only=True)
    name = fields.String()
    status = fields.String()
    source = fields.String()
    body = fields.String(allow_none=True)
    scheduled_at = fields.DateTime(allow_none=True)
    total = fields.Integer()
    queued = fields.Integer()
    sent = fields.Integer()
    delivered = fields.Integer()
    failed = fields.Integer()
    blocked = fields.Integer()
    progress = fields.Integer()
    started_at = fields.DateTime(allow_none=True)
    completed_at = fields.DateTime(allow_none=True)
    created_at = fields.DateTime()


class BulkResultSchema(BaseSchema):
    campaign = fields.Nested(CampaignOutSchema)
    created = fields.Integer()
    blocked = fields.Integer()
    rejected = fields.List(fields.Nested(RejectedSchema))


class UploadFormSchema(BaseSchema):
    message = fields.String(load_default=None, validate=validate.Length(max=1600),
                            metadata={"description": "Texte commun avec variables ; facultatif si colonne « message »"})
    template_id = fields.UUID(load_default=None)
    name = fields.String(load_default=None, validate=validate.Length(max=150))
    sheet = fields.String(load_default=None)
    phone_column = fields.String(load_default=None, metadata={"description": "Nom de la colonne téléphone (auto si absent)"})
    message_column = fields.String(load_default=None)
    mapping = fields.String(load_default=None,
                            metadata={"description": "JSON {colonne: variable} (auto si absent)"})
    device_id = fields.UUID(load_default=None)
    sim_slot = fields.Integer(load_default=None, validate=validate.Range(min=0, max=1))
    scheduled_at = fields.DateTime(load_default=None)
    add_stop_footer = fields.Boolean(load_default=False)
    save_contacts = fields.Boolean(load_default=False)
    dry_run = fields.Boolean(load_default=False, metadata={"description": "Valider sans envoyer"})


class UploadRowSchema(BaseSchema):
    row = fields.Integer(attribute="row_number")
    phone = fields.String(attribute="phone_raw", allow_none=True)
    status = fields.String()
    error = fields.String(allow_none=True)


class UploadResultSchema(BaseSchema):
    import_id = fields.UUID()
    columns = fields.List(fields.String())
    mapping = fields.Dict()
    summary = fields.Dict()
    preview = fields.List(fields.Dict())
    rejected = fields.List(fields.Nested(UploadRowSchema))
    campaign = fields.Nested(CampaignOutSchema, allow_none=True)
    error_report_url = fields.String(allow_none=True)


# --- Inbox ----------------------------------------------------------------------------

class InboxArgs(PageArgs):
    phone = fields.String(load_default=None)
    since = fields.DateTime(load_default=None)
    unread = fields.Boolean(load_default=None)


# --- Contacts / groupes ------------------------------------------------------------------

class ContactSchema(BaseSchema):
    id = fields.UUID(attribute="uuid", dump_only=True)
    phone = fields.String(required=True, validate=validate.Length(min=6, max=30))
    first_name = fields.String(allow_none=True, validate=validate.Length(max=80))
    last_name = fields.String(allow_none=True, validate=validate.Length(max=80))
    email = fields.Email(allow_none=True)
    birthday = fields.Date(allow_none=True)
    locale = fields.String(validate=validate.OneOf(["fr", "ar"]))
    custom_fields = fields.Dict(keys=fields.String())
    consent_status = fields.String(validate=validate.OneOf(["opt_in", "unknown", "opted_out"]))
    groups = fields.Method("dump_groups", "load_groups")
    notes = fields.String(allow_none=True)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)

    def dump_groups(self, obj):
        return [g.name for g in obj.groups]

    def load_groups(self, value):
        if not isinstance(value, list):
            raise ValidationError("Liste de noms ou d'identifiants de groupes attendue")
        return value


class ContactUpdateSchema(ContactSchema):
    phone = fields.String(validate=validate.Length(min=6, max=30))


class ContactListArgs(PageArgs):
    q = fields.String(load_default=None, metadata={"description": "Recherche nom / téléphone"})
    group_id = fields.Integer(load_default=None)
    consent_status = fields.String(load_default=None)


class ContactPageSchema(BaseSchema):
    data = fields.List(fields.Nested(ContactSchema))
    meta = fields.Nested(MetaSchema)


class GroupSchema(BaseSchema):
    id = fields.Integer(dump_only=True)
    name = fields.String(required=True, validate=validate.Length(min=1, max=100))
    description = fields.String(allow_none=True, validate=validate.Length(max=255))
    color = fields.String(allow_none=True, validate=validate.Regexp(r"^#[0-9a-fA-F]{6}$"))
    contacts_count = fields.Function(lambda g: len(g.contacts), dump_only=True)
    created_at = fields.DateTime(dump_only=True)


class GroupMembersSchema(BaseSchema):
    contacts = fields.List(fields.String(), required=True, validate=validate.Length(min=1, max=10000),
                           metadata={"description": "UUID de contacts ou numéros de téléphone"})


# --- Modèles ------------------------------------------------------------------------------

class TemplateSchema(BaseSchema):
    id = fields.UUID(attribute="uuid", dump_only=True)
    name = fields.String(required=True, validate=validate.Length(min=1, max=120))
    category = fields.String(load_default="general", validate=validate.Length(max=40))
    language = fields.String(load_default="fr", validate=validate.OneOf(["fr", "ar"]))
    body = fields.String(required=True, validate=validate.Length(min=1, max=1600))
    variables = fields.List(fields.String(), dump_only=True)
    is_active = fields.Boolean(load_default=True)
    sms = fields.Method("dump_sms", dump_only=True)
    created_at = fields.DateTime(dump_only=True)

    def dump_sms(self, obj):
        from ..services.sms_encoding import analyze
        info = analyze(obj.body)
        return {"encoding": info.encoding, "characters": info.characters, "segments": info.segments}


class TemplateListArgs(PageArgs):
    category = fields.String(load_default=None)
    language = fields.String(load_default=None)


# --- Planifications -------------------------------------------------------------------------

class AudienceSchema(BaseSchema):
    groups = fields.List(fields.Integer(), load_default=list)
    contacts = fields.List(fields.String(), load_default=list)
    exclude_groups = fields.List(fields.Integer(), load_default=list)
    all = fields.Boolean(load_default=False)


class ScheduleSchema(BaseSchema):
    id = fields.UUID(attribute="uuid", dump_only=True)
    name = fields.String(required=True, validate=validate.Length(min=1, max=150))
    body = fields.String(load_default=None, validate=validate.Length(max=1600))
    template_id = fields.UUID(load_default=None, load_only=True)
    audience = fields.Nested(AudienceSchema, required=True)
    device_id = fields.UUID(load_default=None, load_only=True)
    sim_slot = fields.Integer(load_default=None, validate=validate.Range(min=0, max=1))
    add_stop_footer = fields.Boolean(load_default=False)
    recurrence = fields.String(required=True, validate=validate.OneOf(RECURRENCES))
    run_time = fields.Time(required=True, metadata={"example": "09:30"})
    run_date = fields.Date(load_default=None)
    days_of_week = fields.List(fields.Integer(validate=validate.Range(min=1, max=7)), load_default=list)
    day_of_month = fields.Integer(load_default=None, validate=validate.Range(min=1, max=31))
    start_date = fields.Date(load_default=None)
    end_date = fields.Date(load_default=None)
    is_active = fields.Boolean(load_default=True)
    next_run_at = fields.DateTime(dump_only=True, allow_none=True)
    last_run_at = fields.DateTime(dump_only=True, allow_none=True)

    @validates_schema
    def _check(self, data, **kwargs):
        if not data.get("body") and not data.get("template_id"):
            raise ValidationError("Fournir « body » ou « template_id »", "body")
        if data.get("recurrence") == "once" and not data.get("run_date"):
            raise ValidationError("run_date requis pour une planification unique", "run_date")
        if data.get("recurrence") == "weekly" and not data.get("days_of_week"):
            raise ValidationError("days_of_week requis (1=lundi … 7=dimanche)", "days_of_week")


# --- Appareils ---------------------------------------------------------------------------------

class SimSchema(BaseSchema):
    slot = fields.Integer(attribute="slot_index")
    carrier = fields.String(allow_none=True)
    phone_number = fields.String(allow_none=True)
    is_active = fields.Boolean()


class DeviceOutSchema(BaseSchema):
    id = fields.UUID(attribute="uuid", dump_only=True)
    name = fields.String()
    status = fields.String()
    model = fields.String(allow_none=True)
    manufacturer = fields.String(allow_none=True)
    android_version = fields.String(allow_none=True)
    app_version = fields.String(allow_none=True)
    battery_level = fields.Integer(allow_none=True)
    is_charging = fields.Boolean(allow_none=True)
    signal_level = fields.Integer(allow_none=True)
    local_queue_size = fields.Integer()
    last_seen_at = fields.DateTime(allow_none=True)
    default_sim_slot = fields.Integer()
    send_interval_sec = fields.Integer()
    max_per_hour = fields.Integer()
    max_per_day = fields.Integer()
    is_enabled = fields.Boolean()
    sims = fields.List(fields.Nested(SimSchema))


# --- Webhooks ------------------------------------------------------------------------------------

class WebhookSchema(BaseSchema):
    id = fields.UUID(attribute="uuid", dump_only=True)
    url = fields.Url(required=True, schemes={"http", "https"}, require_tld=False)
    events = fields.List(fields.String(validate=validate.OneOf(WEBHOOK_EVENTS)), required=True,
                         validate=validate.Length(min=1))
    is_active = fields.Boolean(dump_only=True)
    failure_count = fields.Integer(dump_only=True)
    created_at = fields.DateTime(dump_only=True)


class WebhookCreatedSchema(WebhookSchema):
    secret = fields.String(dump_only=True, metadata={"description": "Affiché une seule fois"})
