"""Asking across all contracts: the question becomes a filter, the filter runs in code, rows come back with sources."""

from datetime import date, timedelta

import pytest

from app.core.config import settings
from app.services import job_runner, portfolio_service as ps
from app.services.llm_service import LLMUnavailable, set_fake_handler
from tests.helpers import upload, upload_and_process

PASSWORD = "correct horse battery"
TODAY = date(2026, 9, 20)


def field(d, key):
    return next(f for f in d["fields"] if f["field_key"] == key)


async def correct(client, d, key, value):
    r = await client.post(f"/api/contracts/{d['id']}/fields/{field(d, key)['id']}/review", json={"action": "correct", "value": value})
    assert r.status_code == 200, r.text


async def ask(client, q):
    r = await client.post("/api/portfolio/ask", json={"question": q})
    assert r.status_code == 200, r.text
    return r.json()


def in_days(n):
    return (date.today() + timedelta(days=n)).isoformat()


# ─── understanding common wording with rules (no model) ───────────────────────

@pytest.mark.parametrize("q,check", [
    ("Which contracts expire in the next 90 days?", lambda f: (f.expires_from, f.expires_to) == (TODAY, TODAY + timedelta(days=90))),
    ("contracts expiring within 3 months", lambda f: f.expires_to == date(2026, 12, 20)),
    ("which contracts end in the next 2 weeks", lambda f: f.expires_to == TODAY + timedelta(days=14)),
    ("Which contracts auto-renew?", lambda f: f.auto_renew is True and f.expires_from is None),
    ("contracts that renew automatically", lambda f: f.auto_renew is True),
    ("contracts that do not auto-renew", lambda f: f.auto_renew is False),
    ("contracts without auto renewal", lambda f: f.auto_renew is False),
    ("Which contracts expire this quarter and auto-renew?", lambda f: (f.expires_from, f.expires_to, f.auto_renew) == (date(2026, 7, 1), date(2026, 9, 30), True)),
    ("what expires next quarter", lambda f: (f.expires_from, f.expires_to) == (date(2026, 10, 1), date(2026, 12, 31))),
    ("expiring next year", lambda f: (f.expires_from, f.expires_to) == (date(2027, 1, 1), date(2027, 12, 31))),
    ("expiring this year", lambda f: f.expires_to == date(2026, 12, 31)),
    ("what expires next month", lambda f: (f.expires_from, f.expires_to) == (date(2026, 10, 1), date(2026, 10, 31))),
    ("contracts that have already expired", lambda f: f.expires_to == date(2026, 9, 19) and f.expires_from is None),
    ("auto renewing contracts this quarter", lambda f: f.auto_renew is True and f.expires_to == date(2026, 9, 30)),
    ("notice period longer than 60 days", lambda f: (f.notice_days_gt, f.notice_scope) == (60, "either")),
    ("termination notice longer than 2 months", lambda f: (f.notice_days_gt, f.notice_scope) == (60, "termination")),
    ("renewal notice shorter than 30 days", lambda f: (f.notice_days_lt, f.notice_scope) == (30, "renewal")),
    ("contracts with high priority flags", lambda f: f.has_high_flags),
    ("which need review", lambda f: f.needs_review),
    ("contracts with no expiry date", lambda f: f.no_expiry),
    ("contracts tagged vendor", lambda f: f.tag == "vendor"),
])
def test_common_wordings_are_understood_by_rules(q, check):
    f = ps.rule_parse(q, TODAY)
    assert check(f), (q, f)


@pytest.mark.parametrize("q", ["What does the contract say about spending?", "hello there", "tell me a joke", "who pays whom"])
def test_questions_that_are_not_filters_produce_no_filter(q):
    assert ps.rule_parse(q, TODAY).is_empty()


# ─── running the filter over real data ────────────────────────────────────────

