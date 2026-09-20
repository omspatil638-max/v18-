"""
push_service.py
---------------
Browser push notifications (Web Push with VAPID). Free, no third-party account: the browser's own
push service (Chrome/Edge/Firefox) delivers the message, even when the ContractLens tab is closed.

The payload is a short title/body plus a link. Subscriptions the push service reports as gone
(404/410) are deleted so a dead browser is not retried forever. Credentials are never logged.
"""

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.models import PushSubscription

logger = logging.getLogger(__name__)

# fn(subscription_info, payload_json) -> None. Raises PushGone for an expired subscription.
PushSender = Callable[[Dict[str, Any], str], None]


class PushGone(Exception):
    """The browser's push service says this subscription no longer exists."""


def _webpush_send(subscription: Dict[str, Any], payload: str) -> None:
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=subscription,
            data=payload,
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.VAPID_SUBJECT},
            ttl=24 * 3600,
            timeout=15,
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status in (404, 410):
            raise PushGone() from exc
        raise


_sender: PushSender = _webpush_send


def set_sender(sender: Optional[PushSender]) -> None:
    """Tests only: swap the transport."""
    global _sender
    _sender = sender or _webpush_send


def payload(title: str, body: str, url: Optional[str] = None, tag: str = "contractlens-alerts") -> str:
    return json.dumps({"title": title, "body": body, "url": url or settings.APP_BASE_URL.rstrip("/") + "/", "tag": tag})


async def subscriptions_for(db: AsyncSession, user_id) -> List[PushSubscription]:
    return list((await db.execute(select(PushSubscription).where(PushSubscription.user_id == user_id))).scalars())


async def send_to_user(db: AsyncSession, user_id, message: str) -> Dict[str, int]:
    """Send `message` to every registered browser of the user. Returns {sent, failed, removed}."""
    result = {"sent": 0, "failed": 0, "removed": 0}
    if not settings.push_configured:
        return result
    for sub in await subscriptions_for(db, user_id):
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        try:
            await asyncio.to_thread(_sender, info, message)
            result["sent"] += 1
        except PushGone:
            await db.execute(delete(PushSubscription).where(PushSubscription.id == sub.id))
            result["removed"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Push failed (%s)", exc.__class__.__name__)
            result["failed"] += 1
    return result
