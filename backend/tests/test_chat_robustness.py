"""A chat turn must ALWAYS end in a visible reply. These lock in the 'he doesn't reply' failures."""

import pytest

from tests.helpers import upload_and_process


async def ask(client, cid, question, expect=200):
    r = await client.post(f"/api/contracts/{cid}/chat", json={"content": question})
    assert r.status_code == expect, r.text
    return r.json()


@pytest.mark.parametrize("question", [
    "what is this contract about?",     # no expandable search terms: crashed with a NUL byte in the SQL parameter
    "is it ok",
    "??",
    "and then?",
    "what is it",
])
async def test_questions_with_no_searchable_terms_still_get_a_reply(client, llm, long_pdf, question):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, question)
    assert a["content"].strip() and a["answer_status"] in ("answered", "not_found", "no_question", "unavailable")


async def test_same_without_any_llm_configured(client, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "what is this contract about?")
    assert a["content"].strip() and a["answer_status"] in ("unavailable", "not_found")


async def test_nul_bytes_in_a_question_do_not_crash_the_request(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "Can we terminate\x00 early?")
    assert a["answer_status"] == "answered"
    history = (await client.get(f"/api/contracts/{cid}/chat")).json()
    assert all("\x00" not in m["content"] for m in history)


async def test_unexpected_internal_error_becomes_a_visible_reply_not_a_500(client, llm, long_pdf, monkeypatch):
    cid = (await upload_and_process(client, long_pdf))["id"]
    from app.services import rag_service as mod

    async def boom(*a, **kw):
        raise RuntimeError("simulated failure inside retrieval")

    monkeypatch.setattr(mod.vector_service, "search_chunks", boom)
    a = await ask(client, cid, "Can we terminate early?")
    assert a["answer_status"] == "unavailable" and "try again" in a["content"].lower()
    history = (await client.get(f"/api/contracts/{cid}/chat")).json()
    assert [m["role"] for m in history] == ["USER", "ASSISTANT"]         # the question is not lost


async def test_llm_outage_still_replies_with_passages(client, llm, long_pdf):
    from app.services.llm_service import LLMError
    cid = (await upload_and_process(client, long_pdf))["id"]
    llm.raise_always = LLMError("bad_json", "simulated outage")
    a = await ask(client, cid, "Can we terminate early?")
    assert a["answer_status"] == "unavailable" and a["citations"]


async def test_over_long_or_empty_question_is_rejected_cleanly(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    assert (await client.post(f"/api/contracts/{cid}/chat", json={"content": ""})).status_code == 422
    assert (await client.post(f"/api/contracts/{cid}/chat", json={"content": "x" * 2001})).status_code == 422


async def test_pdf_text_containing_nul_bytes_can_still_be_processed(client, llm, tmp_path):
    """A NUL inside extracted PDF text used to make saving the document fail."""
    from app.services.pdf_service import pdf_service
    from sample_data.generate import long_contract_text, write_pdf
    write_pdf(tmp_path / "n.pdf", long_contract_text())
    res = pdf_service.extract_text_from_pdf(str(tmp_path / "n.pdf"))
    assert res.is_valid_text_pdf and "\x00" not in res.full_text
