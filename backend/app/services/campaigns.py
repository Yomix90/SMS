"""Campagnes : création à partir d'une audience, d'un import ou d'une liste ; pause, reprise, annulation."""
from dataclasses import dataclass, field

import sqlalchemy as sa

from ..extensions import db
from ..models import Campaign, Contact, Group, Import, Message, Receivable, Template, utcnow
from ..models.campaign import (CAMPAIGN_CANCELED, CAMPAIGN_COMPLETED, CAMPAIGN_PAUSED, CAMPAIGN_RUNNING,
                               CAMPAIGN_SCHEDULED, IMPORT_SENT)
from ..models.contact import RECEIVABLE_UNPAID, contact_groups
from ..models.message import ST_CANCELED, ST_DISPATCHED, ST_PAUSED, ST_QUEUED, ST_SCHEDULED
from . import queue, realtime
from .audit import audit
from .templating import contact_variables, render
from .timeutil import ensure_utc


@dataclass
class Recipient:
    phone: str
    variables: dict = field(default_factory=dict)
    message: str | None = None      # message spécifique (colonne « message » ou API)
    contact: Contact | None = None
    external_ref: str | None = None


@dataclass
class CampaignResult:
    campaign: Campaign
    created: int = 0
    blocked: int = 0
    rejected: list[dict] = field(default_factory=list)  # {"phone", "error", "index"}


def resolve_audience(audience: dict) -> list[Contact]:
    """audience = {"groups": [ids], "contacts": [ids ou uuids], "exclude_groups": [ids], "all": bool}"""
    audience = audience or {}
    q = db.session.query(Contact)
    conditions = []
    if audience.get("all"):
        conditions.append(sa.true())
    if audience.get("groups"):
        sub = sa.select(contact_groups.c.contact_id).where(contact_groups.c.group_id.in_(audience["groups"]))
        conditions.append(Contact.id.in_(sub))
    if audience.get("contacts"):
        ids = [c for c in audience["contacts"] if isinstance(c, int) or str(c).isdigit()]
        uuids = [c for c in audience["contacts"] if c not in ids]
        if ids:
            conditions.append(Contact.id.in_([int(i) for i in ids]))
        if uuids:
            import uuid as uuidlib
            conditions.append(Contact.uuid.in_([uuidlib.UUID(str(u)) for u in uuids]))
    if not conditions:
        return []
    q = q.filter(sa.or_(*conditions))
    if audience.get("exclude_groups"):
        sub = sa.select(contact_groups.c.contact_id).where(
            contact_groups.c.group_id.in_(audience["exclude_groups"]))
        q = q.filter(Contact.id.notin_(sub))
    return q.order_by(Contact.id).all()


def nearest_unpaid(contact_ids: list[int]) -> dict[int, Receivable]:
    """Échéance impayée la plus proche par contact (pour {montant}, {date_echeance}, {reference})."""
    result: dict[int, Receivable] = {}
    if not contact_ids:
        return result
    rows = (db.session.query(Receivable)
            .filter(Receivable.contact_id.in_(contact_ids), Receivable.status == RECEIVABLE_UNPAID)
            .order_by(Receivable.due_date).all())
    for r in rows:
        result.setdefault(r.contact_id, r)
    return result


def audience_recipients(audience: dict) -> list[Recipient]:
    contacts = resolve_audience(audience)
    receivables = nearest_unpaid([c.id for c in contacts])
    return [Recipient(c.phone, contact_variables(c, receivables.get(c.id)), contact=c) for c in contacts]


def preview(body: str, recipient: Recipient, add_stop_footer: bool = False) -> dict:
    """Aperçu du rendu final pour un destinataire réel."""
    from .sms_encoding import analyze
    res = render(recipient.message or body, recipient.variables)
    text = res.text
    if add_stop_footer:
        lang = recipient.contact.locale if recipient.contact else queue.detect_lang(text)
        text = f"{text}\n{queue.stop_footer(lang)}"
    return {"text": text, "missing": res.missing, "sms": analyze(text).to_dict()}


