"""
alerts.py — upcoming-deadline alerts, their settings, and optional email.

Exposed at /alerts, with /reminders kept as a compatibility alias for the earlier dashboard.
"""

import re
import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import serializers
from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.models import (
    Alert, AlertSetting, AlertStatus, Contract, Deadline, Obligation, ObligationStatus, PushSubscription, User,
)
from app.schemas.schemas import AlertResponse
from app.services import alert_service, deadline_service

router = APIRouter(tags=["Alerts"])

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AlertSettingsResponse(BaseModel):
    lead_days: List[int]
    default_lead_days: List[int]
    email_to: Optional[str] = None
    email_enabled: bool = False
    smtp_configured: bool = False
    repeat_enabled: bool = True
    repeat_hours: int = 24
    max_repeats: int = 5
    push_enabled: bool = False
    push_available: bool = False
    push_subscriptions: int = 0
    briefing_enabled: bool = False
    briefing_hour: int = 8
    timezone: str = "UTC"
    paused_until: Optional[date] = None
    account_email: Optional[str] = None
    email_verified: bool = False


class AlertSettingsUpdate(BaseModel):
    lead_days: Optional[List[int]] = Field(default=None, max_length=6)
    email_to: Optional[str] = Field(default=None, max_length=255)
    email_enabled: Optional[bool] = None
    repeat_enabled: Optional[bool] = None
    repeat_hours: Optional[int] = Field(default=None, ge=1, le=168)
    push_enabled: Optional[bool] = None
    briefing_enabled: Optional[bool] = None
    briefing_hour: Optional[int] = Field(default=None, ge=0, le=23)
    timezone: Optional[str] = Field(default=None, max_length=64)
    pause_days: Optional[int] = Field(default=None, ge=0, le=90, description="Pause all notifications for N days; 0 resumes")

    @field_validator("lead_days")
    @classmethod
    def _leads(cls, v):
        if v is None:
            return v
        if any((not isinstance(d, int)) or d < 1 or d > 365 for d in v):
            raise ValueError("Lead times must be whole numbers of days between 1 and 365.")
        return sorted(set(v), reverse=True)


async def _list(user: User, db: AsyncSession, unacknowledged_only: bool, due_only: bool) -> List[AlertResponse]:
    stmt = (
        select(Alert, Contract.title)
        .join(Contract, Contract.id == Alert.contract_id)
        .join(Deadline, Deadline.id == Alert.deadline_id)
        .outerjoin(Obligation, Obligation.id == Deadline.obligation_id)
        .options(selectinload(Alert.deadline))
        .where(
            Alert.user_id == user.id, Contract.deleted_at.is_(None),
            Deadline.contract_version_id == Contract.current_version_id,
            or_(Obligation.id.is_(None), Obligation.status != ObligationStatus.COMPLETED),
        )
        .order_by(Alert.fire_on.asc())
    )
    if unacknowledged_only:
        stmt = stmt.where(Alert.status.in_([AlertStatus.PENDING, AlertStatus.SENT]))
    if due_only:
        stmt = stmt.where(Alert.fire_on <= date.today())
    today = date.today()
    return [serializers.alert_response(a, title, today) for a, title in (await db.execute(stmt)).all()]


