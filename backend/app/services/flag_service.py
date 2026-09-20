"""
flag_service.py
---------------
"Needs human review" flags. Each flag carries a plain-English reason and a link to the
source text. Two kinds of rules, both deterministic (no LLM decides what is risky):

  * rules over EXTRACTED data      low confidence, quote not found, missing field, conflicting
                                   dates, auto-renewal, terms changed by an amendment
  * rules over the DOCUMENT TEXT   vague wording, uncapped/absent liability cap, unusual
                                   termination rights. These do not need an LLM, so they still
                                   work when extraction is unavailable.

A flag is a prompt for a person to check something. It is not a legal conclusion.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import (
    Clause, Contract, ContractParty, ContractVersion, Document, DocumentKind, ExtractedField,
    FieldStatus, FlagSeverity, FlagStatus, Obligation, ReviewFlag,
)
from app.services import validators as v
from app.services.text_index import DocIndex

logger = logging.getLogger(__name__)

H, M, L = FlagSeverity.HIGH, FlagSeverity.MEDIUM, FlagSeverity.LOW
SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

LOW_CONFIDENCE_BELOW = 0.6
MAX_VAGUE_FLAGS = 12
MAX_ITEM_FLAGS = 10          # per category (obligations / clauses / parties), to avoid flooding the queue

FIELD_LABELS = {
    "effective_date": "Effective date", "expiration_date": "Expiration date", "renewal_terms": "Renewal terms",
    "auto_renew": "Auto-renewal", "renewal_notice_period": "Renewal notice period", "payment_terms": "Payment terms",
    "termination_conditions": "Termination conditions", "termination_notice_period": "Termination notice period",
}
# Missing these matters most; the severity says how much.
REQUIRED_FIELDS = {
    "expiration_date": (H, "No end date was found. The contract may run indefinitely, or the date may be stated in a way that could not be read."),
    "effective_date": (M, "No effective (start) date was found, so the term and any date-based deadlines cannot be worked out."),
    "payment_terms": (M, "No payment terms were found. Check how much is owed, and when."),
    "termination_conditions": (M, "No termination terms were found. Check how, and on what notice, either party can end the contract."),
    "renewal_terms": (L, "No renewal terms were found. Check what happens when the term ends."),
}
HIGH_STAKES_FIELDS = {"effective_date", "expiration_date", "renewal_notice_period", "termination_notice_period",
                      "auto_renew", "payment_terms"}
AMENDMENT_KEYS = ("expiration_date", "effective_date", "termination_notice_period", "renewal_notice_period",
                  "auto_renew", "payment_terms")


@dataclass
class FlagDraft:
    code: str
    severity: FlagSeverity
    reason: str
    target_type: Optional[str] = None
    target_id: Optional[uuid.UUID] = None
    target_label: Optional[str] = None
    page: Optional[int] = None
    section: Optional[str] = None
    quote: Optional[str] = None


# ─── Vague wording ────────────────────────────────────────────────────────────

_VAGUE: List[Tuple[re.Pattern, FlagSeverity, str, str]] = [
    (re.compile(r"\bbest\s+(?:efforts|endeavou?rs)\b", re.I), M, "best efforts",
     "It is unclear how far a party must go, and courts and parties often read it very strictly."),
    (re.compile(r"\b(?:commercially\s+)?reasonable\s+(?:efforts|endeavou?rs)\b", re.I), L, "reasonable efforts",
     "It does not say what effort is required or how it is measured, so a shortfall is hard to prove or dispute."),
    (re.compile(r"\b(?:as\s+(?:mutually\s+)?agreed|to\s+be\s+(?:mutually\s+)?agreed|to\s+be\s+determined|tbd)\b", re.I), M, "as agreed / to be determined",
     "The term is left open, so it is not actually settled by this contract."),
    (re.compile(r"\b(?:in|at)\s+(?:its|his|her|their|the\s+\w+'s)\s+(?:sole\s+|absolute\s+)?discretion\b|\bsole\s+discretion\b", re.I), M, "at its discretion",
     "One party can decide alone, with no standard the other party can rely on."),
    (re.compile(r"\bfrom\s+time\s+to\s+time\b", re.I), L, "from time to time",
     "There is no fixed schedule or frequency."),
    (re.compile(r"\b(?:promptly|as\s+soon\s+as\s+(?:practicable|possible|reasonably\s+practicable)|without\s+undue\s+delay|within\s+a\s+reasonable\s+(?:time|period))\b", re.I), L, "no fixed deadline",
     "It does not give a number of days, so it is unclear when the obligation is late."),
]


def _sentence_around(text: str, start: int, end: int, cap: int = 260) -> str:
    left = max(text.rfind(". ", 0, start), text.rfind("\n\n", 0, start), text.rfind("; ", 0, start))
    right_candidates = [i for i in (text.find(". ", end), text.find("\n\n", end), text.find("; ", end)) if i != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    s = " ".join(text[left + 1 if left != -1 else 0: right].split())
    if len(s) > cap:                                  # keep the match inside the window
        centre = start - (left + 1 if left != -1 else 0)
        a = max(0, min(centre - cap // 2, len(s) - cap))
        s = s[a:a + cap].strip()
    return s


def _locate(doc: DocIndex, sentence: str, sec) -> Tuple[Optional[int], Optional[str], str]:
    m = doc.find(sentence)
    if m is not None:
        return m.page_start, m.section, " ".join(m.matched_text.split())
    return sec.page_start, sec.label, sentence


def vague_wording_flags(doc: DocIndex) -> List[FlagDraft]:
    out: List[FlagDraft] = []
    seen: set = set()
    for sec in doc.sections:
        for pattern, severity, name, why in _VAGUE:
            m = pattern.search(sec.text)
            if not m or (name, sec.label) in seen:
                continue
            seen.add((name, sec.label))
            page, section, quote = _locate(doc, _sentence_around(sec.text, m.start(), m.end()), sec)
            out.append(FlagDraft(
                "VAGUE_WORDING", severity,
                f"“{m.group(0).strip()}” is open to interpretation. {why}",
                "document", None, name, page, section, quote,
            ))
    out.sort(key=lambda f: (SEVERITY_RANK[f.severity.value], f.page or 10 ** 6))
    return out[:MAX_VAGUE_FLAGS]


# ─── Liability ────────────────────────────────────────────────────────────────

_UNCAPPED = re.compile(
    r"liabilit\w*[^.]{0,100}\b(?:shall|will|is|are|remains?)\s+(?:be\s+)?(?:unlimited|uncapped)\b|unlimited\s+liabilit|liabilit\w*\s+(?:shall|will)\s+not\s+be\s+(?:limited|capped)|"
    r"no\s+(?:limit|cap|limitation)\s+(?:on|to|of)\s+(?:the\s+)?(?:\w+\s+)?liabilit|\buncapped\b|without\s+limit(?:ation)?\s+(?:as\s+to\s+)?liabilit", re.I)
_CAP = re.compile(
    r"(?:aggregate|total|maximum|overall)\s+liabilit\w*[^.]{0,160}?(?:not\s+exceed|limited\s+to|capped|shall\s+not\s+exceed)|"
    r"limitation\s+of\s+liabilit|liabilit\w*[^.]{0,80}(?:shall|will)\s+not\s+exceed|in\s+no\s+event[^.]{0,120}liab", re.I)
_LIABILITY_WORD = re.compile(r"\bliab(?:le|ility|ilities)\b|\bindemnif", re.I)


def liability_flags(doc: DocIndex) -> List[FlagDraft]:
    text_all = "\n".join(s.text for s in doc.sections)
    for sec in doc.sections:
        m = _UNCAPPED.search(sec.text)
        if m:
            page, section, quote = _locate(doc, _sentence_around(sec.text, m.start(), m.end()), sec)
            return [FlagDraft(
                "UNCAPPED_LIABILITY", H,
                "The contract appears to state that liability is unlimited or uncapped. A single claim could exceed the value of the contract.",
                "document", None, "Liability cap", page, section, quote)]
    if _CAP.search(text_all):
        return []
    lm = None
    for sec in doc.sections:
        lm = _LIABILITY_WORD.search(sec.text)
        if lm:
            page, section, quote = _locate(doc, _sentence_around(sec.text, lm.start(), lm.end()), sec)
            return [FlagDraft(
                "UNCAPPED_LIABILITY", M,
                "Liability or indemnity is mentioned but no cap on it was found. Without a cap, exposure may be unlimited.",
                "document", None, "Liability cap", page, section, quote)]
    return [FlagDraft(
        "UNCAPPED_LIABILITY", M,
        "No limitation-of-liability clause was found. Without one, exposure for damages may be unlimited.",
        "document", None, "Liability cap")]


# ─── Termination ──────────────────────────────────────────────────────────────

_TERMINATE = re.compile(r"\bterminat\w*|\bcancel\w*", re.I)
_CONVENIENCE = re.compile(r"\bfor\s+convenience\b|\bat\s+any\s+time\b|\bwithout\s+(?:any\s+)?(?:cause|reason)\b|\bfor\s+any\s+(?:or\s+no\s+)?reason\b", re.I)
_MUTUAL = re.compile(r"\beither\s+party\b|\beach\s+party\b|\bboth\s+parties\b|\beither\s+of\s+the\s+parties\b|\bany\s+party\b|\beither\s+side\b", re.I)
_SUBJECT = re.compile(r"\b(?:the\s+)?(Provider|Customer|Client|Company|Licensor|Licensee|Supplier|Vendor|Contractor|Landlord|Tenant|Buyer|Seller)\b\s+(?:may|can|shall\s+have\s+the\s+right\s+to|has\s+the\s+right\s+to)\b", re.I)
_TERM_FEE = re.compile(r"(?:early\s+)?termination\s+(?:fee|charge|penalt\w+)|liquidated\s+damages|pay(?:ment)?\s+(?:of\s+)?the\s+remaining\s+(?:fees|balance|term)|remaining\s+(?:fees|term|balance)\s+(?:shall|will)\s+(?:become\s+)?(?:due|payable)", re.I)
_LOCKIN = re.compile(r"non-?cancel+able|minimum\s+(?:commitment|term|purchase|spend)|lock-?in\s+period|may\s+not\s+(?:be\s+)?terminate\w*", re.I)
_NO_NOTICE = re.compile(r"terminat\w*[^.]{0,100}(?:immediately|with\s+immediate\s+effect)[^.]{0,100}without\s+(?:prior\s+)?notice|without\s+(?:prior\s+)?notice[^.]{0,80}terminat", re.I)


def termination_flags(doc: DocIndex, fields: Iterable[ExtractedField]) -> List[FlagDraft]:
    out: List[FlagDraft] = []
    seen: set = set()

    def add(code_key: str, sec, m, severity, reason):
        if (code_key, sec.label) in seen:
            return
        seen.add((code_key, sec.label))
        page, section, quote = _locate(doc, _sentence_around(sec.text, m.start(), m.end()), sec)
        out.append(FlagDraft("UNUSUAL_TERMINATION", severity, reason, "document", None, "Termination rights", page, section, quote))

    for sec in doc.sections:
        # Termination for convenience that only ONE party can use.
        for sm in re.finditer(r"[^.;]+[.;]?", sec.text):
            sentence = sm.group(0)
            if _TERMINATE.search(sentence) and _CONVENIENCE.search(sentence) and not _MUTUAL.search(sentence):
                subj = _SUBJECT.search(sentence)
                if subj:
                    add("one_sided", sec, sm, H,
                        f"Only one party (the {subj.group(1)}) appears to be able to end this contract for convenience. "
                        "Check whether the other party has a matching right.")
        m = _TERM_FEE.search(sec.text)
        if m:
            add("fee", sec, m, M, "Ending the contract early may trigger a fee or require paying the remaining balance.")
        m = _LOCKIN.search(sec.text)
        if m:
            add("lockin", sec, m, M, "The contract appears to lock you in (non-cancellable or a minimum commitment). Check when you can actually leave.")
        m = _NO_NOTICE.search(sec.text)
        if m:
            add("nonotice", sec, m, M, "A party may be able to terminate immediately without notice, leaving no time to react.")

    # Notice periods, from the extracted (already validated) fields.
    for f in fields:
        if f.field_key != "termination_notice_period" or not f.value or f.status == FieldStatus.EXTRACTION_UNAVAILABLE.value:
            continue
        days = f.value.get("days")
        if not isinstance(days, int):
            continue
        if days <= 7:
            out.append(FlagDraft("UNUSUAL_TERMINATION", M,
                                 f"The termination notice period is very short ({f.display_value}). There may be little time to find an alternative.",
                                 "field", f.id, "Termination notice period", f.page, f.section, f.source_quote))
        elif days >= 180:
            out.append(FlagDraft("UNUSUAL_TERMINATION", M,
                                 f"The termination notice period is unusually long ({f.display_value}). It can make leaving the contract slow and costly.",
                                 "field", f.id, "Termination notice period", f.page, f.section, f.source_quote))
    return out


# ─── How the text was obtained (scan / Word) ──────────────────────────────────

def source_flags(source: Optional[dict]) -> List[FlagDraft]:
    if not source:
        return []
    if source.get("kind") == "ocr":
        pages = source.get("pages") or []
        low = source.get("low_confidence_pages") or []
        skipped = source.get("skipped_pages") or []
        empty = source.get("empty_pages") or []
        parts = [f"{len(pages)} page(s) were scanned images and were read with OCR, which can misread digits and names "
                 f"(pages {', '.join(str(p) for p in pages[:20])}). Values found there are marked 'needs review' until you check them against the page image."]
        if low:
            parts.append(f"Low reading confidence on page(s) {', '.join(str(p) for p in low[:20])}.")
        if empty:
            parts.append(f"Nothing could be read on page(s) {', '.join(str(p) for p in empty[:20])}.")
        if skipped:
            parts.append(f"{len(skipped)} further page(s) were not read because of the OCR page limit, so this analysis is incomplete.")
        return [FlagDraft("SCANNED_PAGES", H if (skipped or low or empty) else M, " ".join(parts), "document", None, "Scanned pages")]
    if source.get("kind") == "docx":
        extra = " ".join(source.get("notes") or [])
        return [FlagDraft("CONVERTED_DOCUMENT", L,
                          "This Word document was converted to a PDF so it could be analysed. Page numbers refer to the converted copy, "
                          "not to Word's own pagination." + (f" {extra}" if extra else ""), "document", None, "Converted from Word")]
    return []


# ─── Rules over the extracted data ────────────────────────────────────────────

def _is_conflict(f: ExtractedField) -> bool:
    n = f.notes or {}
    msg = str(n.get("validation", "")).lower()
    return bool(n.get("conflicts")) or any(k in msg for k in ("before the effective", "same as the effective", "conflicting", "is before", "plausible", "longer than 50"))


def field_flags(fields: List[ExtractedField], complete: bool) -> List[FlagDraft]:
    out: List[FlagDraft] = []
    for f in fields:
        label = FIELD_LABELS.get(f.field_key, f.field_key)
        stakes = f.field_key in HIGH_STAKES_FIELDS
        st = f.status
        reviewed = bool((f.notes or {}).get("user_review"))     # a person already looked at it

        if st == FieldStatus.EXTRACTION_UNAVAILABLE.value:
            continue
        if reviewed:
            if f.field_key == "auto_renew" and (f.value or {}).get("bool") is True:
                out.append(FlagDraft("AUTO_RENEWAL", M, "", "field", f.id, label, f.page, f.section, f.source_quote))
            continue
        if st == FieldStatus.NOT_FOUND.value:
            if complete and f.field_key in REQUIRED_FIELDS:
                sev, why = REQUIRED_FIELDS[f.field_key]
                out.append(FlagDraft("MISSING_FIELD", sev, why, "field", f.id, label))
            continue

        note = (f.notes or {}).get("validation")
        if _is_conflict(f):
            conflicts = (f.notes or {}).get("conflicts") or []
            extra = f" Other values found: {', '.join(str(c.get('value')) for c in conflicts[:3])}." if conflicts else ""
            out.append(FlagDraft("DATE_CONFLICT", H, f"{label}: {note or 'conflicting values were found.'}{extra}",
                                 "field", f.id, label, f.page, f.section, f.source_quote))
        elif st == FieldStatus.NEEDS_REVIEW.value and f.page is None:
            out.append(FlagDraft("QUOTE_NOT_FOUND", H if stakes else M,
                                 f"{label} was read as “{f.display_value}”, but the supporting quote could not be found in the document, so it is unverified.",
                                 "field", f.id, label, None, None, f.source_quote))
        elif st == FieldStatus.NEEDS_REVIEW.value or (f.confidence is not None and f.confidence < LOW_CONFIDENCE_BELOW):
            why = note or "It could not be fully verified."
            out.append(FlagDraft("LOW_CONFIDENCE", M if stakes else L,
                                 f"{label} (“{f.display_value}”) has low confidence. {why}",
                                 "field", f.id, label, f.page, f.section, f.source_quote))

        if f.field_key == "auto_renew" and (f.value or {}).get("bool") is True:
            out.append(FlagDraft("AUTO_RENEWAL", M, "", "field", f.id, label, f.page, f.section, f.source_quote))
    return out


def auto_renewal_reason(fields: List[ExtractedField]) -> str:
    notice = next((f for f in fields if f.field_key == "renewal_notice_period" and f.display_value
                   and f.status != FieldStatus.EXTRACTION_UNAVAILABLE.value), None)
    tail = (f" You must give notice {notice.display_value} before the term ends to stop it." if notice
            else " Check the notice period for stopping it.")
    return "This contract renews automatically." + tail + " Missing the window locks you into another term."


def item_flags(parties: List[ContractParty], clauses: List[Clause], obligations: List[Obligation], complete: bool) -> List[FlagDraft]:
    out: List[FlagDraft] = []

    def scan(items, kind: str, label_of, quote_of, page_of, section_of):
        count = 0
        for it in items:
            status = it.verification_status if kind == "obligation" else it.status
            low = it.confidence is not None and it.confidence < LOW_CONFIDENCE_BELOW
            if status == FieldStatus.VERIFIED.value and not low:
                continue
            if count >= MAX_ITEM_FLAGS:
                break
            count += 1
            unverified = status != FieldStatus.VERIFIED.value or page_of(it) is None
            code = "QUOTE_NOT_FOUND" if page_of(it) is None else "LOW_CONFIDENCE"
            reason = (f"{kind.capitalize()} “{label_of(it)}” could not be verified against the document text."
                      if unverified else f"{kind.capitalize()} “{label_of(it)}” was extracted with low confidence.")
            out.append(FlagDraft(code, M, reason, kind, it.id, label_of(it), page_of(it), section_of(it), quote_of(it)))

    scan(parties, "party", lambda p: p.name, lambda p: p.source_quote, lambda p: p.source_page, lambda p: p.source_section)
    scan(clauses, "clause", lambda c: c.title, lambda c: c.source_quote, lambda c: c.source_page, lambda c: c.source_section)
    scan(obligations, "obligation", lambda o: f"{o.responsible_party}: {o.action[:70]}", lambda o: o.source_quote,
         lambda o: o.source_page, lambda o: o.source_section)
    if complete and not parties:
        out.append(FlagDraft("MISSING_FIELD", H, "No parties were identified. Check who this contract is between.", "party", None, "Parties"))
    return out


# ─── Amendments / new versions ────────────────────────────────────────────────

def _canon(f: Optional[ExtractedField]) -> Optional[str]:
    if f is None or f.status in (FieldStatus.NOT_FOUND.value, FieldStatus.EXTRACTION_UNAVAILABLE.value) or not f.display_value:
        return None
    if f.field_key == "payment_terms":                 # compare amounts, not wording, to avoid noise
        amounts = sorted(v.amounts_in(f.display_value) | v.amounts_in(f.source_quote))
        return ",".join(amounts) if amounts else None
    if f.field_key == "auto_renew":
        return "yes" if (f.value or {}).get("bool") else "no"
    if f.field_key.endswith("notice_period") and f.value and f.value.get("days") is not None:
        return f"{f.value['days']} days"
    return f.display_value


def _fmt_amounts(canon: str) -> str:
    """'12500,900.50' -> '12,500, 900.50' for a human-readable reason."""
    out = []
    for a in canon.split(","):
        head, _, tail = a.partition(".")
        out.append(f"{int(head):,}" + (f".{tail}" if tail else "") if head.isdigit() else a)
    return ", ".join(out)


async def amendment_flags(
    db: AsyncSession, version: ContractVersion, fields: List[ExtractedField], main_kind: Optional[DocumentKind],
) -> List[FlagDraft]:
    out: List[FlagDraft] = []
    if main_kind == DocumentKind.AMENDMENT:
        out.append(FlagDraft(
            "AMENDMENT_CHANGES_TERM", L,
            "This upload is an amendment. Amendments can override terms of the original agreement; check exactly what it supersedes.",
            "document", None, "Amendment"))
    if version.version_number <= 1:
        return out

    prev = (await db.execute(
        select(ContractVersion).where(
            ContractVersion.contract_id == version.contract_id, ContractVersion.version_number < version.version_number)
        .order_by(ContractVersion.version_number.desc()).limit(1)
    )).scalar_one_or_none()
    if prev is None:
        return out
    prev_fields = {f.field_key: f for f in (await db.execute(
        select(ExtractedField).where(ExtractedField.contract_version_id == prev.id))).scalars().all()}
    new_fields = {f.field_key: f for f in fields}

    for key in AMENDMENT_KEYS:
        old, new = _canon(prev_fields.get(key)), _canon(new_fields.get(key))
        if old == new or (old is None and new is None):
            continue
        label = FIELD_LABELS.get(key, key)
        nf = new_fields.get(key)
        if key == "payment_terms" and old and new:
            what = f"changed: the amounts went from {_fmt_amounts(old)} to {_fmt_amounts(new)}"
        elif old and new:
            what = f"changed from {prev_fields[key].display_value} to {nf.display_value}"
        elif new:
            what = f"is now stated ({nf.display_value}) but was not in {prev.label}"
        else:
            what = f"is no longer stated (it was {prev_fields[key].display_value} in {prev.label})"
        out.append(FlagDraft(
            "AMENDMENT_CHANGES_TERM", H if key in HIGH_STAKES_FIELDS else M,
            f"{label} {what} compared with {prev.label}.",
            "field", nf.id if nf else None, label,
            nf.page if nf else None, nf.section if nf else None, nf.source_quote if nf else None))
    return out


# ─── Orchestration ────────────────────────────────────────────────────────────

def _fingerprint(code: str, label: Optional[str], quote: Optional[str]) -> Tuple[str, str, str]:
    return code, (label or "").lower(), " ".join((quote or "").lower().split())[:60]


async def generate_flags(
    db: AsyncSession,
    contract: Contract,
    version: ContractVersion,
    fields: List[ExtractedField],
    parties: List[ContractParty],
    clauses: List[Clause],
    obligations: List[Obligation],
    doc: DocIndex,
    extraction_status: str,
    extraction_error: Optional[str] = None,
    chunks_ok: Optional[int] = None,
    chunks_total: Optional[int] = None,
    source: Optional[dict] = None,
) -> int:
    """(Re)build the flags for a version. Resolved/dismissed decisions survive a rebuild."""
    complete = extraction_status == "complete"
    drafts: List[FlagDraft] = []

    if extraction_status == "partial":
        span = f" ({chunks_ok} of {chunks_total} sections)" if chunks_total else ""
        drafts.append(FlagDraft(
            "EXTRACTION_INCOMPLETE", H,
            f"Only part of this document could be analysed{span}. Anything not listed may simply be missing, so review the full text.",
            "document", None, "Analysis coverage"))
    elif extraction_status == "unavailable":
        drafts.append(FlagDraft(
            "EXTRACTION_INCOMPLETE", M,
            "AI extraction did not run for this document, so no terms, obligations or deadlines were extracted. "
            "The checks below come from the raw text only.",
            "document", None, "Analysis coverage"))

    drafts += source_flags(source)
    drafts += field_flags(fields, complete)
    for d in drafts:                                             # fill the auto-renewal wording (needs sibling fields)
        if d.code == "AUTO_RENEWAL" and not d.reason:
            d.reason = auto_renewal_reason(fields)
    drafts += item_flags(parties, clauses, obligations, complete)
    drafts += vague_wording_flags(doc)
    drafts += liability_flags(doc)
    drafts += termination_flags(doc, fields)

    main_doc = (await db.execute(
        select(Document).where(Document.version_id == version.id).order_by(Document.created_at))).scalars().first()
    drafts += await amendment_flags(db, version, fields, main_doc.kind if main_doc else None)
    from app.services import policy_service          # local import: policy_service imports this module
    drafts += await policy_service.drafts_for_user(db, contract.user_id, fields)

    # Keep a person's earlier decisions when the same flag reappears after re-processing.
    previous = (await db.execute(select(ReviewFlag).where(ReviewFlag.contract_version_id == version.id))).scalars().all()
    decided = {_fingerprint(p.code, p.target_label, p.source_quote): (p.status, p.resolved_at)
               for p in previous if p.status != FlagStatus.OPEN}
    await db.execute(delete(ReviewFlag).where(ReviewFlag.contract_version_id == version.id))

    seen: set = set()
    created = 0
    for d in drafts:
        fp = _fingerprint(d.code, d.target_label, d.quote)
        if fp in seen:
            continue
        seen.add(fp)
        status, resolved_at = decided.get(fp, (FlagStatus.OPEN, None))
        db.add(ReviewFlag(
            contract_id=contract.id, contract_version_id=version.id, code=d.code, severity=d.severity,
            reason=d.reason, target_type=d.target_type, target_id=d.target_id, target_label=d.target_label,
            source_page=d.page, source_section=d.section, source_quote=d.quote,
            status=status, resolved_at=resolved_at,
        ))
        created += 1
    logger.info("Generated %d review flags for version %s", created, str(version.id)[:8])
    return created
