"""
compare_service.py
------------------
Compare two versions of the same contract.

  1. SECTIONS  matched by heading, then by text similarity; each labelled
               UNCHANGED / MODIFIED / ADDED / REMOVED.
  2. KEY TERMS compared IN CODE (dates, notice periods, amounts, renewal, parties). No model
               decides whether a number changed.
  3. IMPACT    the LLM only EXPLAINS what a material change means, in one plain sentence. Any
               number in that sentence must appear in the two quoted passages; otherwise the
               explanation is discarded rather than shown.

Every change carries the section, page and quote from BOTH versions, so it can be opened at the
source. Nothing here is legal advice.
"""

import difflib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    ChangeType, Contract, ContractParty, ContractVersion, ExtractedField, FieldStatus, VersionChange,
)
from app.services import validators as v
from app.services.llm_service import LLMError, LLMUnavailable, llm_service
from app.services.text_index import DocIndex, Section, normalize, pages_from_raw_text

logger = logging.getLogger(__name__)

MATCH_MIN_SIMILARITY = 0.5
MAX_SECTION_TEXT = 6000
MAX_IMPACTS = 8
MAX_IMPACT_CHARS = 300

FIELD_LABELS = {
    "effective_date": "Effective date", "expiration_date": "Expiration date", "renewal_terms": "Renewal terms",
    "auto_renew": "Auto-renewal", "renewal_notice_period": "Renewal notice period", "payment_terms": "Payment amount",
    "termination_conditions": "Termination conditions", "termination_notice_period": "Termination notice",
}
COMPARE_KEYS = list(FIELD_LABELS)
MATERIAL_KEYS = {"effective_date", "expiration_date", "renewal_notice_period", "termination_notice_period",
                 "auto_renew", "payment_terms"}
# A changed SECTION is treated as material when its heading/text is about one of these.
MATERIAL_TOPICS = re.compile(
    r"terminat|renew|payment|fees?|price|liabilit|indemn|confidential|data protection|governing law|"
    r"intellectual property|warrant|non-?compete|exclusiv|assign", re.I)


# ─── sections ─────────────────────────────────────────────────────────────────

def _title(sec: Section) -> str:
    """Heading without numbering, so '9. Termination' matches '12. Termination'."""
    t = re.sub(r"^(?:section|article|schedule|exhibit)?\s*[0-9ivxlc.]+[.)]?\s*", "", (sec.heading or "").lower()).strip()
    return re.sub(r"[^a-z0-9 ]+", "", t)


def body_of(sec: Section) -> str:
    """Section text WITHOUT its heading line, so renumbering ('1. Termination' -> '9. Termination')
    or restyling a heading is not reported as a change to the clause itself."""
    lines = sec.text.split("\n", 1)
    first = normalize(lines[0])
    if len(lines) == 2 and (sec.number is not None or normalize(sec.heading or "") in first):
        return lines[1]
    return sec.text


def _words(text: str) -> List[str]:
    return normalize(text).split()


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass
class SecMatch:
    old: Optional[Section]
    new: Optional[Section]
    kind: ChangeType
    similarity: float = 0.0


def match_sections(old_secs: List[Section], new_secs: List[Section]) -> List[SecMatch]:
    """Pair sections by heading first, then by best text similarity; the rest are added/removed."""
    pairs: Dict[int, int] = {}                          # new index -> old index
    used_old: set = set()

    by_title: Dict[str, List[int]] = {}
    for i, s in enumerate(old_secs):
        by_title.setdefault(_title(s), []).append(i)
    for j, s in enumerate(new_secs):
        t = _title(s)
        if t and by_title.get(t):
            i = by_title[t].pop(0)
            pairs[j] = i
            used_old.add(i)

    old_sets = {i: set(_words(body_of(s))) for i, s in enumerate(old_secs) if i not in used_old}
    candidates: List[Tuple[float, int, int]] = []
    for j, s in enumerate(new_secs):
        if j in pairs:
            continue
        nw = set(_words(body_of(s)))
        for i, ow in old_sets.items():
            sim = _jaccard(nw, ow)
            if sim >= MATCH_MIN_SIMILARITY:
                candidates.append((sim, j, i))
    for sim, j, i in sorted(candidates, reverse=True):
        if j not in pairs and i not in used_old:
            pairs[j] = i
            used_old.add(i)

    out: List[Tuple[float, SecMatch]] = []
    new_pos_of_old = {i: j for j, i in pairs.items()}
    for j, s in enumerate(new_secs):
        if j in pairs:
            o = old_secs[pairs[j]]
            ob, nb = body_of(o), body_of(s)
            same = normalize(ob) == normalize(nb)
            sim = 1.0 if same else difflib.SequenceMatcher(None, _words(ob), _words(nb), autojunk=False).ratio()
            out.append((float(j), SecMatch(o, s, ChangeType.UNCHANGED if same else ChangeType.MODIFIED, sim)))
        else:
            out.append((float(j), SecMatch(None, s, ChangeType.ADDED)))
    for i, o in enumerate(old_secs):
        if i in used_old:
            continue
        before = [k for k in new_pos_of_old if k < i]
        key = (new_pos_of_old[max(before)] + 0.5) if before else -0.5
        out.append((key, SecMatch(o, None, ChangeType.REMOVED)))
    out.sort(key=lambda t: t[0])
    return [m for _, m in out]


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.;:])\s+|\n+", text)
    return [" ".join(p.split()) for p in parts if p and p.strip()]


