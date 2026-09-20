"""
alert_service.py
----------------
Notification delivery for due alerts (email and browser push) and the background scheduler.

An alert is "due" once today >= fire_on (deadline minus its lead time). Due alerts always show
on the dashboard. If the user turned on email and/or browser push (and the server has SMTP / VAPID
keys), each due alert is sent at once as a digest, then re-sent every repeat_hours until the user
marks it read, up to ALERT_MAX_REPEATS reminders. Both channels are optional: without them the app
works fully and simply says so.
"""

import asyncio
import logging
import secrets
import smtplib
import ssl
from zoneinfo import ZoneInfo
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Callable, Dict, List, Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.database import AsyncSessionLocal
from app.models.models import Alert, AlertSetting, AlertStatus, Contract, Deadline, Obligation, ObligationStatus, User
from app.services import account_service, push_service

logger = logging.getLogger(__name__)

DISCLAIMER = "AI-assisted, not legal advice. Check every date against the contract itself."

# Tests replace this to capture messages instead of opening a network connection.
Sender = Callable[[EmailMessage], None]


def _smtp_send(msg: EmailMessage) -> None:
    """Blocking SMTP delivery. Raises on failure; the caller reports it without ever logging credentials."""
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20) as smtp:
        if settings.SMTP_STARTTLS:
            smtp.starttls(context=ssl.create_default_context())
        if settings.SMTP_USER:
            smtp.login(settings.SMTP_USER, settings.smtp_password)
        smtp.send_message(msg)


_sender: Sender = _smtp_send


def set_sender(sender: Optional[Sender]) -> None:
    """Tests only: swap the SMTP transport."""
    global _sender
    _sender = sender or _smtp_send


def build_message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def digest_body(rows: List[Dict], today: date, *, ack_url: Optional[str] = None, dashboard_url: Optional[str] = None,
                reminder: int = 0, repeat_hours: Optional[int] = None, reminders_left: Optional[int] = None,
                late_note: Optional[str] = None, briefing: bool = False) -> str:
    lines = [f"ContractLens {'morning briefing' if briefing else ''}: {len(rows)} deadline(s) need attention.".replace("  ", " "), ""]
    if late_note:
        lines += [late_note, ""]
    if reminder:
        lines += [f"This is reminder {reminder}: these alerts have not been marked as read yet.", ""]
    for r in rows:
        d = r["deadline_date"]
        left = (d - today).days
        when = f"OVERDUE by {-left} day(s)" if left < 0 else ("due TODAY" if left == 0 else f"in {left} day(s)")
        lines.append(f"- {r['title']}: {r['label']}")
        lines.append(f"    {d.isoformat()} ({when})")
        if r.get("basis"):
            lines.append(f"    Basis: {r['basis']}")
        if r.get("source_page"):
            lines.append(f"    Source: page {r['source_page']}" + (f", {r['source_section']}" if r.get("source_section") else ""))
        lines.append("")
    if ack_url:
        lines += ["Mark these as read (stops the reminders):", f"  {ack_url}", ""]
    if dashboard_url:
        lines += ["Open ContractLens:", f"  {dashboard_url}", ""]
    if repeat_hours and reminders_left:
        lines += [f"If you do not mark them as read, ContractLens will remind you again in about {repeat_hours} hour(s) "
                  f"({reminders_left} more reminder(s) at most).", ""]
    lines += [DISCLAIMER, "You are receiving this because email alerts are turned on in ContractLens."]
    return "\n".join(lines)


async def send_message(msg: EmailMessage) -> None:
    """Send one email through the configured transport (raises on failure; callers never log credentials)."""
    await asyncio.to_thread(_sender, msg)


