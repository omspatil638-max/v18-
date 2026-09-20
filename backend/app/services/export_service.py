"""
export_service.py
-----------------
Get your data out: the contract register as CSV, and a per-contract "evidence pack" (every value with
its status, page and quote, so someone else can check it without opening the app).

Both are plain, honest exports: unverified values say so, and nothing is summed or interpreted.
CSV cells that start with = + - @ are prefixed so a spreadsheet never runs them as formulas.
"""

import csv
import io
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Contract, ContractVersion, Document
from app.services.compare_service import FIELD_LABELS

KEY_FIELDS = ["effective_date", "expiration_date", "auto_renew", "renewal_notice_period",
              "termination_notice_period", "payment_terms", "renewal_terms", "termination_conditions"]
DISCLAIMER = "AI-assisted, not legal advice. Values were read from the document by software and can be wrong; check each one against its source page."


def safe_cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "")
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _is_reviewed(f) -> bool:
    return bool((f.notes or {}).get("user_review"))


async def register_csv(db: AsyncSession, user_id) -> str:
    rows = (await db.execute(
        select(Contract, ContractVersion)
        .join(ContractVersion, ContractVersion.id == Contract.current_version_id)
        .where(Contract.user_id == user_id, Contract.deleted_at.is_(None))
        .options(selectinload(ContractVersion.fields), selectinload(ContractVersion.flags), selectinload(ContractVersion.parties))
        .order_by(Contract.title)
    )).all()
    header = ["Title", "Counterparty", "Type", "Tags", "Parties", "Status", "Analysis", "Pages"]
    for k in KEY_FIELDS:
        label = FIELD_LABELS.get(k, k)
        header += [label, f"{label} (status)", f"{label} (page)"]
    header += ["Open flags", "High-priority open flags", "Uploaded", "Note"]
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(header)
    for contract, v in rows:
        by = {f.field_key: f for f in v.fields}
        open_flags = [f for f in v.flags if f.status.value == "OPEN"]
        line = [contract.title, contract.counterparty, contract.contract_type, "; ".join(contract.tags or []),
                "; ".join(p.name for p in v.parties), v.status.value, v.extraction_status, v.page_count]
        for k in KEY_FIELDS:
            f = by.get(k)
            if f is None or f.value is None:
                line += ["", "not found" if f is not None and f.status == "not_found" else ("not analysed" if f is not None else ""), ""]
            else:
                line += [f.display_value, ("verified" if f.status == "verified" else "needs review") + (" (reviewed by you)" if _is_reviewed(f) else ""), f.page]
        line += [len(open_flags), sum(1 for f in open_flags if f.severity.value == "HIGH"), contract.created_at.date().isoformat(), ""]
        w.writerow([safe_cell(c) for c in line])
    out.write("\r\n")
    w.writerow([safe_cell(DISCLAIMER)])
    return "﻿" + out.getvalue()                       # BOM so Excel reads UTF-8 correctly


def _md(text: Any) -> str:
    return " ".join(str(text or "").replace("|", "\\|").split())


async def evidence_markdown(db: AsyncSession, contract: Contract, version: ContractVersion) -> str:
    doc = (await db.execute(select(Document).where(Document.version_id == version.id))).scalars().first()
    v = (await db.execute(
        select(ContractVersion).where(ContractVersion.id == version.id).options(
            selectinload(ContractVersion.fields), selectinload(ContractVersion.parties), selectinload(ContractVersion.obligations),
            selectinload(ContractVersion.deadlines), selectinload(ContractVersion.flags), selectinload(ContractVersion.clauses))
    )).scalar_one()
    L: List[str] = [f"# Evidence pack: {contract.title}", "", f"> {DISCLAIMER}", ""]
    L += [f"- Version: {v.label}", f"- File: {doc.filename if doc else 'unknown'}" + (f" (SHA-256 `{doc.sha256}`)" if doc and doc.sha256 else ""),
          f"- Pages: {v.page_count}", f"- Analysis: {v.extraction_status}", f"- Exported: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC"]
    src = (v.extraction_meta or {}).get("source")
    if src and src.get("kind") == "ocr":
        L.append(f"- **Read from scanned pages by OCR** (pages {', '.join(str(p) for p in src.get('pages', []))}). OCR can misread digits and names; values from these pages are marked 'needs review' until checked.")
    if src and src.get("kind") == "docx":
        L.append("- Converted from a Word document; page numbers refer to the converted copy.")
    L += ["", "## Key terms", "", "| Term | Value | Status | Page | Source quote |", "|---|---|---|---|---|"]
    for f in sorted(v.fields, key=lambda f: KEY_FIELDS.index(f.field_key) if f.field_key in KEY_FIELDS else 99):
        status = "verified" if f.status == "verified" else f.status.replace("_", " ")
        if _is_reviewed(f):
            ur = f.notes["user_review"]
            status += f"; {ur.get('action', 'reviewed').replace('_', ' ')} by you"
        L.append(f"| {FIELD_LABELS.get(f.field_key, f.field_key)} | {_md(f.display_value) or '-'} | {status} | {f.page or '-'} | {_md(f.source_quote) or '-'} |")
        ur = (f.notes or {}).get("user_review")
        if ur and ur.get("original") and ur["original"].get("display_value") != f.display_value:
            L.append(f"|   ↳ original AI value | {_md(ur['original'].get('display_value')) or '-'} | {ur['original'].get('status')} | {ur['original'].get('page') or '-'} | {_md(ur['original'].get('source_quote')) or '-'} |")
    if v.parties:
        L += ["", "## Parties", "", "| Role | Name | Status | Page | Source quote |", "|---|---|---|---|---|"]
        L += [f"| {_md(p.role)} | {_md(p.name)} | {p.status} | {p.source_page or '-'} | {_md(p.source_quote) or '-'} |" for p in v.parties]
    if v.obligations:
        L += ["", "## Obligations", "", "| Who | Obligation | Due | Status | Page | Source quote |", "|---|---|---|---|---|---|"]
        L += [f"| {_md(o.responsible_party)} | {_md(o.action)} | {o.due_date.isoformat() if o.due_date else _md(o.due_rule)} | {o.verification_status} | {o.source_page or '-'} | {_md(o.source_quote) or '-'} |"
              for o in v.obligations]
    if v.deadlines:
        L += ["", "## Deadlines (worked out in code)", "", "| Date | What | How it was worked out | Page |", "|---|---|---|---|"]
        L += [f"| {d.deadline_date.isoformat()} | {_md(d.label)} | {_md(d.basis) or '-'} | {d.source_page or '-'} |" for d in sorted(v.deadlines, key=lambda d: d.deadline_date)]
    if v.flags:
        L += ["", "## Review flags", "", "| Severity | State | Flag | Page |", "|---|---|---|---|"]
        L += [f"| {f.severity.value.title()} | {f.status.value.title()} | {_md(f.reason)} | {f.source_page or '-'} |" for f in v.flags]
    L += ["", "---", DISCLAIMER, ""]
    return "\n".join(L)