def changed_sentences(old_text: str, new_text: str) -> Tuple[List[str], List[str]]:
    """The sentences that were removed from the old text and added in the new one."""
    a, b = _sentences(old_text), _sentences(new_text)
    na, nb = [normalize(x) for x in a], [normalize(x) for x in b]
    removed: List[str] = []
    added: List[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, na, nb, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            removed += a[i1:i2]
        if op in ("replace", "insert"):
            added += b[j1:j2]
    return removed, added


def word_diff(old_text: str, new_text: str) -> List[Dict[str, str]]:
    """Word-level segments [{op: equal|delete|insert, text}] for highlighting in the UI."""
    a, b = old_text.split(), new_text.split()
    segs: List[Dict[str, str]] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            segs.append({"op": "equal", "text": " ".join(a[i1:i2])})
            continue
        if i2 > i1:
            segs.append({"op": "delete", "text": " ".join(a[i1:i2])})
        if j2 > j1:
            segs.append({"op": "insert", "text": " ".join(b[j1:j2])})
    return segs


# ─── key terms, compared in code ──────────────────────────────────────────────

def _usable(f: Optional[ExtractedField]) -> bool:
    return f is not None and f.status not in (FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value) \
        and bool(f.display_value)


def _fmt_amounts(f: ExtractedField) -> str:
    amounts = sorted(v.amounts_in(f.display_value) | v.amounts_in(f.source_quote), key=lambda a: float(a))
    return ", ".join(f"{float(a):,.2f}".rstrip("0").rstrip(".") if "." in a else f"{int(a):,}" for a in amounts)


def _canonical(f: Optional[ExtractedField]) -> Optional[str]:
    if not _usable(f):
        return None
    if f.field_key == "payment_terms":
        return _fmt_amounts(f) or None
    if f.field_key == "auto_renew":
        return "Yes" if (f.value or {}).get("bool") else "No"
    if f.field_key.endswith("notice_period") and f.value and f.value.get("days") is not None:
        return f"{f.value['value']} {f.value['unit']}"
    if f.field_key in ("renewal_terms", "termination_conditions"):
        return normalize(f.display_value)
    return f.display_value


def _shown(f: ExtractedField) -> str:
    if f.field_key == "payment_terms":
        return _fmt_amounts(f) or f.display_value
    return _canonical(f) if f.field_key not in ("renewal_terms", "termination_conditions") else f.display_value


@dataclass
class Draft:
    change_type: ChangeType
    category: str
    label: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_quote: Optional[str] = None
    new_quote: Optional[str] = None
    old_page: Optional[int] = None
    new_page: Optional[int] = None
    old_section: Optional[str] = None
    new_section: Optional[str] = None
    is_material: bool = False


def compare_fields(old_fields: List[ExtractedField], new_fields: List[ExtractedField],
                   old_parties: List[ContractParty], new_parties: List[ContractParty]) -> Tuple[List[Draft], List[str]]:
    out: List[Draft] = []
    notes: List[str] = []
    old_by, new_by = {f.field_key: f for f in old_fields}, {f.field_key: f for f in new_fields}

    for key in COMPARE_KEYS:
        o, n = old_by.get(key), new_by.get(key)
        if any(f is not None and f.status == FieldStatus.EXTRACTION_UNAVAILABLE.value for f in (o, n)):
            notes.append(f"{FIELD_LABELS[key]} could not be compared because extraction was unavailable for one version.")
            continue
        co, cn = _canonical(o), _canonical(n)
        if co == cn:
            continue
        ctype = ChangeType.ADDED if co is None else ChangeType.REMOVED if cn is None else ChangeType.MODIFIED
        text_only = key in ("renewal_terms", "termination_conditions")
        out.append(Draft(
            ctype, key, FIELD_LABELS[key],
            _shown(o) if co is not None else None, _shown(n) if cn is not None else None,
            o.source_quote if o else None, n.source_quote if n else None,
            o.page if o else None, n.page if n else None, o.section if o else None, n.section if n else None,
            is_material=(key in MATERIAL_KEYS) and not text_only,
        ))

    old_names = {normalize(v.normalize_party_name(p.name)): p for p in old_parties}
    new_names = {normalize(v.normalize_party_name(p.name)): p for p in new_parties}
    for key, p in new_names.items():
        if key not in old_names:
            out.append(Draft(ChangeType.ADDED, "party", "Party added", None, p.name, None, p.source_quote,
                             None, p.source_page, None, p.source_section, is_material=True))
    for key, p in old_names.items():
        if key not in new_names:
            out.append(Draft(ChangeType.REMOVED, "party", "Party removed", p.name, None, p.source_quote, None,
                             p.source_page, None, p.source_section, None, is_material=True))
    return out, notes


def compare_section_matches(matches: List[SecMatch], new_doc: DocIndex, old_doc: DocIndex) -> List[Draft]:
    out: List[Draft] = []

    def locate(doc: DocIndex, sentences: List[str], sec: Section) -> Tuple[str, int, str]:
        """A real passage from `doc` for the change (first changed sentence that can be verified)."""
        for s in sentences:
            m = doc.find(s[:400])
            if m is not None:
                return " ".join(m.matched_text.split()), m.page_start, m.section or sec.label
        return " ".join(sec.text.split())[:300], sec.page_start, sec.label

    for m in matches:
        if m.kind == ChangeType.UNCHANGED:
            out.append(Draft(ChangeType.UNCHANGED, "section", m.new.label, None, None, None, None,
                             m.old.page_start, m.new.page_start, m.old.label, m.new.label))
            continue
        topic = bool(MATERIAL_TOPICS.search(f"{(m.new or m.old).heading} {(m.new or m.old).text[:400]}"))
        if m.kind == ChangeType.ADDED:
            q, pg, sc = locate(new_doc, _sentences(m.new.text), m.new)
            out.append(Draft(ChangeType.ADDED, "section", m.new.label, None, " ".join(m.new.text.split())[:MAX_SECTION_TEXT],
                             None, q, None, pg, None, sc, is_material=topic))
        elif m.kind == ChangeType.REMOVED:
            q, pg, sc = locate(old_doc, _sentences(m.old.text), m.old)
            out.append(Draft(ChangeType.REMOVED, "section", m.old.label, " ".join(m.old.text.split())[:MAX_SECTION_TEXT], None,
                             q, None, pg, None, sc, None, is_material=topic))
        else:
            removed, added = changed_sentences(body_of(m.old), body_of(m.new))
            oq, opg, osc = locate(old_doc, removed or _sentences(m.old.text), m.old)
            nq, npg, nsc = locate(new_doc, added or _sentences(m.new.text), m.new)
            out.append(Draft(ChangeType.MODIFIED, "section", m.new.label,
                             " ".join(removed)[:MAX_SECTION_TEXT] or None, " ".join(added)[:MAX_SECTION_TEXT] or None,
                             oq, nq, opg, npg, osc, nsc, is_material=topic))
    return out


# ─── impact (LLM explains; code verifies) ─────────────────────────────────────

IMPACT_SCHEMA = {
    "type": "object",
    "properties": {"impacts": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "integer"}, "impact": {"type": "string"}},
        "required": ["id", "impact"], "additionalProperties": False}}},
    "required": ["impacts"], "additionalProperties": False,
}
IMPACT_SYSTEM = """You explain what a change between two versions of a contract means in practice, for a business reader.

Each change below shows the OLD and NEW text. For every change write ONE plain sentence (at most 40 words) on its practical consequence.
Rules:
- Use only the facts shown. Do NOT introduce any number, date or name that is not in the change text.
- Say who is affected only if the text says so; otherwise describe the effect neutrally (for example: "a longer notice period means ending the contract needs more advance planning").
- If a value is newly added or removed, say what that means (for example: "the contract now renews automatically unless notice is given").
- No legal advice, no opinion on enforceability, no speculation.
Return JSON: {"impacts": [{"id": <the change id>, "impact": "<sentence>"}]}."""

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")}


