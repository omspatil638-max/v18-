import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SESSION_COOKIE, get_current_user
from app.core.config import settings
from app.core.security import hash_password, hash_token, new_session_token, verify_password
from app.db.database import get_db
from app.db.seed import seed_demo_user
from app.models.models import AlertSetting, User, UserSession
from app.schemas.schemas import AuthConfigResponse, LoginRequest, RegisterRequest, UserResponse
from app.services import account_service, alert_service
from app.services.account_service import AccountError, auth_state

router = APIRouter(prefix="/auth", tags=["Auth"])

# Naive in-memory brute-force throttle: 5 failed logins / 15 min per (email, client ip).
_FAILS: Dict[str, List[float]] = defaultdict(list)
_WINDOW_S, _MAX_FAILS = 15 * 60, 5


def _throttle_key(request: Request, email: str) -> str:
    return f"{email.lower()}|{request.client.host if request.client else '?'}"


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=settings.COOKIE_SECURE,
        max_age=settings.SESSION_TTL_HOURS * 3600, path="/",
    )


async def _start_session(db: AsyncSession, user: User, response: Response) -> None:
    token, token_hash = new_session_token()
    db.add(UserSession(
        user_id=user.id, token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.SESSION_TTL_HOURS),
    ))
    await db.commit()
    _set_cookie(response, token)


