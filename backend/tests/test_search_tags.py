"""Ctrl+K search over contracts, extracted values and passages; tags and contract types."""

import pytest

from app.core.config import settings
from app.services import job_runner
from tests.helpers import upload, upload_and_process

PASSWORD = "correct horse battery"


async def search(client, q):
    r = await client.get("/api/search", params={"q": q})
    assert r.status_code == 200, r.text
    return r.json()


async def patch(client, cid, **body):
    return await client.patch(f"/api/contracts/{cid}", json=body)


# ─── tags and types ───────────────────────────────────────────────────────────

async def test_tags_are_normalised_deduplicated_and_capped(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    r = await patch(client, d["id"], tags=["  Vendor ", "vendor", "IT   Services", "", "x" * 50])
    assert r.status_code == 200
    assert r.json()["tags"] == ["Vendor", "IT Services", "x" * 30]
    assert (await patch(client, d["id"], tags=[f"t{i}" for i in range(11)])).status_code == 422
    assert (await patch(client, d["id"], tags=[])).json()["tags"] == []


async def test_type_and_tags_show_on_the_contract_and_in_lists(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await patch(client, d["id"], contract_type="  Service   agreement ", tags=["Vendor"])
    one = (await client.get(f"/api/contracts/{d['id']}")).json()
    assert one["contract_type"] == "Service agreement" and one["tags"] == ["Vendor"]
    listed = (await client.get("/api/contracts")).json()
    assert listed[0]["contract_type"] == "Service agreement" and listed[0]["tags"] == ["Vendor"]
    assert (await patch(client, d["id"], contract_type="")).json()["contract_type"] is None


async def test_tag_and_type_counts_ignore_deleted_contracts(client, llm, long_pdf):
    a = await upload_and_process(client, long_pdf)
    b = (await upload(client, long_pdf, "second.pdf")).json()
    await job_runner.wait_idle()
    await patch(client, a["id"], tags=["Vendor", "Urgent"], contract_type="NDA")
    await patch(client, b["id"], tags=["vendor"], contract_type="NDA")
    assert (await client.get("/api/tags")).json() == [{"name": "Vendor", "count": 2}, {"name": "Urgent", "count": 1}]
    assert (await client.get("/api/contract-types")).json() == [{"name": "NDA", "count": 2}]
    await client.delete(f"/api/contracts/{b['id']}")
    assert (await client.get("/api/tags")).json() == [{"name": "Urgent", "count": 1}, {"name": "Vendor", "count": 1}]


# ─── search ───────────────────────────────────────────────────────────────────

async def test_short_or_empty_queries_return_nothing(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    for q in ("", " ", "a"):
        r = await search(client, q)
        assert r["contracts"] == r["fields"] == r["passages"] == []


async def test_contracts_are_found_by_title_counterparty_type_tag_and_party(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await patch(client, d["id"], title="Master Services Agreement", counterparty="Acme Holdings",
                contract_type="Service agreement", tags=["Renewal-2027"])
    for q, detail in (("master serv", None), ("acme", "Counterparty: Acme Holdings"), ("service agree", "Type:"),
                      ("renewal-2027", "Tagged: Renewal-2027"), ("northwind", "Party:")):
        hits = (await search(client, q))["contracts"]
        assert hits and hits[0]["contract_id"] == d["id"], q
        if detail:
            assert detail in hits[0]["detail"], (q, hits[0]["detail"])


async def test_extracted_values_are_found_with_their_source(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    hits = (await search(client, "90 days"))["fields"]
    assert hits and hits[0]["contract_id"] == d["id"] and hits[0]["status"] in ("verified", "needs_review")
    assert hits[0]["label"] and "90 days" in hits[0]["value"].lower()


async def test_passages_come_with_a_page_and_a_quote_that_can_be_highlighted(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    hits = (await search(client, "force majeure"))["passages"]
    assert hits and hits[0]["contract_id"] == d["id"] and hits[0]["page"]
    p = hits[0]
    assert "\u00ab" in p["snippet"] and "\u00ab" not in p["quote"] and "<" not in p["snippet"]
    r = await client.get(f"/api/contracts/{d['id']}/pages/{p['page']}/highlights", params={"q": p["quote"]})
    assert r.status_code == 200 and r.json()["matched"] is True


async def test_a_word_that_is_nowhere_finds_nothing(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    r = await search(client, "xyzzyplugh")
    assert r["contracts"] == r["fields"] == r["passages"] == []


@pytest.mark.parametrize("q", ["'; DROP TABLE contracts; --", "100%", "under_score", r"back\slash", "a\x00b", "(((", "\"unclosed", "!!! ???", "<script>alert(1)</script>"])
async def test_hostile_or_odd_queries_never_break_search(client, llm, long_pdf, q):
    await upload_and_process(client, long_pdf)
    r = await client.get("/api/search", params={"q": q})
    assert r.status_code == 200
    assert (await client.get("/api/contracts")).status_code == 200            # still alive


async def test_deleted_contracts_are_not_searched(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    assert (await search(client, "force majeure"))["passages"]
    await client.delete(f"/api/contracts/{d['id']}")
    r = await search(client, "force majeure")
    assert r["contracts"] == r["fields"] == r["passages"] == []


async def test_only_the_current_version_is_searched(client, llm, long_pdf, tmp_path):
    from sample_data.generate import long_contract_text, write_pdf
    from tests.helpers import upload_version
    d = await upload_and_process(client, long_pdf)
    write_pdf(tmp_path / "v2.pdf", long_contract_text().replace("Force Majeure", "Acts of Providence").replace("force majeure", "acts of providence"))
    await upload_version(client, d["id"], (tmp_path / "v2.pdf").read_bytes(), label="v2")
    assert (await search(client, "force majeure"))["passages"] == []
    assert (await search(client, "acts of providence"))["passages"]


async def test_search_is_per_user(client, new_client, monkeypatch, llm, long_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    await upload(a, long_pdf)
    await job_runner.wait_idle()
    assert (await search(a, "force majeure"))["passages"]
    rb = await search(b, "force majeure")
    assert rb["contracts"] == rb["fields"] == rb["passages"] == []
    assert (await b.get("/api/tags")).json() == []
    for path in ("/api/search?q=force", "/api/tags", "/api/contract-types"):
        assert (await anon.get(path)).status_code == 401
