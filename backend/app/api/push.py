"""
push.py: register browsers for Web Push notifications.

  GET  /push/public-key     the VAPID public key the browser needs (null = push not set up on the server)
  POST /push/subscribe      store this browser's subscription
  POST /push/unsubscribe    remove it
  POST /push/test           send a test notification to this user's browsers
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.database import get_db
from app.models.models import PushSubscription, User
from app.services import push_service

router = APIRouter(prefix="/push", tags=["Push notifications"])

MAX_SUBSCRIPTIONS_PER_USER = 10


class Keys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=255)
    auth: str = Field(min_length=4, max_length=255)


class SubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: Keys


class UnsubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)


@router.get("/public-key")
async def public_key(user: User = Depends(get_current_user)):
    return {"public_key": settings.VAPID_PUBLIC_KEY.strip() if settings.push_configured else None}


@router.post("/subscribe")
async def subscribe(payload: SubscribeRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not settings.push_configured:
        raise HTTPException(status_code=503, detail="Push notifications are not set up on the server yet.")
    if not payload.endpoint.startswith("https://"):
        raise HTTPException(status_code=422, detail="Push endpoints must be https.")
    existing = (await db.execute(select(PushSubscription).where(PushSubscription.endpoint == payload.endpoint))).scalar_one_or_none()
    if existing:
        existing.user_id, existing.p256dh, existing.auth = user.id, payload.keys.p256dh, payload.keys.auth
    else:
        count = len((await db.execute(select(PushSubscription.id).where(PushSubscription.user_id == user.id))).all())
        if count >= MAX_SUBSCRIPTIONS_PER_USER:
            raise HTTPException(status_code=422, detail="Too many browsers are registered. Turn notifications off in one of them first.")
        db.add(PushSubscription(user_id=user.id, endpoint=payload.endpoint, p256dh=payload.keys.p256dh, auth=payload.keys.auth))
    await db.commit()
    return {"message": "This browser will now get notifications."}


@router.post("/unsubscribe")
async def unsubscribe(payload: UnsubscribeRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await db.execute(delete(PushSubscription).where(PushSubscription.endpoint == payload.endpoint, PushSubscription.user_id == user.id))
    await db.commit()
    return {"message": "Notifications are off for this browser."}


@router.post("/test")
async def send_test(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if not settings.push_configured:
        raise HTTPException(status_code=503, detail="Push notifications are not set up on the server yet.")
    if not await push_service.subscriptions_for(db, user.id):
        raise HTTPException(status_code=503, detail="No browser is registered yet. Turn on browser notifications first.")
    res = await push_service.send_to_user(
        db, user.id, push_service.payload("ContractLens test", "Notifications are working. AI-assisted, not legal advice.", tag="contractlens-test"))
    await db.commit()
    if not res["sent"]:
        raise HTTPException(status_code=503, detail="The notification could not be delivered. Try turning notifications off and on again.")
    return {"message": f"Test notification sent to {res['sent']} browser(s).", "sent": res["sent"]}
