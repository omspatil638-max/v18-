"""
Chat must cope with how people really ask questions: different words than the contract uses
("expire" vs "ends", "cancel" vs "terminate"), broad requests ("summarise"), and small talk.
A user once got 'Not found in this contract' for all of these.
"""

import pytest

from app.db.database import AsyncSessionLocal
from app.services.vector_service import (
    build_or_query, heading_patterns, is_small_talk, query_terms, vector_service,
)
from tests.helpers import upload_and_process


async def ask(client, cid, question):
    r = await client.post(f"/api/contracts/{cid}/chat", json={"content": question})
    assert r.status_code == 200, r.text
    return r.json()


def make_pdf(tmp_path, body: str) -> bytes:
    from sample_data.generate import write_pdf
    p = tmp_path / "c.pdf"
    write_pdf(p, body)
    return p.read_bytes()


# ─── query understanding (pure) ───────────────────────────────────────────────

@pytest.mark.parametrize("question,must_include", [
    ("When does this agreement expire?", {"expir", "end", "until", "term"}),
    ("can I cancel?", {"terminat", "notice"}),
    ("What are my obligations?", {"shall", "must", "responsible"}),
    ("how much do I pay", {"fee", "payment", "invoice"}),
    ("who are the parties", {"between", "inc", "llc"}),
    ("is there a penalty for late payment", {"interest", "default"}),
    ("who owns the IP", {"intellectual", "property"}),
    ("what law governs this", {"jurisdiction", "courts"}),
])
def test_questions_are_expanded_to_the_words_contracts_actually_use(question, must_include):
    assert must_include <= set(query_terms(question)), query_terms(question)


def test_filler_words_are_not_searched():
    terms = query_terms("What is the contract about? Please tell me")
    assert not ({"what", "the", "please", "tell", "contract"} & set(terms))


def test_tsquery_is_always_safe_alphanumeric():
    q = build_or_query("Can we terminate early?; DROP TABLE contracts; -- ' | & !")
    assert q and all(part.strip().isalnum() for part in q.split("|"))


def test_heading_patterns_target_section_titles():
    pats = heading_patterns("Can I cancel this early?")
    assert any("terminat" in p for p in pats)


@pytest.mark.parametrize("text", ["hi", "Hello!", "hey", "thanks", "thank you", "Good morning", "who are you?",
                                  "what can you do?", "help", "hello, who are you?"])
def test_small_talk_is_recognised(text):
    assert is_small_talk(text)


@pytest.mark.parametrize("text", ["Can we terminate early?", "hi, when does it expire?", "help me understand the fees",
                                  "What is the notice period?", "thanks but who pays tax?"])
def test_real_questions_are_not_mistaken_for_small_talk(text):
    assert not is_small_talk(text)


# ─── retrieval finds the right passage despite different wording ──────────────

async def _search(client, cid, question):
    vid = (await client.get(f"/api/contracts/{cid}/status")).json()["current_version_id"]
    async with AsyncSessionLocal() as db:
        return await vector_service.search_chunks(db, __import__("uuid").UUID(vid), question)


PLAIN = (
    "SERVICES AGREEMENT\n\nThis agreement is between Alpha Ltd and Beta Inc.\n\n"
    "1. Fees\nBeta shall pay Alpha 5,000 each month.\n\n"
    "2. Term\nThis agreement continues until December 31, 2030 and then comes to an end.\n\n"
    "3. Exit\nEither side can terminate on thirty (30) days written notice.\n\n"
    "4. Law\nThis agreement is governed by the laws of Ontario.\n"
)


async def test_expire_finds_a_contract_that_says_ends_and_until(client, llm, tmp_path):
    cid = (await upload_and_process(client, make_pdf(tmp_path, PLAIN)))["id"]
    hits = await _search(client, cid, "When does this agreement expire?")
    assert any("until December 31, 2030" in h["content"] for h in hits), [h["content"][:40] for h in hits]


async def test_cancel_finds_the_terminate_clause(client, llm, tmp_path):
    cid = (await upload_and_process(client, make_pdf(tmp_path, PLAIN)))["id"]
    hits = await _search(client, cid, "Can I cancel?")
    assert any("terminate on thirty" in h["content"] for h in hits)


async def test_governing_law_question_finds_the_law_section(client, llm, tmp_path):
    cid = (await upload_and_process(client, make_pdf(tmp_path, PLAIN)))["id"]
    hits = await _search(client, cid, "what law governs this?")
    assert any("laws of Ontario" in h["content"] for h in hits)


