"""
vector_service.py
-----------------
Retrieval chunks + full-text search (Postgres tsvector / GIN index).

Keyword search alone fails on the way people actually talk ("when does it EXPIRE?" against
a contract that says "ends", "can I CANCEL?" against "terminate"). Three things close that gap
without needing an embedding model:

  1. query expansion with a legal-domain synonym table (deterministic, works with no LLM)
  2. matching on section HEADINGS as well as body text ("Term and Renewal", "Termination")
  3. padding thin results with the document's opening (parties/preamble)

Semantic (pgvector) retrieval is added in Phase 7 and will be combined with this (hybrid).
"""

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.models import ContractChunk
from app.services import embedding_service
from app.services.text_index import DocIndex, chunk_sections

logger = logging.getLogger(__name__)

RETRIEVAL_CHUNK_CHARS = 1400
RETRIEVAL_OVERLAP = 200
MIN_HITS_BEFORE_PADDING = 4
MIN_SEMANTIC_SIMILARITY = 0.30     # cosine; below this a passage is noise, not evidence
RRF_K = 60

_STOP = {
    "the", "and", "for", "that", "this", "with", "are", "was", "were", "will", "shall", "may", "can", "could",
    "would", "should", "does", "did", "has", "have", "had", "what", "when", "where", "which", "who", "whom",
    "how", "why", "our", "your", "their", "its", "any", "all", "not", "but", "from", "into", "about", "there",
    "contract", "agreement", "tell", "give", "show", "please", "need", "want", "know", "much", "many", "get",
    "there", "them", "they", "then", "than", "also", "just", "like", "some", "does", "done", "make", "made",
}

# root -> related words that a contract is likely to use instead. Roots are matched by prefix,
# so "expire", "expires", "expiry" and "expiration" all hit the "expir" entry.
_END = ["end", "ends", "expir", "expiration", "terminat", "term", "until", "duration", "period", "renew", "through"]
_PAY = ["payment", "pay", "fee", "fees", "invoice", "price", "charge", "amount", "cost", "compensation", "billing"]
SYNONYMS: Dict[str, List[str]] = {
    "expir": _END, "end": _END, "finish": _END, "last": _END, "long": ["term", "duration", "period", "until", "years", "months"],
    "cancel": ["terminat", "cancellation", "notice", "convenience", "withdraw", "exit", "early"],
    "terminat": ["termination", "cancel", "notice", "convenience", "breach", "cure", "expir", "early"],
    "exit": ["terminat", "cancel", "notice", "convenience"],
    "early": ["terminat", "convenience", "notice", "cancel"],
    "pay": _PAY, "cost": _PAY, "price": _PAY, "fee": _PAY, "charge": _PAY, "money": _PAY, "invoice": _PAY,
    "owe": _PAY, "bill": _PAY, "afford": _PAY, "subscription": _PAY,
    "obligat": ["shall", "must", "agrees", "responsible", "responsibility", "required", "duty", "undertake", "deliver", "provide"],
    "responsib": ["shall", "must", "obligat", "duty", "deliver"],
    "duty": ["shall", "must", "obligat", "responsib"],
    "deadline": ["due", "within", "days", "later", "date", "deliver", "submit"],
    "due": ["within", "days", "deadline", "date", "payable", "invoice"],
    "part": ["between", "agreement", "inc", "llc", "ltd", "limited", "corporation", "company", "provider", "customer"],
    "who": ["between", "inc", "llc", "ltd", "limited", "corporation", "company"],
    "compan": ["between", "inc", "llc", "ltd", "limited", "corporation", "provider", "customer"],
    "renew": ["renewal", "extend", "extension", "automatic", "automatically", "successive", "term", "non-renewal"],
    "extend": ["renew", "renewal", "extension", "term"],
    "penalt": ["interest", "late", "damages", "liquidated", "fine", "charge", "default", "breach"],
    "late": ["interest", "overdue", "unpaid", "penalty", "default", "due"],
    "liab": ["liable", "damages", "indemn", "cap", "limit", "limitation", "responsible"],
    "sue": ["liab", "damages", "dispute", "court", "jurisdiction", "arbitration"],
    "risk": ["liab", "indemn", "damages", "warrant", "insurance", "limitation"],
    "confiden": ["confidentiality", "disclose", "secret", "proprietary", "nda", "non-disclosure"],
    "secret": ["confiden", "disclose", "proprietary"],
    "start": ["effective", "commenc", "begin", "date", "signed"],
    "begin": ["effective", "commenc", "start", "date"],
    "effective": ["commenc", "start", "begin", "date"],
    "govern": ["law", "jurisdiction", "courts", "dispute", "venue"],
    "law": ["governing", "jurisdiction", "courts", "venue", "state"],
    "dispute": ["arbitration", "jurisdiction", "governing", "litigation", "courts", "resolution"],
    "data": ["personal", "privacy", "protection", "gdpr", "processing", "security", "breach"],
    "privacy": ["data", "personal", "protection", "gdpr", "processing"],
    "secur": ["data", "protection", "audit", "breach", "safeguard", "encryption"],
    "warrant": ["guarantee", "represent", "assur", "disclaim"],
    "insur": ["coverage", "indemnity", "liability", "policy"],
    "assign": ["transfer", "subcontract", "delegate", "consent"],
    "chang": ["amend", "modif", "variation", "written", "signed"],
    "amend": ["change", "modif", "variation", "written", "signed"],
    "sign": ["execut", "signature", "effective", "date"],
    "own": ["intellectual", "property", "ownership", "rights", "license", "licence"],
    "ip": ["intellectual", "property", "ownership", "license", "licence", "rights"],
    "refund": ["credit", "return", "reimburs", "payment"],
    "discount": ["fee", "price", "rebate", "credit"],
    "tax": ["taxes", "gst", "vat", "fee", "payment"],
    "audit": ["inspect", "records", "compliance", "report"],
    "report": ["deliver", "submit", "audit", "quarterly", "annual"],
    "notice": ["written", "notify", "days", "terminat", "renew"],
    "support": ["service", "response", "maintenance", "sla", "availability"],
    "sla": ["availability", "uptime", "service", "level", "credit", "response"],
    "uptime": ["availability", "sla", "service", "level"],
    "renewal": ["renew", "automatic", "successive", "term", "notice"],
}