async def test_expiring_soon_returns_only_contracts_whose_known_date_is_in_range_with_the_value_shown(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    r = await ask(client, "Which contracts expire in the next 90 days?")
    assert r["status"] == "no_match" and r["matches"] == [] and r["total_contracts"] == 1   # expires 2028
    await correct(client, d, "expiration_date", in_days(50))
    r = await ask(client, "Which contracts expire in the next 90 days?")
    assert r["status"] == "answered" and r["interpreted_by"] == "rules" and len(r["matches"]) == 1
    m = r["matches"][0]
    assert m["contract_id"] == d["id"] and m["facts"][0]["value"] == in_days(50) and m["facts"][0]["label"] == "Expiration date"
    assert m["facts"][0]["reviewed"] is True
    assert r["summary"].startswith("1 of 1 analysed contract match") and any("Expires between" in x for x in r["interpretation"])


async def test_a_verified_match_carries_its_source(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    r = await ask(client, "Which contracts auto-renew?")
    fact = r["matches"][0]["facts"][0]
    assert r["matches"][0]["contract_id"] == d["id"] and fact["status"] == "verified" and fact["page"] and fact["quote"]
    r2 = await client.get(f"/api/contracts/{d['id']}/pages/{fact['page']}/highlights", params={"q": fact["quote"]})
    assert r2.json()["matched"] is True                       # the source really is on that page


async def test_notice_period_comparisons_use_computed_days(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)                # termination notice: 90 days
    assert (await ask(client, "termination notice period longer than 60 days"))["status"] == "answered"
    assert (await ask(client, "termination notice period longer than 120 days"))["status"] == "no_match"
    assert (await ask(client, "termination notice shorter than 120 days"))["status"] == "answered"


async def test_unknown_values_never_match(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await client.post(f"/api/contracts/{d['id']}/fields/{field(d, 'expiration_date')['id']}/review", json={"action": "not_in_contract"})
    assert (await ask(client, "Which contracts expire in the next 3000 days?"))["matches"] == []
    r = await ask(client, "contracts with no expiry date")
    assert r["status"] == "answered" and r["matches"][0]["facts"][0]["value"] == "not found"


async def test_tags_and_types_filter_and_combine(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await client.patch(f"/api/contracts/{d['id']}", json={"tags": ["Vendor"], "contract_type": "NDA"})
    assert (await ask(client, "contracts tagged vendor"))["status"] == "answered"
    assert (await ask(client, "contracts tagged other"))["status"] == "no_match"
    both = await ask(client, "contracts tagged vendor that auto-renew")
    assert both["status"] == "answered" and len(both["matches"][0]["facts"]) == 2


async def test_high_priority_flags_filter(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    flags = (await client.get(f"/api/contracts/{d['id']}/flags")).json()
    has_high = any(f["severity"] == "HIGH" and f["status"] == "OPEN" for f in flags)
    r = await ask(client, "contracts with high priority flags")
    assert (r["status"] == "answered") is has_high


# ─── the model may translate wording, nothing more ────────────────────────────

def install(handler, inner):
    set_fake_handler(lambda s, u, n: handler(s, u) if n == "portfolio_filter" else inner(s, u, n))


async def test_free_form_wording_goes_through_the_model_but_it_sees_no_contract_data(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await correct(client, d, "expiration_date", in_days(50))
    seen = []

    def translate(system, user):
        seen.append((system, user))
        return {"supported": True, "expires_within_days": 90, "expires_from": None, "expires_to": None, "auto_renew": None,
                "notice_scope": None, "notice_days_gt": None, "notice_days_lt": None, "has_high_flags": None,
                "needs_review": None, "no_expiry": None, "contract_type": None, "tag": None, "counterparty": None}

    install(translate, llm)
    r = await ask(client, "Which of my agreements are wrapping up soonish?")
    assert r["interpreted_by"] == "ai" and r["status"] == "answered" and r["matches"][0]["contract_id"] == d["id"]
    prompt = seen[0][0] + seen[0][1]
    assert "Northwind" not in prompt and "Contoso" not in prompt and d["title"] not in prompt
    assert "Question: Which of my agreements are wrapping up soonish?" in seen[0][1]


async def test_a_bad_translation_is_validated_in_code(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    bad = {"supported": True, "expires_within_days": 99999999, "expires_from": "not a date", "expires_to": "1600-01-01",
           "auto_renew": "yes", "notice_scope": "sideways", "notice_days_gt": -5, "notice_days_lt": 0, "has_high_flags": "true",
           "needs_review": None, "no_expiry": None, "contract_type": "   ", "tag": None, "counterparty": None}
    install(lambda s, u: bad, llm)
    r = await ask(client, "Which of my agreements are wrapping up soonish?")
    assert r["interpreted_by"] != "ai" and r["matches"] == []               # nothing usable survived; nothing invented


async def test_when_the_model_is_unavailable_the_rules_still_work_and_the_rest_says_so(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)

    def down(s, u):
        raise LLMUnavailable("rate_limited", "quota")

    install(down, llm)
    assert (await ask(client, "Which contracts auto-renew?"))["status"] == "answered"                     # rules: no model needed
    r = await ask(client, "Which of my agreements are wrapping up soonish?")
    assert r["status"] == "unavailable_ai" and "unavailable" in r["summary"]


async def test_supported_false_falls_back_to_a_text_search(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    install(lambda s, u: {"supported": False}, llm)
    r = await ask(client, "what does it say about force majeure")
    assert r["status"] == "passages" and r["interpreted_by"] == "keyword" and r["matches"] == []
    p = r["passages"][0]
    assert p["contract_id"] == d["id"] and p["page"] and "not an answer" in r["summary"]


async def test_without_any_model_a_keyword_question_returns_real_passages(client, long_pdf):
    d = await upload_and_process(client, long_pdf)                         # no `llm` fixture
    r = await ask(client, "force majeure")
    assert r["status"] == "passages" and r["passages"][0]["contract_id"] == d["id"]
    r2 = await ask(client, "zzzzqqqq wwwwyyyy")
    assert r2["status"] == "not_understood" and r2["passages"] == [] and r2["examples"]


# ─── validation and access ────────────────────────────────────────────────────

async def test_bad_questions_are_rejected(client):
    assert (await client.post("/api/portfolio/ask", json={"question": ""})).status_code == 422
    assert (await client.post("/api/portfolio/ask", json={"question": "x" * 501})).status_code == 422
    assert (await client.post("/api/portfolio/ask", json={})).status_code == 422
    r = await ask(client, "ab")
    assert r["status"] == "not_understood" and r["matches"] == []


async def test_deleted_contracts_are_not_matched(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    assert (await ask(client, "Which contracts auto-renew?"))["status"] == "answered"
    await client.delete(f"/api/contracts/{d['id']}")
    assert (await ask(client, "Which contracts auto-renew?"))["matches"] == []


async def test_asking_is_per_user(client, new_client, monkeypatch, llm, long_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    await upload(a, long_pdf)
    await job_runner.wait_idle()
    assert (await ask(a, "Which contracts auto-renew?"))["status"] == "answered"
    rb = await ask(b, "Which contracts auto-renew?")
    assert rb["matches"] == [] and rb["total_contracts"] == 0
    assert (await anon.post("/api/portfolio/ask", json={"question": "auto-renew"})).status_code == 401