async def send_test_email(to: str) -> Optional[str]:
    """Returns None on success, otherwise a short reason (never contains credentials)."""
    if not settings.smtp_configured:
        return "Email is not configured on the server (set SMTP_HOST and SMTP_FROM in backend/.env)."
    try:
        await asyncio.to_thread(_sender, build_message(
            to, "ContractLens test email", f"This is a test email from ContractLens. Email alerts are working.\n\n{DISCLAIMER}"))
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Test email failed: %s", exc.__class__.__name__)
        return f"The email could not be sent ({exc.__class__.__name__}). Check the SMTP settings."


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _dedupe(rows: List[Dict]) -> List[Dict]:
    unique, seen = [], set()
    for r in sorted(rows, key=lambda r: r["deadline_date"]):
        key = (r["title"], r["label"], r["deadline_date"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _row(alert: Alert, title: str) -> Dict:
    dl: Deadline = alert.deadline
    return {"title": title, "label": dl.label, "deadline_date": dl.deadline_date, "basis": dl.basis,
            "source_page": dl.source_page, "source_section": dl.source_section}


def _select_for_channel(alerts: List[Alert], count_attr: str, at_attr: str, setting: AlertSetting, now: datetime) -> List[Alert]:
    """
    Alerts to include in this channel's next message, or [] if nothing is due to be sent.
    First notification goes out at once. While unread, a reminder follows every repeat_hours,
    up to ALERT_MAX_REPEATS reminders; with repeat off only never-notified alerts are sent.
    """
    cap = 1 + max(0, settings.ALERT_MAX_REPEATS)
    interval = timedelta(hours=max(1, setting.repeat_hours or 24))
    candidates = [a for a in alerts if getattr(a, count_attr) < cap and (setting.repeat_enabled or getattr(a, count_attr) == 0)]
    for a in candidates:
        last = _aware(getattr(a, at_attr))
        if getattr(a, count_attr) == 0 or last is None or now - last >= interval:
            return candidates
    return []


async def run_alert_cycle(today: Optional[date] = None, now: Optional[datetime] = None) -> Dict[str, int]:
    """
    Notify each user about due, unread alerts by email and/or browser push. Runs every few minutes;
    what is actually sent is decided per alert: first notification at once, then a reminder every
    repeat_hours until the alert is marked read (or the reminder cap is reached).
    Idempotent: running it twice in a row sends nothing the second time.
    """
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    result = {"users_emailed": 0, "alerts_sent": 0, "reminders_sent": 0, "failed": 0, "skipped_no_smtp": 0,
              "users_pushed": 0, "push_sent": 0, "push_failed": 0}
    email_ok, push_ok = settings.smtp_configured, settings.push_configured
    if not (email_ok or push_ok):
        return result

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(Alert, Contract.title, AlertSetting)
            .join(Contract, Contract.id == Alert.contract_id)
            .join(Deadline, Deadline.id == Alert.deadline_id)
            .outerjoin(Obligation, Obligation.id == Deadline.obligation_id)
            .join(AlertSetting, AlertSetting.user_id == Alert.user_id)
            .options(selectinload(Alert.deadline))
            .where(
                Alert.status.in_([AlertStatus.PENDING, AlertStatus.SENT]), Alert.fire_on <= today,
                Contract.deleted_at.is_(None), Deadline.contract_version_id == Contract.current_version_id,
                or_(Obligation.id.is_(None), Obligation.status != ObligationStatus.COMPLETED),
                or_(AlertSetting.email_enabled.is_(True), AlertSetting.push_enabled.is_(True)),
            )
            .order_by(Alert.fire_on)
        )).all()

        by_user: Dict = {}
        for alert, title, setting in rows:
            pack = by_user.setdefault(alert.user_id, {"setting": setting, "items": []})
            pack["items"].append((alert, title))

        for user_id, pack in by_user.items():
            setting: AlertSetting = pack["setting"]
            user = await db.get(User, user_id)
            local = now.astimezone(ZoneInfo(account_service.valid_timezone(setting.timezone)))
            if setting.paused_until and local.date() <= setting.paused_until:
                continue                                              # "pause all notifications until ..."
            titles = {a.id: t for a, t in pack["items"]}
            alerts = [a for a, _ in pack["items"]]
            if not setting.ack_token:
                setting.ack_token = secrets.token_urlsafe(24)
            base = settings.APP_BASE_URL.rstrip("/")
            ack_url, dash_url = f"{base}/ack/{setting.ack_token}", f"{base}/"

            # ── email ──
            unverified_own_address = bool(
                user is not None and user.consented_at is not None and user.email_verified_at is None
                and (setting.email_to or "").lower() == user.email.lower())   # never mail an address nobody has proven
            briefing_wait = bool(setting.briefing_enabled and (local.hour < setting.briefing_hour or setting.last_briefing_on == local.date()))
            if setting.email_enabled and setting.email_to and not unverified_own_address:
                if not email_ok:
                    result["skipped_no_smtp"] += 1
                elif briefing_wait:
                    pass                                              # email goes out once a day, at the briefing time
                else:
                    chosen = _select_for_channel(alerts, "email_count", "emailed_at", setting, now)
                    if chosen:
                        unique = _dedupe([_row(a, titles[a.id]) for a in chosen])
                        reminder = max(a.email_count for a in chosen)          # 0 = first notification
                        cap = 1 + max(0, settings.ALERT_MAX_REPEATS)
                        left = max(0, cap - 1 - reminder) if setting.repeat_enabled else 0
                        subject = (f"ContractLens reminder {reminder}: {len(unique)} deadline(s) still not marked as read"
                                   if reminder else f"ContractLens: {len(unique)} deadline(s) need attention")
                        late = None
                        if setting.briefing_enabled:
                            subject = f"Your ContractLens briefing: {len(unique)} deadline(s) need attention"
                            if local.hour >= setting.briefing_hour + 2:
                                late = (f"Sent late: ContractLens was not running at {setting.briefing_hour:02d}:00, your briefing time. "
                                        "Nothing was lost; this covers everything due since.")
                        body = digest_body(unique, today, ack_url=ack_url, dashboard_url=dash_url, reminder=reminder,
                                           repeat_hours=setting.repeat_hours if left else None, reminders_left=left,
                                           late_note=late, briefing=bool(setting.briefing_enabled))
                        try:
                            await asyncio.to_thread(_sender, build_message(setting.email_to, subject, body))
                        except Exception as exc:  # noqa: BLE001
                            logger.warning("Alert email failed (%s); will retry next cycle", exc.__class__.__name__)
                            result["failed"] += 1
                        else:
                            for a in chosen:
                                a.emailed_at, a.email_count = now, a.email_count + 1
                                if a.status == AlertStatus.PENDING:
                                    a.status = AlertStatus.SENT
                            if setting.briefing_enabled:
                                setting.last_briefing_on = local.date()
                            result["users_emailed"] += 1
                            result["alerts_sent"] += len(chosen)
                            result["reminders_sent"] += sum(1 for a in chosen if a.email_count > 1)

            # ── browser push ──
            if setting.push_enabled and push_ok:
                chosen = _select_for_channel(alerts, "push_count", "pushed_at", setting, now)
                if chosen:
                    unique = _dedupe([_row(a, titles[a.id]) for a in chosen])
                    reminder = max(a.push_count for a in chosen)
                    head = (f"Reminder: {len(unique)} deadline(s) still unread" if reminder
                            else f"{len(unique)} deadline(s) need attention")
                    lines = [f"{r['title']}: {r['label']} ({r['deadline_date'].isoformat()})" for r in unique[:3]]
                    if len(unique) > 3:
                        lines.append(f"and {len(unique) - 3} more")
                    sent = await push_service.send_to_user(db, user_id, push_service.payload(head, "\n".join(lines), dash_url))
                    if sent["sent"]:
                        for a in chosen:
                            a.pushed_at, a.push_count = now, a.push_count + 1
                            if a.status == AlertStatus.PENDING:
                                a.status = AlertStatus.SENT
                        result["users_pushed"] += 1
                        result["push_sent"] += sent["sent"]
                    result["push_failed"] += sent["failed"]
        await db.commit()
    return result