def impact_is_grounded(impact: str, *sources: Optional[str]) -> bool:
    """Every number in the explanation must appear in the change text (words for numbers are allowed)."""
    if not impact or len(impact) > MAX_IMPACT_CHARS:
        return False
    allowed: set = set()
    for s in sources:
        allowed |= _numbers(s or "")
    return _numbers(impact) <= allowed


async def explain_impacts(changes: List[VersionChange]) -> Tuple[Dict[Any, str], str]:
    """Returns ({change.id: sentence}, status) with status complete | partial | unavailable | none."""
    targets = [c for c in changes if c.is_material and c.change_type != ChangeType.UNCHANGED]
    targets.sort(key=lambda c: (c.category == "section", c.label))
    targets = targets[:MAX_IMPACTS]
    if not targets:
        return {}, "none"
    if not llm_service.is_configured:
        return {}, "unavailable"

    def show(c: VersionChange, i: int) -> str:
        return (f"[{i}] {c.label} ({c.change_type.value})\n  OLD: {(c.old_value or '(absent)')[:300]}\n  NEW: {(c.new_value or '(absent)')[:300]}\n"
                f"  OLD passage: {(c.old_quote or '')[:220]}\n  NEW passage: {(c.new_quote or '')[:220]}")

    try:
        data = await llm_service.complete_json(
            IMPACT_SYSTEM, "<changes>\n" + "\n\n".join(show(c, i) for i, c in enumerate(targets, 1)) + "\n</changes>",
            IMPACT_SCHEMA, "change_impacts", max_tokens=1200)
    except (LLMUnavailable, LLMError) as exc:
        logger.warning("Impact explanation unavailable: %s", exc)
        return {}, "unavailable"

    out: Dict[Any, str] = {}
    for item in (data or {}).get("impacts", []) if isinstance(data, dict) else []:
        i = item.get("id")
        if not isinstance(i, int) or not 1 <= i <= len(targets):
            continue
        c = targets[i - 1]
        text = " ".join(str(item.get("impact") or "").split())
        if impact_is_grounded(text, c.old_value, c.new_value, c.old_quote, c.new_quote, c.label):
            out[c.id] = text
    return out, ("complete" if len(out) == len(targets) else "partial" if out else "unavailable")