@router.get("/alerts", response_model=List[AlertResponse])
async def list_alerts(
    unacknowledged_only: bool = True,
    due_only: bool = Query(False, description="Only alerts whose lead time has been reached (what needs attention now)"),
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    return await _list(user, db, unacknowledged_only, due_only)


@router.get("/reminders", response_model=List[AlertResponse], include_in_schema=False)
async def list_reminders_alias(
    unacknowledged_only: bool = True, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    return await _list(user, db, unacknowledged_only, due_only=False)


# ─── settings (must be declared before the /{alert_id} routes) ────────────────

async def _settings_payload(db: AsyncSession, user: User) -> AlertSettingsResponse:
    s = (await db.execute(select(AlertSetting).where(AlertSetting.user_id == user.id))).scalar_one_or_none()
    subs = (await db.execute(select(func.count()).select_from(PushSubscription).where(PushSubscription.user_id == user.id))).scalar_one()
    return AlertSettingsResponse(
        lead_days=sorted(s.lead_days, reverse=True) if s and s.lead_days else settings.alert_lead_days,
        default_lead_days=settings.alert_lead_days,
        email_to=s.email_to if s else None, email_enabled=bool(s and s.email_enabled),
        smtp_configured=settings.smtp_configured,
        repeat_enabled=s.repeat_enabled if s else True, repeat_hours=(s.repeat_hours if s else 24) or 24,
        max_repeats=settings.ALERT_MAX_REPEATS, push_enabled=bool(s and s.push_enabled),
        push_available=settings.push_configured, push_subscriptions=subs,
        briefing_enabled=bool(s and s.briefing_enabled), briefing_hour=(s.briefing_hour if s else 8),
        timezone=(s.timezone if s else "UTC") or "UTC", paused_until=(s.paused_until if s else None),
        account_email=user.email if user.consented_at is not None else None, email_verified=user.email_verified_at is not None,
    )


@router.get("/alerts/settings", response_model=AlertSettingsResponse)
async def get_alert_settings(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _settings_payload(db, user)


@router.put("/alerts/settings", response_model=AlertSettingsResponse)
async def update_alert_settings(
    payload: AlertSettingsUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
):
    """Change lead times and/or email delivery. Changing lead times rebuilds the pending alerts."""
    s = (await db.execute(select(AlertSetting).where(AlertSetting.user_id == user.id))).scalar_one_or_none()
    if s is None:
        s = AlertSetting(user_id=user.id)
        db.add(s)

    if payload.email_to is not None:
        email = payload.email_to.strip()
        if email and not _EMAIL.match(email):
            raise HTTPException(status_code=422, detail="That does not look like an email address.")
        s.email_to = email or None
    if payload.email_enabled is not None:
        if payload.email_enabled and not (payload.email_to or s.email_to):
            raise HTTPException(status_code=422, detail="Enter an email address before turning email alerts on.")
        s.email_enabled = payload.email_enabled

    if payload.repeat_enabled is not None:
        s.repeat_enabled = payload.repeat_enabled
    if payload.repeat_hours is not None:
        s.repeat_hours = payload.repeat_hours
    if payload.push_enabled is not None:
        s.push_enabled = payload.push_enabled
    if payload.briefing_enabled is not None:
        s.briefing_enabled = payload.briefing_enabled
    if payload.briefing_hour is not None:
        s.briefing_hour = payload.briefing_hour
    if payload.timezone is not None:
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(payload.timezone)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=422, detail="Unknown time zone. Use a name like Asia/Kolkata or Europe/London.")
        s.timezone = payload.timezone
    if payload.pause_days is not None:
        s.paused_until = (date.today() + timedelta(days=payload.pause_days)) if payload.pause_days > 0 else None

    relead = payload.lead_days is not None and payload.lead_days != (s.lead_days or None)
    if payload.lead_days is not None:
        s.lead_days = payload.lead_days
    await db.flush()
    if relead:
        await deadline_service.regenerate_alerts_for_user(db, user.id)
    await db.commit()
    return await _settings_payload(db, user)


@router.post("/alerts/test-email")
async def send_test_email(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    s = (await db.execute(select(AlertSetting).where(AlertSetting.user_id == user.id))).scalar_one_or_none()
    if not s or not s.email_to:
        raise HTTPException(status_code=422, detail="Save an email address first.")
    problem = await alert_service.send_test_email(s.email_to)
    if problem:
        raise HTTPException(status_code=503, detail=problem)
    return {"message": f"A test email was sent to {s.email_to}."}


# ─── acknowledge ──────────────────────────────────────────────────────────────

async def _acknowledge(alert_id: uuid.UUID, user: User, db: AsyncSession):
    alert = (await db.execute(select(Alert).where(Alert.id == alert_id, Alert.user_id == user.id))).scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.status = AlertStatus.ACKNOWLEDGED
    alert.acknowledged_at = datetime.utcnow()
    await db.commit()
    return {"message": "Alert acknowledged"}


@router.patch("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _acknowledge(alert_id, user, db)


@router.patch("/reminders/{alert_id}/acknowledge", include_in_schema=False)
async def acknowledge_reminder_alias(alert_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _acknowledge(alert_id, user, db)


@router.post("/alerts/acknowledge-all")
async def acknowledge_all(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Mark every currently due, unread alert of this user as read (stops their reminders)."""
    return {"acknowledged": await _acknowledge_due(db, user.id)}


async def _acknowledge_due(db: AsyncSession, user_id: uuid.UUID) -> int:
    res = await db.execute(
        update(Alert)
        .where(Alert.user_id == user_id, Alert.status.in_([AlertStatus.PENDING, AlertStatus.SENT]), Alert.fire_on <= date.today())
        .values(status=AlertStatus.ACKNOWLEDGED, acknowledged_at=datetime.utcnow())
    )
    await db.commit()
    return res.rowcount or 0


# ─── one-click "mark as read" from an email (no login: the link carries an unguessable token) ───────

async def _setting_by_token(db: AsyncSession, token: str) -> AlertSetting:
    s = None
    if 16 <= len(token) <= 64:
        s = (await db.execute(select(AlertSetting).where(AlertSetting.ack_token == token))).scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="This link is no longer valid.")
    return s


@router.get("/alerts/ack/{token}")
async def ack_link_status(token: str, db: AsyncSession = Depends(get_db)):
    """Read-only: how many alerts this link would mark as read. Never changes anything (mail scanners prefetch links)."""
    try:
        s = await _setting_by_token(db, token)
    except HTTPException:
        return {"valid": False, "pending": 0}
    pending = (await db.execute(
        select(func.count()).select_from(Alert).where(
            Alert.user_id == s.user_id, Alert.status.in_([AlertStatus.PENDING, AlertStatus.SENT]), Alert.fire_on <= date.today())
    )).scalar_one()
    return {"valid": True, "pending": pending}


@router.post("/alerts/ack/{token}")
async def ack_link(token: str, db: AsyncSession = Depends(get_db)):
    s = await _setting_by_token(db, token)
    return {"acknowledged": await _acknowledge_due(db, s.user_id)}


@router.get("/alerts/briefing/preview")
async def preview_briefing(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """What the next morning briefing would contain. Sends nothing and changes nothing."""
    return await alert_service.briefing_preview(db, user)
