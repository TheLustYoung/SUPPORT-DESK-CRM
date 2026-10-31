"""Bulk messages and announcements.

- Recipients are a segment of eligible users: language, activity, tags, registration date, tickets. Users who
  blocked the bot, were banned, anonymized or unsubscribed (/stop) are always excluded.
- Starting a campaign writes one delivery row per recipient (unique per campaign and user), then the workers send
  them in batches at the configured rate. A delivery is marked «sending» before the Telegram call, so a retried or
  restarted job never sends the same announcement twice.
- Telegram's flood control (429) pauses sending for retry_after; 403 marks the user as having blocked the bot.
"""
from datetime import datetime, timedelta
from sqlalchemy import exists, func, insert, select, update
from backend.models import Campaign, CampaignDelivery, Ticket, TgUser, UserTag, now
from backend.services import audit, content, queue, rich
from backend.services.i18n import match
from backend.services.security import DomainError

SEND_TASK = "workers.tasks.send_campaign"
FILTER_KEYS = {"languages", "active_days", "inactive_days", "tags", "exclude_tags", "registered_from", "registered_to", "has_tickets"}


def clean_filters(data: dict | None) -> dict:
    data = {k: v for k, v in (data or {}).items() if k in FILTER_KEYS and v not in (None, "", [])}
    result = {}
    for key in ("languages", "tags", "exclude_tags"):
        if key in data:
            result[key] = [str(v)[:48] for v in data[key]][:50]
    for key in ("active_days", "inactive_days"):
        if key in data:
            result[key] = max(1, min(int(data[key]), 3650))
    for key in ("registered_from", "registered_to"):
        if key in data:
            try:
                result[key] = datetime.fromisoformat(str(data[key])).date().isoformat()
            except ValueError:
                raise DomainError("bad_filter") from None
    if "has_tickets" in data:
        result["has_tickets"] = bool(data["has_tickets"])
    return result


def audience(db, filters: dict):
    """SELECT of the eligible users' ids for a segment."""
    stmt = select(TgUser.id, TgUser.language, TgUser.tg_language).where(
        TgUser.bot_blocked.is_(False), TgUser.banned.is_(False), TgUser.campaigns_opt_out.is_(False), TgUser.anonymized.is_(False))
    if filters.get("languages"):
        codes = [lang.code for lang in content.languages(db)]
        default = content.default_language(db)
        wanted = set(filters["languages"])
        # the language the user actually gets: their choice, else Telegram's, else the default
        ids = [row.id for row in db.execute(stmt) if (match(row.language, codes) or match(row.tg_language, codes) or default) in wanted]
        stmt = stmt.where(TgUser.id.in_(ids or [-1]))
    moment = now()
    if filters.get("active_days"):
        stmt = stmt.where(TgUser.last_seen >= moment - timedelta(days=filters["active_days"]))
    if filters.get("inactive_days"):
        stmt = stmt.where(TgUser.last_seen < moment - timedelta(days=filters["inactive_days"]))
    for tag in filters.get("tags") or []:
        stmt = stmt.where(exists().where(UserTag.user_id == TgUser.id, UserTag.tag == tag))
    if filters.get("exclude_tags"):
        stmt = stmt.where(~exists().where(UserTag.user_id == TgUser.id, UserTag.tag.in_(filters["exclude_tags"])))
    if filters.get("registered_from"):
        stmt = stmt.where(TgUser.first_seen >= datetime.fromisoformat(filters["registered_from"]))
    if filters.get("registered_to"):
        stmt = stmt.where(TgUser.first_seen < datetime.fromisoformat(filters["registered_to"]) + timedelta(days=1))
    if "has_tickets" in filters:
        condition = exists().where(Ticket.user_id == TgUser.id)
        stmt = stmt.where(condition if filters["has_tickets"] else ~condition)
    return stmt


def count(db, filters: dict) -> int:
    return db.scalar(select(func.count()).select_from(audience(db, clean_filters(filters)).subquery()))


def save(db, campaign: Campaign | None, data: dict, actor) -> Campaign:
    if campaign is not None and campaign.status not in ("draft", "scheduled"):
        raise DomainError("campaign_started")
    name = str(data.get("name") or "").strip()
    if not 1 <= len(name) <= 120:
        raise DomainError("bad_name")
    try:
        parsed = rich.parse(data.get("content"))
        rich.check_assets(db, parsed)
    except ValueError as error:
        raise DomainError(rich.reason(error)) from None
    if parsed.empty:
        raise DomainError("empty_message")
    rate = int(data.get("rate_per_second") or 20)
    if campaign is None:
        campaign = Campaign(created_by=actor.username)
        db.add(campaign)
    campaign.name, campaign.content, campaign.filters = name, parsed.model_dump(), clean_filters(data.get("filters"))
    campaign.rate_per_second, campaign.max_attempts = max(1, min(rate, 30)), max(1, min(int(data.get("max_attempts") or 3), 10))
    campaign.updated_at = now()
    db.flush()
    return campaign