async def test_a_question_with_no_matches_still_returns_the_opening_so_parties_are_visible(client, llm, tmp_path):
    cid = (await upload_and_process(client, make_pdf(tmp_path, PLAIN)))["id"]
    hits = await _search(client, cid, "xylophone zeppelin")
    assert hits and hits[0]["why"] == "opening" and "Alpha Ltd" in hits[0]["content"]


async def test_section_heading_alone_is_enough_to_retrieve(client, llm, tmp_path):
    """The body says 'Either side can terminate', but the heading 'Exit' is what matches 'exit'."""
    cid = (await upload_and_process(client, make_pdf(tmp_path, PLAIN)))["id"]
    hits = await _search(client, cid, "exit")
    assert any(h["why"] == "heading" or "terminate" in h["content"] for h in hits)


# ─── answers ──────────────────────────────────────────────────────────────────

async def test_greeting_gets_a_helpful_reply_without_a_search_or_model_call(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    before = len(llm.calls)
    a = await ask(client, cid, "hi")
    assert a["answer_status"] == "no_question" and a["citations"] == []
    assert "Try" in a["content"] and "Not found" not in a["content"]
    assert len(llm.calls) == before


async def test_greeting_works_even_with_no_llm_configured(client, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "hello")
    assert a["answer_status"] == "no_question"


async def test_who_are_the_parties_is_answered_from_verified_facts(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "Who are the parties?")
    assert a["answer_status"] == "answered", a["content"]
    assert "Northwind" in a["content"] and "Contoso" in a["content"]
    c = a["citations"][0]
    assert c["verified"] is True and c["source_page"] == 1
    assert "Party (Provider): Northwind Analytics Inc." in llm.last_prompt      # the facts reached the model


async def test_how_much_do_i_pay_uses_the_extracted_payment_term(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "how much do I pay?")
    assert a["answer_status"] == "answered" and "12,500" in a["content"]
    assert "$12,500" in a["citations"][0]["snippet"]


async def test_broad_request_is_answered_rather_than_dismissed(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "summarize this contract")
    assert a["answer_status"] == "answered" and a["citations"]


async def test_expire_question_is_answered_from_the_term_clause(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "When does this agreement expire?")
    assert a["answer_status"] == "answered" and "December 31, 2028" in a["content"]
    assert "expires on December 31, 2028" in a["citations"][0]["snippet"]
    assert a["citations"][0]["source_page"] >= 4


async def test_non_verbatim_quote_is_repaired_to_a_real_passage_not_dropped(client, llm, long_pdf):
    """The model paraphrased its quote. The answer is kept only because a REAL matching sentence exists."""
    cid = (await upload_and_process(client, long_pdf))["id"]
    llm.answer_mode = "paraphrase"
    a = await ask(client, cid, "Can we terminate early?")
    assert a["answer_status"] == "answered", a["content"]
    snippet = a["citations"][0]["snippet"]
    assert "terminate this Agreement for convenience" in snippet          # actual document text, not the paraphrase
    assert "either side can" not in snippet.lower()


async def test_model_declaring_not_a_question_is_a_normal_reply(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    llm.answer_mode = "not_a_question"
    a = await ask(client, cid, "blah blah")
    assert a["answer_status"] == "no_question" and a["citations"] == []


async def test_not_found_tells_the_user_what_they_can_ask(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "what is the capital of France?")
    assert a["answer_status"] == "not_found" and a["content"].startswith("Not found in this contract.")
    assert "parties" in a["content"] and "termination" in a["content"]


async def test_unverified_facts_are_flagged_to_the_model(client, llm, long_pdf):
    llm.transform_quote = lambda q: q.replace("December 31, 2028", "December 31, 2031")   # expiration becomes unverifiable
    cid = (await upload_and_process(client, long_pdf))["id"]
    await ask(client, cid, "Who are the parties?")
    assert "[unverified]" in llm.last_prompt and "Expiration date" in llm.last_prompt


async def test_prompt_stays_small_enough_for_the_free_tier(client, llm, long_pdf):
    """Groq's free tier allows ~8K tokens/min; the whole Q&A prompt must fit with room for the answer."""
    cid = (await upload_and_process(client, long_pdf))["id"]
    await ask(client, cid, "Can we terminate early?")
    assert len(llm.last_prompt) < 16_000, len(llm.last_prompt)      # roughly 4-5K tokens
