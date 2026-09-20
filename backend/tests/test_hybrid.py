"""Phase 7: hybrid retrieval (semantic + keyword + headings), fused by reciprocal rank."""

import uuid

import pytest
from sqlalchemy import text

from app.db.database import AsyncSessionLocal, engine
from app.services import embedding_service
from app.services.vector_service import vector_service
from tests.helpers import upload_and_process


@pytest.fixture
def semantic():
    """Turn on the deterministic test embedder (concept-based, so it needs no model download)."""
    embedding_service.use_fake_embedder(True)
    yield
    embedding_service.use_fake_embedder(False)


def make_pdf(tmp_path, body: str) -> bytes:
    from sample_data.generate import write_pdf
    p = tmp_path / "c.pdf"
    write_pdf(p, body)
    return p.read_bytes()


# No word in the question ("lapse") appears in the passage ("conclude"): only meaning connects them.
DOC = (
    "SERVICES AGREEMENT\n\nThis agreement is between Alpha Ltd and Beta Inc.\n\n"
    "1. Charges\nBeta shall pay Alpha 5,000 each month for the work.\n\n"
    "2. Duration\nThe engagement will conclude on December 31, 2030.\n\n"
    "3. Privacy\nEach side keeps the other's information private and undisclosed.\n\n"
    "4. Venue\nDisputes go to the courts of Ontario.\n"
)


async def search(cid_client, cid, q, limit=8):
    vid = (await cid_client.get(f"/api/contracts/{cid}/status")).json()["current_version_id"]
    async with AsyncSessionLocal() as db:
        return await vector_service.search_chunks(db, uuid.UUID(vid), q, limit=limit, pad=False)


async def count_vectors(cid_client, cid):
    vid = (await cid_client.get(f"/api/contracts/{cid}/status")).json()["current_version_id"]
    async with engine.connect() as conn:
        return (await conn.execute(text(
            "SELECT count(*) FILTER (WHERE embedding_vec IS NOT NULL), count(*) FROM contract_chunks WHERE contract_version_id = :v"),
            {"v": vid})).one()


# ─── storage ──────────────────────────────────────────────────────────────────