_task: Optional[asyncio.Task] = None


async def _loop() -> None:
    while True:
        try:
            await run_alert_cycle()
        except Exception:  # noqa: BLE001 - the scheduler must never die
            logger.exception("Alert cycle failed")
        await asyncio.sleep(max(1, settings.ALERT_CHECK_INTERVAL_MIN) * 60)


def start_scheduler() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.get_running_loop().create_task(_loop())


async def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        _task = None


async def briefing_preview(db, user, today: Optional[date] = None, now: Optional[datetime] = None) -> Dict:
    """What the next briefing would say, without sending anything or changing any state."""
    today = today or date.today()
    now = now or datetime.now(timezone.utc)
    setting = (await db.execute(select(AlertSetting).where(AlertSetting.user_id == user.id))).scalar_one_or_none()
    rows = (await db.execute(
        select(Alert, Contract.title)
        .join(Contract, Contract.id == Alert.contract_id)
        .join(Deadline, Deadline.id == Alert.deadline_id)
        .outerjoin(Obligation, Obligation.id == Deadline.obligation_id)
        .options(selectinload(Alert.deadline))
        .where(Alert.user_id == user.id, Alert.status.in_([AlertStatus.PENDING, AlertStatus.SENT]), Alert.fire_on <= today,
               Contract.deleted_at.is_(None), Deadline.contract_version_id == Contract.current_version_id,
               or_(Obligation.id.is_(None), Obligation.status != ObligationStatus.COMPLETED))
        .order_by(Alert.fire_on)
    )).all()
    unique = _dedupe([_row(a, t) for a, t in rows])
    base = settings.APP_BASE_URL.rstrip("/")
    body = digest_body(unique, today, dashboard_url=f"{base}/", briefing=True) if unique else ""
    reason = None
    if not unique:
        reason = "Nothing is due and unread, so no email would be sent. ContractLens never sends an empty briefing."
    elif setting is None or not setting.email_to:
        reason = "No email address is set for alerts."
    elif user.consented_at is not None and user.email_verified_at is None and setting.email_to.lower() == user.email.lower():
        reason = "Your email address is not verified yet, so nothing is sent to it."
    elif not settings.smtp_configured:
        reason = "Email sending is not set up on this server yet (run Setup-Email.cmd)."
    elif setting.paused_until and today <= setting.paused_until:
        reason = f"Notifications are paused until {setting.paused_until.isoformat()}."
    return {
        "subject": f"Your ContractLens briefing: {len(unique)} deadline(s) need attention" if unique else None,
        "body": body, "count": len(unique), "would_send": reason is None and bool(unique), "reason": reason,
        "items": [{"title": r["title"], "label": r["label"], "deadline_date": r["deadline_date"],
                   "days_left": (r["deadline_date"] - today).days, "source_page": r["source_page"]} for r in unique],
    }