_TOKEN = re.compile(r"[A-Za-z0-9]{2,}")

_GREETING = re.compile(
    r"^\s*(hi+|hello+|hey+|yo|howdy|greetings|good\s+(morning|afternoon|evening)|thanks?( you)?|thank you( so much)?|"
    r"ok(ay)?|cool|great|nice|bye|goodbye|test(ing)?|are you there\??|who are you\??|what can you do\??|"
    r"help|hello,?\s+who are you\??)[\s!.?]*$",
    re.IGNORECASE,
)


def is_small_talk(question: str) -> bool:
    """A greeting/thanks/'what can you do' — not a question about the contract."""
    return bool(_GREETING.match(question or ""))


def _stem(token: str) -> str:
    """Crude suffix strip so 'expires' / 'obligations' / 'terminating' reach the synonym table."""
    t = token.lower()
    for suffix in ("ations", "ation", "ings", "ing", "ies", "ied", "ers", "er", "es", "ed", "s"):
        if t.endswith(suffix) and len(t) - len(suffix) >= 3:
            return t[: -len(suffix)]
    return t


def query_terms(question: str) -> List[str]:
    """Content words of the question plus their domain synonyms, de-duplicated, order preserved."""
    base: List[str] = []
    for tok in _TOKEN.findall((question or "").lower()):
        if tok not in _STOP and tok not in base and len(tok) >= 2:
            base.append(tok)

    expanded: List[str] = list(base)
    for tok in base:
        stem = _stem(tok)
        for root, related in SYNONYMS.items():
            if stem.startswith(root) or (len(stem) >= 4 and root.startswith(stem)):
                for r in related:
                    if r not in expanded:
                        expanded.append(r)
    return expanded


def build_or_query(question: str) -> Optional[str]:
    """A safe tsquery: alphanumeric tokens joined with OR (English stemming is applied by Postgres)."""
    tokens = [t for t in query_terms(question) if re.fullmatch(r"[a-z0-9]+", t)]
    return " | ".join(tokens[:40]) or None


def heading_patterns(question: str) -> List[str]:
    """ILIKE patterns for section headings ('Term and Renewal', 'Termination') the question points at."""
    stems = {_stem(t) for t in query_terms(question) if len(t) >= 4}
    return [f"%{s}%" for s in sorted(stems)][:30]