# ─── orchestration ────────────────────────────────────────────────────────────

async def compare_versions(db: AsyncSession, contract: Contract, old: ContractVersion, new: ContractVersion) -> Dict[str, Any]:
    """(Re)compute and store the differences between two READY versions."""
    docs = []
    for ver in (old, new):
        docs.append(DocIndex(pages_from_raw_text(ver.raw_text)))
    old_doc, new_doc = docs

    old_fields = (await db.execute(select(ExtractedField).where(ExtractedField.contract_version_id == old.id))).scalars().all()
    new_fields = (await db.execute(select(ExtractedField).where(ExtractedField.contract_version_id == new.id))).scalars().all()
    old_parties = (await db.execute(select(ContractParty).where(ContractParty.contract_version_id == old.id))).scalars().all()
    new_parties = (await db.execute(select(ContractParty).where(ContractParty.contract_version_id == new.id))).scalars().all()

    field_drafts, notes = compare_fields(old_fields, new_fields, old_parties, new_parties)
    section_drafts = compare_section_matches(match_sections(old_doc.sections, new_doc.sections), new_doc, old_doc)

    await db.execute(delete(VersionChange).where(
        VersionChange.from_version_id == old.id, VersionChange.to_version_id == new.id))
    rows: List[VersionChange] = []
    for d in field_drafts + section_drafts:
        row = VersionChange(
            contract_id=contract.id, from_version_id=old.id, to_version_id=new.id, change_type=d.change_type,
            category=d.category, label=d.label[:255], old_value=d.old_value, new_value=d.new_value,
            old_quote=d.old_quote, new_quote=d.new_quote, old_page=d.old_page, new_page=d.new_page,
            old_section=d.old_section, new_section=d.new_section, is_material=d.is_material,
        )
        db.add(row)
        rows.append(row)
    await db.flush()

    impacts, impact_status = await explain_impacts(rows)
    for r in rows:
        if r.id in impacts:
            r.impact_text = impacts[r.id]

    meta = dict(new.extraction_meta or {})
    meta["comparison"] = {
        "from_version_id": str(old.id), "status": "ready", "impact_status": impact_status,
        "notes": notes, "at": datetime.utcnow().isoformat(),
    }
    new.extraction_meta = meta
    await db.commit()
    return {"changes": len(rows), "impact_status": impact_status, "notes": notes}


async def previous_ready_version(db: AsyncSession, version: ContractVersion) -> Optional[ContractVersion]:
    return (await db.execute(
        select(ContractVersion).where(
            ContractVersion.contract_id == version.contract_id,
            ContractVersion.version_number < version.version_number,
            ContractVersion.raw_text.isnot(None),
        ).order_by(ContractVersion.version_number.desc()).limit(1)
    )).scalar_one_or_none()
