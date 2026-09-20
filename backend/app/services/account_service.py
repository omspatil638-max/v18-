"""
account_service.py
------------------
Sign-up support: phone validation, the e-mail verification code, and which "state" the server is in.

AUTH_MODE=auto (the default) means:
  setup  no account with a password exists yet. The very first screen is the sign-up form. Until it
         is completed the app behaves like the old single-user demo, so nothing already stored is lost:
         signing up CLAIMS the existing local data instead of starting empty.
  login  at least one account exists: everything requires signing in.

Nothing here logs or returns a verification code. A code is only ever sent to the address it proves.
"""

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_token
from app.models.models import EmailVerification, User

CODE_TTL_MINUTES = 15
MAX_CODE_ATTEMPTS = 5
RESEND_MIN_SECONDS = 60
RESEND_MAX_PER_HOUR = 5


class AccountError(ValueError):
    """Bad input. The message is safe to show."""


async def auth_state(db: AsyncSession) -> str:
    """'demo' | 'setup' | 'login' (see the module docstring)."""
    if settings.AUTH_MODE in ("demo", "login"):
        return settings.AUTH_MODE
    has_account = (await db.execute(select(User.id).where(User.password_hash.isnot(None)).limit(1))).first()
    return "login" if has_account else "setup"


def normalize_phone(raw: Optional[str]) -> str:
    """Digits with an optional leading +, 7 to 15 digits (the E.164 range). Formatting characters are dropped."""
    text = (raw or "").strip()
    if not text:
        raise AccountError("Enter your phone number.")
    if re.search(r"[^\d\s+()\-.]", text):
        raise AccountError("A phone number can only contain digits, spaces and + ( ) - .")
    plus = text.startswith("+")
    digits = re.sub(r"\D", "", text)
    if not 7 <= len(digits) <= 15:
        raise AccountError("Enter a valid phone number with 7 to 15 digits, including the country code if you can (for example +1 415 555 0134).")
    if len(set(digits)) == 1:
        raise AccountError("That does not look like a real phone number.")
    return ("+" if plus else "") + digits


def valid_timezone(name: Optional[str]) -> str:
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(name or "UTC")
        return name or "UTC"
    except Exception:  # noqa: BLE001 - unknown or malformed zone names fall back to UTC
        return "UTC"


def _hash(user_id, code: str) -> str:
    return hash_token(f"{user_id}:{code}")


async def issue_code(db: AsyncSession, user: User) -> Optional[str]:
    """Create a fresh 6-digit code for the user's current address (older codes stop working). Returns the code."""
    from sqlalchemy import update
    await db.execute(update(EmailVerification).where(EmailVerification.user_id == user.id, EmailVerification.used_at.is_(None))
                     .values(used_at=datetime.now(timezone.utc)))
    code = f"{secrets.randbelow(10 ** 6):06d}"
    now = datetime.now(timezone.utc)                     # explicit: a naive default is misread on a non-UTC machine
    db.add(EmailVerification(user_id=user.id, email=user.email, code_hash=_hash(user.id, code), created_at=now,
                             expires_at=now + timedelta(minutes=CODE_TTL_MINUTES)))
    await db.flush()
    return code


async def recent_sends(db: AsyncSession, user: User) -> Tuple[int, Optional[datetime]]:
    rows = (await db.execute(
        select(EmailVerification.created_at).where(
            EmailVerification.user_id == user.id, EmailVerification.created_at > datetime.now(timezone.utc) - timedelta(hours=1))
        .order_by(EmailVerification.created_at.desc())
    )).scalars().all()
    return len(rows), (rows[0] if rows else None)


async def check_code(db: AsyncSession, user: User, code: str) -> None:
    """Raises AccountError unless `code` is the live code for the user's current address."""
    code = re.sub(r"\D", "", code or "")
    row = (await db.execute(
        select(EmailVerification).where(EmailVerification.user_id == user.id, EmailVerification.used_at.is_(None))
        .order_by(EmailVerification.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None or row.email != user.email or row.expires_at < now:
        raise AccountError("That code has expired. Ask for a new one.")
    if row.attempts >= MAX_CODE_ATTEMPTS:
        raise AccountError("Too many wrong codes. Ask for a new one.")
    row.attempts += 1
    if not secrets.compare_digest(row.code_hash, _hash(user.id, code)):
        await db.flush()
        left = MAX_CODE_ATTEMPTS - row.attempts
        raise AccountError("That code is not right." + (f" {left} attempt(s) left." if left > 0 else " Ask for a new code."))
    row.used_at = now
    user.email_verified_at = now