def create_campaign(name: str, *, body: str | None = None, template: Template | None = None,
                    recipients: list[Recipient] | None = None, audience: dict | None = None,
                    import_: Import | None = None, device=None, sim_slot: int | None = None,
                    scheduled_at=None, add_stop_footer: bool = False, source: str = "manual",
                    user=None, api_key=None, priority: int = 0, ignore_window: bool = False) -> CampaignResult:
    """Crée la campagne et met en file un message personnalisé par destinataire."""
    if template is not None and not body:
        body = template.body
    recipients = list(recipients or [])
    if audience:
        recipients.extend(audience_recipients(audience))
    if not recipients:
        raise queue.QueueError("no_recipients", "Aucun destinataire", 422)
    if not body and any(not r.message for r in recipients):
        raise queue.QueueError("empty_body", "Texte du message manquant", 422)

    queue.check_api_quota(api_key, len(recipients))
    queue.check_abuse(len(recipients), api_key=api_key, user=user)

    scheduled_at = ensure_utc(scheduled_at)
    now = utcnow()
    is_future = scheduled_at is not None and scheduled_at > now
    campaign = Campaign(
        name=name[:150], body=body, template_id=template.id if template else None, source=source,
        import_id=import_.id if import_ else None, audience=audience or {},
        device_id=device.id if device is not None else None, sim_slot=sim_slot,
        status=CAMPAIGN_SCHEDULED if is_future else CAMPAIGN_RUNNING,
        scheduled_at=scheduled_at, add_stop_footer=add_stop_footer,
        started_at=None if is_future else now,
        created_by=user.id if user is not None else None,
        api_key_id=api_key.id if api_key is not None else None,
    )
    db.session.add(campaign)
    db.session.flush()

    result = CampaignResult(campaign)
    cache: dict = {}
    footer_cache: dict[str, str] = {}
    for index, r in enumerate(recipients):
        text = render(r.message or body, r.variables).text
        if add_stop_footer:
            lang = r.contact.locale if r.contact else queue.detect_lang(text)
            footer = footer_cache.setdefault(lang, queue.stop_footer(lang))
            text = f"{text}\n{footer}" if footer else text
        try:
            msg = queue.enqueue(r.phone, text, contact=r.contact, campaign=campaign, device=device,
                                sim_slot=sim_slot, scheduled_at=scheduled_at, priority=priority, source=source,
                                user_id=campaign.created_by, api_key=api_key, external_ref=r.external_ref,
                                ignore_window=ignore_window, cache=cache, notify_dashboard=False)
        except queue.QueueError as exc:
            result.rejected.append({"index": index, "phone": str(r.phone), "error": exc.code,
                                    "message": exc.message})
            continue
        result.created += 1
        if msg.status == "blocked":
            result.blocked += 1

    if result.created == 0:
        db.session.rollback()
        raise queue.QueueError("no_valid_recipients", "Aucun destinataire valide", 422,
                               {"rejected": result.rejected[:100]})
    if import_ is not None:
        import_.status = IMPORT_SENT
    queue.refresh_campaign(campaign.id)
    audit("campaign.create", campaign, {"recipients": result.created, "rejected": len(result.rejected),
                                        "source": source}, user_id=campaign.created_by)
    return result


def pause_campaign(campaign: Campaign) -> int:
    if campaign.status not in (CAMPAIGN_RUNNING, CAMPAIGN_SCHEDULED):
        raise queue.QueueError("invalid_state", "Seule une campagne en cours ou planifiée peut être mise en pause",
                               409)
    n = (db.session.query(Message)
         .filter(Message.campaign_id == campaign.id, Message.status.in_((ST_QUEUED, ST_SCHEDULED)))
         .update({Message.status: ST_PAUSED}, synchronize_session=False))
    campaign.status = CAMPAIGN_PAUSED
    queue.refresh_campaign(campaign.id)
    audit("campaign.pause", campaign, {"messages": n})
    return n


def resume_campaign(campaign: Campaign) -> int:
    if campaign.status != CAMPAIGN_PAUSED:
        raise queue.QueueError("invalid_state", "La campagne n'est pas en pause", 409)
    now = utcnow()
    future = campaign.scheduled_at is not None and campaign.scheduled_at > now
    n = (db.session.query(Message)
         .filter(Message.campaign_id == campaign.id, Message.status == ST_PAUSED)
         .update({Message.status: ST_SCHEDULED if future else ST_QUEUED,
                  Message.next_attempt_at: campaign.scheduled_at if future else now},
                 synchronize_session=False))
    campaign.status = CAMPAIGN_SCHEDULED if future else CAMPAIGN_RUNNING
    campaign.started_at = campaign.started_at or (None if future else now)
    queue.refresh_campaign(campaign.id)
    audit("campaign.resume", campaign, {"messages": n})
    return n


def cancel_campaign(campaign: Campaign) -> int:
    if campaign.status in (CAMPAIGN_COMPLETED, CAMPAIGN_CANCELED):
        raise queue.QueueError("invalid_state", "Campagne déjà terminée", 409)
    n = (db.session.query(Message)
         .filter(Message.campaign_id == campaign.id,
                 Message.status.in_((ST_QUEUED, ST_SCHEDULED, ST_PAUSED)))
         .update({Message.status: ST_CANCELED}, synchronize_session=False))
    # Messages déjà transmis aux téléphones : demande d'annulation s'ils ne sont pas encore partis
    dispatched = (db.session.query(Message.device_id, Message.uuid)
                  .filter(Message.campaign_id == campaign.id, Message.status == ST_DISPATCHED).all())
    by_device: dict[int, list[str]] = {}
    for device_id, uid in dispatched:
        if device_id:
            by_device.setdefault(device_id, []).append(str(uid))
    for device_id, ids in by_device.items():
        realtime.to_device(device_id, "task.cancel", {"ids": ids})
    campaign.status = CAMPAIGN_CANCELED
    campaign.completed_at = utcnow()
    queue.refresh_campaign(campaign.id)
    audit("campaign.cancel", campaign, {"messages": n, "dispatched": len(dispatched)})
    return n


def retry_failed(campaign: Campaign) -> int:
    """Relance tous les messages échoués d'une campagne."""
    from ..models.message import ST_FAILED
    count = 0
    for msg in db.session.query(Message).filter_by(campaign_id=campaign.id, status=ST_FAILED):
        count += queue.retry_message(msg)
    if count and campaign.status in (CAMPAIGN_COMPLETED,):
        campaign.status = CAMPAIGN_RUNNING
        campaign.completed_at = None
    queue.refresh_campaign(campaign.id)
    audit("campaign.retry_failed", campaign, {"messages": count})
    return count


def group_ids_by_name(names: list[str]) -> list[int]:
    return [g.id for g in db.session.query(Group).filter(Group.name.in_(names)).all()]