@router.get("/config", response_model=AuthConfigResponse)
async def auth_config(db: AsyncSession = Depends(get_db)):
    state = await auth_state(db)
    return AuthConfigResponse(mode=state, registration_open=settings.ALLOW_REGISTRATION or state == "setup",
                              email_verification_available=settings.smtp_configured)


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(payload: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Plain sign-up (name, email, password). The sign-up screen uses /auth/signup, which also takes a phone number."""
    state = await auth_state(db)
    if state != "login":
        raise HTTPException(status_code=400, detail="Accounts are disabled: the server is in demo mode (AUTH_MODE=demo)."
                            if state == "demo" else "Use the sign-up form to create the first account.")
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="Registration is closed.")
    email = payload.email.strip().lower()
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    user = User(email=email, name=payload.name.strip(), password_hash=hash_password(payload.password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    await _start_session(db, user, response)
    return user


# ─── sign-up with name, phone and email ───────────────────────────────────────

class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=10, max_length=256)
    accept_terms: bool = False
    timezone: Optional[str] = Field(default=None, max_length=64)


class SignupResponse(BaseModel):
    user: UserResponse
    claimed_existing_data: bool
    email_verification: str          # sent | unavailable | failed


async def _send_code(db: AsyncSession, user: User) -> str:
    """Email a fresh verification code. Returns 'sent' | 'unavailable' | 'failed'. The code is never logged."""
    if not settings.smtp_configured:
        return "unavailable"
    code = await account_service.issue_code(db, user)
    await db.commit()
    body = (f"Your ContractLens verification code is {code}\n\nIt expires in {account_service.CODE_TTL_MINUTES} minutes. "
            "If you did not create a ContractLens account, you can ignore this email.\n\n" + alert_service.DISCLAIMER)
    try:
        await alert_service.send_message(alert_service.build_message(user.email, "Your ContractLens verification code", body))
        return "sent"
    except Exception:  # noqa: BLE001
        return "failed"


@router.post("/signup", response_model=SignupResponse, status_code=201)
async def signup(payload: SignupRequest, response: Response, db: AsyncSession = Depends(get_db)):
    state = await auth_state(db)
    if state == "demo":
        raise HTTPException(status_code=400, detail="Accounts are disabled: the server is in demo mode (AUTH_MODE=demo).")
    if state == "login" and not settings.ALLOW_REGISTRATION:
        raise HTTPException(status_code=403, detail="Registration is closed.")
    if not payload.accept_terms:
        raise HTTPException(status_code=422, detail="Please agree to receive your deadline alerts at this email address.")
    try:
        phone = account_service.normalize_phone(payload.phone)
    except AccountError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    name, email = " ".join(payload.name.split()), payload.email.strip().lower()
    if not name:
        raise HTTPException(status_code=422, detail="Enter your name.")

    claimed = False
    if state == "setup":
        # Nobody has signed up yet: the first account takes over the data already stored locally.
        user = await seed_demo_user(db)
        taken = (await db.execute(select(User).where(User.email == email, User.id != user.id))).scalar_one_or_none()
        if taken:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        claimed = True
    else:
        if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
        user = User(email=email, name=name)
        db.add(user)

    user.email, user.name, user.phone = email, name, phone
    user.password_hash = hash_password(payload.password)
    user.consented_at, user.email_verified_at, user.onboarded_at = datetime.now(timezone.utc), None, None
    await db.flush()

    setting = (await db.execute(select(AlertSetting).where(AlertSetting.user_id == user.id))).scalar_one_or_none()
    if setting is None:
        setting = AlertSetting(user_id=user.id)
        db.add(setting)
    setting.email_to = email
    setting.timezone = account_service.valid_timezone(payload.timezone)
    await db.commit()
    await db.refresh(user)
    await _start_session(db, user, response)
    return SignupResponse(user=user, claimed_existing_data=claimed, email_verification=await _send_code(db, user))


class CodeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=12)


@router.post("/verify-email", response_model=UserResponse)
async def verify_email(payload: CodeRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.email_verified_at is not None:
        return user
    try:
        await account_service.check_code(db, user, payload.code)
    except AccountError as exc:
        await db.commit()                                   # keep the attempt count
        raise HTTPException(status_code=422, detail=str(exc))
    setting = (await db.execute(select(AlertSetting).where(AlertSetting.user_id == user.id))).scalar_one_or_none()
    if setting is not None and settings.smtp_configured and not setting.email_enabled:
        # They agreed at sign-up to get alerts at this address, and it is now proven to be theirs.
        setting.email_to, setting.email_enabled, setting.briefing_enabled = user.email, True, True
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/resend-verification")
async def resend_verification(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.email_verified_at is not None:
        return {"message": "Your email is already verified.", "email_verification": "verified"}
    if not settings.smtp_configured:
        raise HTTPException(status_code=503, detail="Email sending is not set up on this server yet, so a code cannot be sent. Run Setup-Email.cmd first.")
    count, last = await account_service.recent_sends(db, user)
    if last is not None and (datetime.now(timezone.utc) - last).total_seconds() < account_service.RESEND_MIN_SECONDS:
        raise HTTPException(status_code=429, detail="A code was just sent. Wait a minute before asking for another.")
    if count >= account_service.RESEND_MAX_PER_HOUR:
        raise HTTPException(status_code=429, detail="Too many codes were requested. Try again in an hour.")
    result = await _send_code(db, user)
    if result != "sent":
        raise HTTPException(status_code=503, detail="The email could not be sent. Check the email settings.")
    return {"message": f"A new code was sent to {user.email}.", "email_verification": "sent"}


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    phone: Optional[str] = Field(default=None, max_length=40)


@router.put("/profile", response_model=UserResponse)
async def update_profile(payload: ProfileUpdate, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if payload.name is not None:
        name = " ".join(payload.name.split())
        if not name:
            raise HTTPException(status_code=422, detail="Enter your name.")
        user.name = name
    if payload.phone is not None:
        try:
            user.phone = account_service.normalize_phone(payload.phone)
        except AccountError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/onboarding/complete", response_model=UserResponse)
async def complete_onboarding(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if user.onboarded_at is None:
        user.onboarded_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
    return user


@router.post("/login", response_model=UserResponse)
async def login(payload: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    state = await auth_state(db)
    if state == "demo":
        raise HTTPException(status_code=400, detail="Accounts are disabled: the server is in demo mode (AUTH_MODE=demo).")
    if state == "setup":
        raise HTTPException(status_code=400, detail="No account exists yet. Create one to get started.")
    key = _throttle_key(request, payload.email)
    now = time.monotonic()
    _FAILS[key] = [t for t in _FAILS[key] if now - t < _WINDOW_S]
    if len(_FAILS[key]) >= _MAX_FAILS:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again in a few minutes.")

    user = (await db.execute(select(User).where(User.email == payload.email.strip().lower()))).scalar_one_or_none()
    # Verify against a dummy hash when the user is unknown so timing does not reveal which emails exist.
    stored = user.password_hash if user and user.password_hash else _DUMMY_HASH
    ok = verify_password(payload.password, stored)
    if not (user and user.password_hash and ok):
        _FAILS[key].append(now)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    _FAILS.pop(key, None)
    await _start_session(db, user, response)
    return user


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await db.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
        await db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user


_DUMMY_HASH = hash_password("dummy-password-for-timing")
