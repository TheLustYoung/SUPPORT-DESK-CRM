"""Bulk messages and announcements in the dashboard."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from backend.api.deps import require, staff_required
from backend.database import session
from backend.models import Campaign, CampaignDelivery, TgUser
from backend.services import campaigns, queue
from backend.services.security import DomainError

router = APIRouter(prefix="/api/admin", tags=["campaigns"])


def campaign_dict(db, c: Campaign) -> dict:
    return {"id": c.id, "name": c.name, "content": c.content, "filters": c.filters, "status": c.status, "scheduled_at": c.scheduled_at,
            "started_at": c.started_at, "finished_at": c.finished_at, "rate_per_second": c.rate_per_second,
            "max_attempts": c.max_attempts, "recipients": c.recipients, "created_by": c.created_by, "created_at": c.created_at,
            "stats": campaigns.stats(db, c.id), "errors": campaigns.errors(db, c.id)}


@router.get("/campaigns")
def list_campaigns(staff=Depends(staff_required), db=Depends(session)):
    return [campaign_dict(db, c) for c in db.scalars(select(Campaign).order_by(Campaign.id.desc()).limit(200))]


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: int, staff=Depends(staff_required), db=Depends(session)):
    return campaign_dict(db, require(db, Campaign, campaign_id))


@router.get("/campaigns/{campaign_id}/deliveries")
def deliveries(campaign_id: int, status: str = "", page: int = Query(1, ge=1), staff=Depends(staff_required), db=Depends(session)):
    """Per-recipient log of a campaign."""
    stmt = select(CampaignDelivery, TgUser).join(TgUser, TgUser.id == CampaignDelivery.user_id).where(CampaignDelivery.campaign_id == campaign_id)
    if status:
        stmt = stmt.where(CampaignDelivery.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.execute(stmt.order_by(CampaignDelivery.id).offset((page - 1) * 100).limit(100)).all()
    return {"total": total, "items": [{"user_id": d.user_id, "name": u.full_name, "language": d.language, "status": d.status,
                                       "attempts": d.attempts, "error": d.error, "sent_at": d.sent_at} for d, u in rows]}


class Filters(BaseModel):
    filters: dict = Field(default_factory=dict)


@router.post("/campaigns/count")
def count(data: Filters, staff=Depends(staff_required), db=Depends(session)):
    """How many users the segment reaches, shown before sending."""
    return {"recipients": campaigns.count(db, data.filters)}


class CampaignData(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: dict
    filters: dict = Field(default_factory=dict)
    rate_per_second: int = Field(default=20, ge=1, le=30)
    max_attempts: int = Field(default=3, ge=1, le=10)


@router.post("/campaigns")
def create(data: CampaignData, staff=Depends(staff_required), db=Depends(session)):
    campaign = campaigns.save(db, None, data.model_dump(), staff)
    db.commit()
    return campaign_dict(db, campaign)


@router.put("/campaigns/{campaign_id}")
def update(campaign_id: int, data: CampaignData, staff=Depends(staff_required), db=Depends(session)):
    campaign = campaigns.save(db, require(db, Campaign, campaign_id), data.model_dump(), staff)
    db.commit()
    return campaign_dict(db, campaign)


@router.delete("/campaigns/{campaign_id}")
def delete(campaign_id: int, staff=Depends(staff_required), db=Depends(session)):
    campaign = require(db, Campaign, campaign_id)
    if campaign.status not in ("draft", "scheduled", "cancelled"):
        raise DomainError("campaign_started")
    db.delete(campaign)
    db.commit()
    return {"ok": True}


class Test(BaseModel):
    language: str = Field(default="en", max_length=12)


@router.post("/campaigns/{campaign_id}/test")
def test(campaign_id: int, data: Test, staff=Depends(staff_required), db=Depends(session)):
    """Sends the announcement to the editor's own Telegram."""
    campaign = require(db, Campaign, campaign_id)
    if not staff.telegram_id:
        raise DomainError("telegram_not_linked")
    queue.later(db, "workers.tasks.send_preview", staff.telegram_id, campaign.content, None, data.language)
    db.commit()
    return {"sent_to": "telegram"}


@router.post("/campaigns/{campaign_id}/start")
def start(campaign_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    campaign = campaigns.start(db, require(db, Campaign, campaign_id), staff, request.state.ip)
    db.commit()
    return campaign_dict(db, campaign)


class When(BaseModel):
    at: datetime


@router.post("/campaigns/{campaign_id}/schedule")
def schedule(campaign_id: int, data: When, request: Request, staff=Depends(staff_required), db=Depends(session)):
    when = data.at if data.at.tzinfo else data.at.replace(tzinfo=timezone.utc)
    campaign = require(db, Campaign, campaign_id)
    campaigns.schedule(db, campaign, when, staff, request.state.ip)
    db.commit()
    return campaign_dict(db, campaign)


@router.post("/campaigns/{campaign_id}/{action}")
def control(campaign_id: int, action: str, staff=Depends(staff_required), db=Depends(session)):
    campaign = require(db, Campaign, campaign_id)
    handlers = {"pause": campaigns.pause, "resume": campaigns.resume, "cancel": campaigns.cancel, "retry": campaigns.retry_failed}
    if action not in handlers:
        raise DomainError("not_found", 404)
    handlers[action](db, campaign, staff)
    db.commit()
    return campaign_dict(db, campaign)