class VectorService:
    @classmethod
    async def chunk_and_store(
        cls, db: AsyncSession, contract_id: uuid.UUID, version_id: uuid.UUID, doc: DocIndex
    ) -> int:
        """Replace this VERSION's retrieval chunks with section-aware chunks of the whole document."""
        await db.execute(delete(ContractChunk).where(ContractChunk.contract_version_id == version_id))
        chunks = chunk_sections(doc.sections, RETRIEVAL_CHUNK_CHARS, RETRIEVAL_OVERLAP, pack=False)
        rows = []
        for c in chunks:
            row = ContractChunk(
                contract_id=contract_id, contract_version_id=version_id, chunk_index=c.index, content=c.text,
                source_page=c.page_start, source_section=c.primary_section, embedding=None,
            )
            db.add(row)
            rows.append(row)
        await db.flush()
        embedded = await cls._store_embeddings(db, rows)
        logger.info("Stored %d retrieval chunks (%d embedded) for version %s", len(chunks), embedded, str(version_id)[:8])
        return len(chunks)

    @classmethod
    async def _store_embeddings(cls, db: AsyncSession, rows: List[ContractChunk]) -> int:
        """Embed locally and store next to the text. Never fatal: without vectors, search is keyword-only."""
        if not rows:
            return 0
        try:
            vectors = await embedding_service.embed_documents([r.content for r in rows])
            if not vectors:
                return 0
            model = embedding_service.status()["model"]
            await db.execute(
                text("UPDATE contract_chunks SET embedding_vec = CAST(CAST(:v AS text) AS vector), embedding_model = :m WHERE id = :id"),
                [{"v": embedding_service.vector_literal(v), "m": model, "id": r.id} for r, v in zip(rows, vectors)],
            )
            return len(rows)
        except Exception as exc:  # noqa: BLE001 - e.g. pgvector extension missing
            logger.warning("Could not store embeddings (%s); keyword search only.", exc.__class__.__name__)
            return 0

    @classmethod
    async def _vector_search(cls, db: AsyncSession, version_id: uuid.UUID, query: str, limit: int) -> List[Dict[str, Any]]:
        try:
            qv = await embedding_service.embed_query(query)
            if qv is None:
                return []
            rows = await db.execute(
                text(
                    """
                    SELECT id, chunk_index, content, source_page, source_section,
                           1 - (embedding_vec <=> CAST(CAST(:qv AS text) AS vector)) AS sim
                    FROM contract_chunks
                    WHERE contract_version_id = :vid AND embedding_vec IS NOT NULL
                    ORDER BY embedding_vec <=> CAST(CAST(:qv AS text) AS vector)
                    LIMIT :lim
                    """
                ),
                {"qv": embedding_service.vector_literal(qv), "vid": version_id, "lim": limit},
            )
            return [cls._row(r, r.sim, "semantic") for r in rows if r.sim >= MIN_SEMANTIC_SIMILARITY]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Vector search unavailable (%s); keyword search only.", exc.__class__.__name__)
            await db.rollback()
            return []

    @staticmethod
    def fuse(keyword: List[Dict[str, Any]], semantic: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
        """
        Reciprocal Rank Fusion: a chunk ranked well by EITHER method rises, and one ranked well by
        BOTH rises most. Uses ranks only, so the two methods' incomparable scores never need mixing.
        """
        merged: Dict[str, Dict[str, Any]] = {}
        scores: Dict[str, float] = {}
        for source in (keyword, semantic):
            for rank, item in enumerate(source):
                cid = item["chunk_id"]
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
                if cid not in merged:
                    merged[cid] = dict(item)
                elif merged[cid]["why"] != item["why"]:
                    merged[cid]["why"] = "both"
        ordered = sorted(merged.values(), key=lambda d: (-scores[d["chunk_id"]], d["chunk_index"]))
        for d in ordered:
            d["score"] = round(scores[d["chunk_id"]], 5)
        return ordered[:limit]

    @staticmethod
    def _row(r, score: Optional[float] = None, why: str = "keyword") -> Dict[str, Any]:
        return {
            "chunk_id": str(r.id), "chunk_index": r.chunk_index, "content": r.content,
            "source_page": r.source_page, "source_section": r.source_section or f"Page {r.source_page}",
            "score": float(score if score is not None else getattr(r, "score", 0.0)), "why": why,
        }

    @classmethod
    async def search_chunks(
        cls, db: AsyncSession, version_id: uuid.UUID, query: str, limit: int = 8, pad: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Best chunks for a question: body-text matches and section-heading matches, ranked together;
        thin results are padded with the document opening so the model always has the parties.
        """
        q = build_or_query(query)
        pats = heading_patterns(query)
        results: List[Dict[str, Any]] = []

        if q:
            rows = await db.execute(
                text(
                    """
                    SELECT c.id, c.chunk_index, c.content, c.source_page, c.source_section,
                           ts_rank_cd(c.tsv, tq) AS body_score,
                           (c.source_section ILIKE ANY (CAST(:pats AS text[]))) AS heading_hit
                    FROM contract_chunks c, to_tsquery('english', :q) tq
                    WHERE c.contract_version_id = :vid
                      AND (c.tsv @@ tq OR c.source_section ILIKE ANY (CAST(:pats AS text[])))
                    ORDER BY (ts_rank_cd(c.tsv, tq) + CASE WHEN c.source_section ILIKE ANY (CAST(:pats AS text[]))
                                                           THEN 1.0 ELSE 0 END) DESC, c.chunk_index
                    LIMIT :lim
                    """
                ),
                # An empty array would be a type error; this placeholder matches no real heading.
                # (It must never contain a NUL byte: PostgreSQL rejects \x00 in text.)
                {"q": q, "vid": version_id, "lim": limit, "pats": pats or ["%zzq-no-such-heading-zzq%"]},
            )
            results = [
                cls._row(r, r.body_score + (1.0 if r.heading_hit else 0.0), "heading" if r.heading_hit else "keyword")
                for r in rows
            ]

        semantic = await cls._vector_search(db, version_id, query, limit=limit * 2)
        if semantic:
            results = cls.fuse(results, semantic, limit)

        if pad and len(results) < MIN_HITS_BEFORE_PADDING:
            have = {r["chunk_id"] for r in results}
            opening = await db.execute(
                text(
                    "SELECT id, chunk_index, content, source_page, source_section FROM contract_chunks "
                    "WHERE contract_version_id = :vid ORDER BY chunk_index LIMIT 2"
                ),
                {"vid": version_id},
            )
            for r in opening:
                if str(r.id) not in have and len(results) < limit:
                    results.append(cls._row(r, 0.0, "opening"))
        return results


vector_service = VectorService()
