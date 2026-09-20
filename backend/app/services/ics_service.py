"""
ics_service.py
--------------
RFC 5545 iCalendar export of deadlines, so they can be imported into Google/Outlook/Apple
Calendar. All-day events; each carries the source page/section and the AI-assisted disclaimer,
plus a reminder for every configured lead time. Hand-rolled (no dependency); strictly escaped
and line-folded so calendar apps accept it.
"""

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, List, Optional

PRODID = "-//ContractLens//Deadlines//EN"
DISCLAIMER = "AI-assisted, not legal advice. Verify against the contract."


def _escape(text: Optional[str]) -> str:
    """RFC 5545 TEXT escaping: backslash, semicolon, comma and newlines."""
    t = (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return t.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def _fold(line: str) -> List[str]:
    """Lines must be at most 75 octets; continuation lines start with one space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return [line]
    out, cur = [], b""
    limit = 75
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > limit:
            out.append(cur.decode("utf-8"))
            cur, limit = b"", 74          # continuation lines lose one octet to the leading space
        cur += b
    if cur:
        out.append(cur.decode("utf-8"))
    return [out[0]] + [" " + p for p in out[1:]]


def _uid(key: str) -> str:
    return hashlib.sha1(key.encode("utf-8")).hexdigest() + "@contractlens"


def build_ics(events: Iterable[dict], name: str = "ContractLens deadlines", lead_days: Optional[List[int]] = None,
              now: Optional[datetime] = None) -> str:
    """
    events: dicts with keys id, date (date), title, description (optional), url (optional).
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    lines: List[str] = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(name)}",
    ]
    for ev in events:
        d: date = ev["date"]
        desc = (ev.get("description") or "").strip()
        desc = f"{desc}\n\n{DISCLAIMER}" if desc else DISCLAIMER
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_uid(str(ev['id']))}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_escape(ev['title'])}",
            f"DESCRIPTION:{_escape(desc)}",
            "TRANSP:TRANSPARENT",
            "CATEGORIES:ContractLens",
        ]
        if ev.get("url"):
            lines.append(f"URL:{ev['url']}")
        for lead in sorted({n for n in (lead_days or []) if n > 0}, reverse=True):
            lines += [
                "BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape('Due in ' + str(lead) + ' days: ' + ev['title'])}",
                f"TRIGGER:-P{lead}D", "END:VALARM",
            ]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    folded: List[str] = []
    for ln in lines:
        folded.extend(_fold(ln))
    return "\r\n".join(folded) + "\r\n"