async def test_chunks_are_embedded_and_stored_as_real_pgvector_values(client, llm, semantic, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    embedded, total = await count_vectors(client, d["id"])
    assert total >= 4 and embedded == total
    vid = d["current_version_id"]
    async with engine.connect() as conn:
        dim, model = (await conn.execute(text(
            "SELECT vector_dims(embedding_vec), embedding_model FROM contract_chunks WHERE contract_version_id = :v LIMIT 1"),
            {"v": vid})).one()
    assert dim == 384 and model == "fake"


async def test_with_embeddings_off_nothing_is_embedded_and_search_still_works(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    embedded, total = await count_vectors(client, d["id"])
    assert embedded == 0 and total >= 4
    hits = await search(client, d["id"], "how much do I pay each month?")
    assert any("5,000 each month" in h["content"] for h in hits)


# ─── the point of Phase 7 ─────────────────────────────────────────────────────

async def test_semantic_search_finds_a_passage_that_shares_no_words_with_the_question(client, llm, semantic, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    hits = await search(client, d["id"], "When will it lapse?")
    assert hits and "conclude on December 31, 2030" in hits[0]["content"]
    assert hits[0]["why"] == "semantic"


async def test_the_same_question_finds_nothing_by_keyword_alone(client, llm, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))                       # embeddings off
    hits = await search(client, d["id"], "When will it lapse?")
    assert not any("conclude on December 31" in h["content"] for h in hits)


@pytest.mark.parametrize("question,expected", [
    ("Is my information kept secret?", "private and undisclosed"),
    ("Which court hears disputes?", "courts of Ontario"),
    ("What does it cost?", "5,000 each month"),
])
async def test_semantic_search_matches_by_meaning_across_topics(client, llm, semantic, tmp_path, question, expected):
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    hits = await search(client, d["id"], question)
    assert any(expected in h["content"] for h in hits[:2]), [h["content"][:40] for h in hits]


async def test_a_passage_found_by_both_methods_is_marked_both_and_ranked_first(client, llm, semantic, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    hits = await search(client, d["id"], "What is the payment amount each month?")
    assert "5,000 each month" in hits[0]["content"] and hits[0]["why"] == "both"


# ─── fusion, unit level ───────────────────────────────────────────────────────

def item(cid, why, idx=0):
    return {"chunk_id": cid, "chunk_index": idx, "content": cid, "source_page": 1, "source_section": "s", "score": 0.0, "why": why}


def test_rank_fusion_prefers_agreement_and_dedupes():
    keyword = [item("a", "keyword"), item("b", "keyword"), item("c", "heading")]
    semantic = [item("c", "semantic"), item("d", "semantic"), item("a", "semantic")]
    fused = vector_service.fuse(keyword, semantic, limit=10)
    ids = [f["chunk_id"] for f in fused]
    assert sorted(ids) == ["a", "b", "c", "d"]                 # no duplicates
    assert set(ids[:2]) == {"a", "c"}                          # chosen by both methods -> on top
    assert {f["chunk_id"]: f["why"] for f in fused}["a"] == "both"
    assert {f["chunk_id"]: f["why"] for f in fused}["d"] == "semantic"


def test_rank_fusion_respects_the_limit_and_handles_empty_inputs():
    assert vector_service.fuse([], [], 5) == []
    only = vector_service.fuse([item("a", "keyword"), item("b", "keyword")], [], 1)
    assert [f["chunk_id"] for f in only] == ["a"]


# ─── resilience ───────────────────────────────────────────────────────────────

async def test_embedding_failure_during_processing_does_not_fail_the_upload(client, llm, tmp_path, monkeypatch):
    async def broken(_texts):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(embedding_service, "embed_documents", broken)
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    assert d["status"] == "READY" and d["extraction_status"] == "complete"
    assert (await count_vectors(client, d["id"]))[0] == 0


async def test_a_broken_query_embedding_falls_back_to_keyword_search(client, llm, semantic, tmp_path, monkeypatch):
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))

    async def broken(_q):
        raise RuntimeError("no model")

    monkeypatch.setattr(embedding_service, "embed_query", broken)
    hits = await search(client, d["id"], "how much do I pay each month?")
    assert any("5,000 each month" in h["content"] for h in hits)


async def test_low_similarity_passages_are_not_returned_as_semantic_evidence(client, llm, semantic, tmp_path):
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    hits = await search(client, d["id"], "xylophone zeppelin quasar")
    assert all(h["why"] != "semantic" for h in hits)


async def test_system_status_reports_the_embedding_state(client, semantic):
    s = (await client.get("/api/system/status")).json()
    assert s["embedding_provider"] == "fake" and s["embedding_available"] is True
    embedding_service.use_fake_embedder(False)
    s = (await client.get("/api/system/status")).json()
    assert s["embedding_provider"] == "none" and s["embedding_available"] is False


# ─── vectors follow their version ─────────────────────────────────────────────

async def test_reprocessing_replaces_vectors_without_duplicating_chunks(client, llm, semantic, tmp_path):
    from app.services import job_runner
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    before = await count_vectors(client, d["id"])
    for _ in range(2):
        await client.post(f"/api/contracts/{d['id']}/reprocess")
        await job_runner.wait_idle()
    assert await count_vectors(client, d["id"]) == before


async def test_search_is_scoped_to_one_version(client, llm, semantic, tmp_path):
    from tests.helpers import upload_version
    d = await upload_and_process(client, make_pdf(tmp_path, DOC))
    v2 = await upload_version(client, d["id"], make_pdf(tmp_path, DOC.replace("December 31, 2030", "June 30, 2031")))
    async with AsyncSessionLocal() as db:
        new = await vector_service.search_chunks(db, uuid.UUID(v2["id"]), "When will it lapse?", pad=False)
        old = await vector_service.search_chunks(db, uuid.UUID(d["current_version_id"]), "When will it lapse?", pad=False)
    assert "June 30, 2031" in new[0]["content"] and "December 31, 2030" in old[0]["content"]