def _fill(db, campaign: Campaign) -> int:
    """One delivery row per recipient, with the language each one gets."""
    codes, default = [lang.code for lang in content.languages(db)], content.default_language(db)
    rows = [{"campaign_id": campaign.id, "user_id": r.id, "language": match(r.language, codes) or match(r.tg_language, codes) or default,
             "status": "pending"} for r in db.execute(audience(db, campaign.filters or {}))]
    for start in range(0, len(rows), 1000):
        db.execute(insert(CampaignDelivery), rows[start:start + 1000])
    return len(rows)


def start(db, campaign: Campaign, actor, ip: str = "") -> Campaign:
    if campaign.status not in ("draft", "scheduled"):
        raise DomainError("campaign_started")
    campaign.recipients = _fill(db, campaign)
    campaign.status, campaign.started_at, campaign.scheduled_at = "running", now(), None
    audit.record(db, actor, "campaign.start", "campaign", campaign.id, {"recipients": campaign.recipients}, ip)
    queue.later(db, SEND_TASK, campaign.id)
    return campaign


def schedule(db, campaign: Campaign, when: datetime, actor, ip: str = "") -> None:
    if campaign.status not in ("draft", "scheduled"):
        raise DomainError("campaign_started")
    if when <= now():
        raise DomainError("bad_time")
    campaign.status, campaign.scheduled_at = "scheduled", when
    audit.record(db, actor, "campaign.schedule", "campaign", campaign.id, {"at": when.isoformat()}, ip)


def start_due(db) -> int:
    """Scheduled campaigns whose time has come (beat, every minute)."""
    due = db.scalars(select(Campaign).where(Campaign.status == "scheduled", Campaign.scheduled_at <= now())).all()
    for campaign in due:
        campaign.recipients = _fill(db, campaign)
        campaign.status, campaign.started_at = "running", now()
        queue.later(db, SEND_TASK, campaign.id)
    return len(due)


def pause(db, campaign: Campaign, actor) -> None:
    if campaign.status != "running":
        raise DomainError("campaign_not_running")
    campaign.status = "paused"
    audit.record(db, actor, "campaign.pause", "campaign", campaign.id)


def resume(db, campaign: Campaign, actor) -> None:
    if campaign.status != "paused":
        raise DomainError("campaign_not_paused")
    campaign.status = "running"
    audit.record(db, actor, "campaign.resume", "campaign", campaign.id)
    queue.later(db, SEND_TASK, campaign.id)


def cancel(db, campaign: Campaign, actor) -> None:
    if campaign.status in ("done", "cancelled"):
        raise DomainError("campaign_finished")
    campaign.status, campaign.finished_at = "cancelled", now()
    db.execute(update(CampaignDelivery).where(CampaignDelivery.campaign_id == campaign.id, CampaignDelivery.status == "pending")
               .values(status="skipped", error="cancelled"))
    audit.record(db, actor, "campaign.cancel", "campaign", campaign.id)


def retry_failed(db, campaign: Campaign, actor) -> int:
    """Sends again to the recipients whose delivery failed for a temporary reason (not blocked ones)."""
    if campaign.status not in ("done", "paused", "running"):
        raise DomainError("campaign_not_started")
    changed = db.execute(update(CampaignDelivery).where(CampaignDelivery.campaign_id == campaign.id, CampaignDelivery.status == "failed")
                         .values(status="pending", attempts=0, next_attempt_at=None, error="")).rowcount or 0
    if changed:
        campaign.status, campaign.finished_at = "running", None
        queue.later(db, SEND_TASK, campaign.id)
    audit.record(db, actor, "campaign.retry", "campaign", campaign.id, {"recipients": changed})
    return changed


def stats(db, campaign_id: int) -> dict:
    rows = db.execute(select(CampaignDelivery.status, func.count()).where(CampaignDelivery.campaign_id == campaign_id)
                      .group_by(CampaignDelivery.status)).all()
    result = {s: 0 for s in ("pending", "sending", "sent", "failed", "blocked", "skipped")}
    result.update({status: number for status, number in rows})
    return result


def errors(db, campaign_id: int) -> dict:
    rows = db.execute(select(CampaignDelivery.error, func.count()).where(CampaignDelivery.campaign_id == campaign_id,
                                                                          CampaignDelivery.status.in_(("failed", "blocked")))
                      .group_by(CampaignDelivery.error)).all()
    return {error or "unknown": number for error, number in rows}


def finish_if_done(db, campaign: Campaign) -> bool:
    left = db.scalar(select(func.count()).select_from(CampaignDelivery).where(
        CampaignDelivery.campaign_id == campaign.id, CampaignDelivery.status.in_(("pending", "sending"))))
    if not left and campaign.status == "running":
        campaign.status, campaign.finished_at = "done", now()
        return True
    return False
