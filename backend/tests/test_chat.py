from tests.helpers import upload_and_process


async def ask(client, cid, question):
    r = await client.post(f"/api/contracts/{cid}/chat", json={"content": question})
    assert r.status_code == 200, r.text
    return r.json()


async def test_answer_is_cited_and_citation_is_verified_against_the_document(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "Can we terminate early?")
    assert a["answer_status"] == "answered" and "ninety" in a["content"].lower()
    assert len(a["citations"]) == 1
    c = a["citations"][0]
    assert c["verified"] is True and c["source_page"] >= 4 and "Termination" in c["source_section"]
    assert "terminate this Agreement for convenience" in c["snippet"]
    assert c["chunk_id"]


async def test_question_the_contract_cannot_answer_is_not_found_with_no_citations(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    a = await ask(client, cid, "What is the boiling point of water on Mars?")
    assert a["answer_status"] == "not_found" and a["content"].startswith("Not found in this contract")
    assert a["citations"] == []
    assert "parties" in a["content"]                        # tells the user what CAN be asked instead of a dead end


async def test_model_declining_to_answer_becomes_not_found(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    llm.answer_mode = "refuse"
    a = await ask(client, cid, "Can we terminate early?")
    assert a["answer_status"] == "not_found" and a["citations"] == []


async def test_answer_with_an_invented_quote_is_rejected(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    llm.answer_mode = "hallucinate"
    a = await ask(client, cid, "Can we terminate early?")
    assert a["answer_status"] == "not_found" and a["citations"] == []
    assert "five (5) days" not in a["content"]                 # the hallucinated answer is not shown


async def test_without_llm_chat_shows_labelled_passages_not_an_answer(client, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]   # LLM_PROVIDER=none
    a = await ask(client, cid, "Can we terminate early?")
    assert a["answer_status"] == "unavailable"
    assert "unavailable" in a["content"] and "not an answer" in a["content"]
    assert a["citations"] and all(c["snippet"] for c in a["citations"])


async def test_chat_history_is_persisted_in_order(client, llm, long_pdf):
    cid = (await upload_and_process(client, long_pdf))["id"]
    await ask(client, cid, "Can we terminate early?")
    h = (await client.get(f"/api/contracts/{cid}/chat")).json()
    assert [m["role"] for m in h] == ["USER", "ASSISTANT"]
    assert h[0]["content"] == "Can we terminate early?"
