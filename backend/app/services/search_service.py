"""
search_service.py
-----------------
One search box over everything the user owns (Ctrl+K): contracts, extracted values and passages of
the contract text. Only live (not deleted) contracts, and only their current version, are searched.

Passage hits come with the page and a quote taken from the real document text, so the result can
open the source viewer with the passage highlighted. Highlight markers are plain characters, never
HTML, because contract text is untrusted.
"""

import re
from typing import Any, Dict, List

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.models import Contract, ContractVersion, ExtractedField, FieldStatus
from app.services.compare_service import FIELD_LABELS
from app.services.vector_service import build_or_query

MARK_START, MARK_END = "«", "»"
MAX_CONTRACTS, MAX_FIELDS, MAX_PASSAGES = 6, 6, 8


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def clean_query(q: str) -> str:
    return " ".join((q or "").replace("\x00", " ").split())[:200]


async def search(db: AsyncSession, user_id, q: str, lenient: bool = False) -> Dict[str, Any]:
    """`lenient`: for a natural-language question. Passages that contain ANY of its content words are
    ranked (instead of requiring every word), because 'what does it say about X' never appears in a contract."""
    q = clean_query(q)
    out: Dict[str, Any] = {"query": q, "contracts": [], "fields": [], "passages": []}
    if len(q) < 2:
        return out
    low = q.lower()

    # ── contracts: title, counterparty, type, tags, party names ──
    rows = (await db.execute(
        select(Contract).where(Contract.user_id == user_id, Contract.deleted_at.is_(None))
        .options(selectinload(Contract.versions).selectinload(ContractVersion.parties))
    )).scalars().all()
    scored = []
    for c in rows:
        cur = next((v for v in c.versions if v.id == c.current_version_id), None)
        parties = [p.name for p in (cur.parties if cur else [])]
        hay = {"title": c.title, "counterparty": c.counterparty or "", "type": c.contract_type or "",
               "tag": " ".join(c.tags or []), "party": " ".join(parties)}
        matched = [k for k, v in hay.items() if low in v.lower()]
        if not matched:
            continue
        rank = 0 if hay["title"].lower().startswith(low) else 1 if "title" in matched else 2
        why = {"title": None, "counterparty": f"Counterparty: {c.counterparty}", "type": f"Type: {c.contract_type}",
               "tag": "Tagged: " + ", ".join(t for t in (c.tags or []) if low in t.lower()),
               "party": "Party: " + next((p for p in parties if low in p.lower()), "")}[next(k for k in ("title", "counterparty", "party", "type", "tag") if k in matched)]
        scored.append((rank, c.title.lower(), {"contract_id": c.id, "title": c.title, "detail": why,
                                                "contract_type": c.contract_type, "tags": list(c.tags or []),
                                                "expiry_date": cur.expiry_date if cur else None}))
    scored.sort(key=lambda t: (t[0], t[1]))
    out["contracts"] = [t[2] for t in scored[:MAX_CONTRACTS]]

    # ── extracted values: 'auto-renews', '60 days', an amount, a name ──
    frows = (await db.execute(
        select(ExtractedField, Contract.title, Contract.id)
        .join(Contract, Contract.current_version_id == ExtractedField.contract_version_id)
        .where(Contract.user_id == user_id, Contract.deleted_at.is_(None),
               ExtractedField.status.notin_([FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value]),
               ExtractedField.display_value.ilike(_like(q), escape="\\"))
        .limit(MAX_FIELDS)
    )).all()
    out["fields"] = [{
        "contract_id": cid, "title": title, "label": FIELD_LABELS.get(f.field_key, f.field_key),
        "value": (f.display_value or "")[:240], "status": f.status, "page": f.page, "quote": f.source_quote,
    } for f, title, cid in frows]

    # ── passages of the contract text (full text search, current versions only) ──
    sql = (
        """
        SELECT c.contract_id, ct.title, c.source_page, c.source_section,
               ts_headline('english', c.content, tq,
                           'StartSel=%s, StopSel=%s, MaxWords=30, MinWords=12, MaxFragments=1') AS snippet,
               ts_rank_cd(c.tsv, tq) AS rank
        FROM contract_chunks c
        JOIN contracts ct ON ct.id = c.contract_id AND c.contract_version_id = ct.current_version_id,
             %s tq
        WHERE ct.user_id = :uid AND ct.deleted_at IS NULL AND c.tsv @@ tq
        ORDER BY rank DESC, c.source_page
        LIMIT :n
        """
    )
    prows = (await db.execute(text(sql % (MARK_START, MARK_END, "websearch_to_tsquery('english', :q)")),
                              {"q": q, "uid": user_id, "n": MAX_PASSAGES})).all()
    if not prows and lenient:
        or_q = build_or_query(q)
        if or_q:
            prows = (await db.execute(text(sql % (MARK_START, MARK_END, "to_tsquery('english', :q)")),
                                      {"q": or_q, "uid": user_id, "n": MAX_PASSAGES})).all()
    for cid, title, page, section, snippet, _rank in prows:
        snippet = (snippet or "").replace("\x00", "")
        plain = re.sub(r"\s+", " ", snippet.replace(MARK_START, "").replace(MARK_END, "")).strip()
        out["passages"].append({"contract_id": cid, "title": title, "page": page, "section": section,
                                "snippet": re.sub(r"\s+", " ", snippet).strip(), "quote": plain})
    return out


async def tag_counts(db: AsyncSession, user_id) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(Contract.tags).where(Contract.user_id == user_id, Contract.deleted_at.is_(None))
    )).scalars().all()
    counts: Dict[str, Dict[str, Any]] = {}
    for tags in rows:
        for t in tags or []:
            e = counts.setdefault(t.lower(), {"name": t, "count": 0})
            e["count"] += 1
    return sorted(counts.values(), key=lambda e: (-e["count"], e["name"].lower()))


async def type_counts(db: AsyncSession, user_id) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(Contract.contract_type).where(Contract.user_id == user_id, Contract.deleted_at.is_(None), Contract.contract_type.isnot(None))
    )).scalars().all()
    counts: Dict[str, int] = {}
    for t in rows:
        counts[t] = counts.get(t, 0) + 1
    return sorted(({"name": k, "count": v} for k, v in counts.items()), key=lambda e: (-e["count"], e["name"].lower()))
