"""The renewal radar: what ends or renews soon and by when to act, all computed in code with sources."""

from datetime import date, timedelta

from app.core.config import settings
from app.services import job_runner, renewal_service
from tests.helpers import upload, upload_and_process

PASSWORD = "correct horse battery"


def field(d, key):
    return next(f for f in d["fields"] if f["field_key"] == key)


async def correct(client, d, key, value):
    r = await client.post(f"/api/contracts/{d['id']}/fields/{field(d, key)['id']}/review", json={"action": "correct", "value": value})
    assert r.status_code == 200, r.text


async def radar(client, **params):
    r = await client.get("/api/renewals", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def in_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


async def test_an_auto_renewing_contract_is_due_on_its_notice_deadline_not_its_expiry(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)             # auto-renews, 60 days' notice
    await correct(client, d, "expiration_date", in_days(70))
    r = await radar(client)
    it = r["items"][0]
    assert it["kind"] == "give_notice_by" and it["action_date"] == in_days(10) and it["days_to_action"] == 10
    assert it["expiry_date"] == in_days(70) and it["bucket"] == "30" and it["auto_renew"] is True
    assert it["notice_deadline"] == in_days(10) and "60" in it["notice_period"]
    assert f"unless notice is given by {in_days(10)}" in it["what_happens"]
    assert r["summary"]["due_30"] == 1 and r["summary"]["auto_renewing"] == 1


async def test_a_missed_notice_deadline_is_shown_as_missed_and_never_hidden(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await correct(client, d, "expiration_date", in_days(20))   # notice was due 40 days ago
    r = await radar(client, window=30)
    it = r["items"][0]
    assert it["kind"] == "notice_missed" and it["bucket"] == "notice_missed" and it["days_to_action"] == -40
    assert "has passed" in it["what_happens"] and r["summary"]["notice_missed"] == 1


async def test_a_contract_that_does_not_auto_renew_is_due_on_its_expiry(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await correct(client, d, "auto_renew", "no")
    await correct(client, d, "expiration_date", in_days(45))
    it = (await radar(client))["items"][0]
    assert it["kind"] == "expires" and it["action_date"] == in_days(45) and it["bucket"] == "60"
    assert it["what_happens"] == f"Ends on {in_days(45)} unless a renewal is agreed."


async def test_the_window_decides_what_is_listed_and_far_dates_are_left_out(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await correct(client, d, "expiration_date", in_days(400))                # notice deadline in 340 days
    assert (await radar(client))["items"] == []
    it = (await radar(client, window=500))["items"][0]
    assert it["bucket"] == "later" and it["days_to_action"] == 340


async def test_recently_ended_contracts_are_listed_only_within_the_expired_window(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await correct(client, d, "expiration_date", in_days(-10))
    it = (await radar(client))["items"][0]
    assert it["kind"] == "expired" and it["bucket"] == "expired" and "Was due to renew" in it["what_happens"]
    assert (await radar(client, expired_days=5))["items"] == []


async def test_a_contract_without_an_expiry_is_listed_as_not_tracked(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await client.post(f"/api/contracts/{d['id']}/fields/{field(d, 'expiration_date')['id']}/review", json={"action": "not_in_contract"})
    r = await radar(client)
    assert r["items"] == [] and r["not_tracked"][0]["contract_id"] == d["id"]
    assert "no renewal alerts" in r["not_tracked"][0]["reason"]


async def test_amounts_are_quoted_as_written_and_never_added_up(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await correct(client, d, "expiration_date", in_days(70))
    it = (await radar(client))["items"][0]
    assert it["amounts"] == ["$12,500"]
    assert "total" not in " ".join(it["what_happens"].lower().split())


async def test_sources_and_verification_are_reported_and_reviewed_values_are_not_called_unverified(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    it = (await radar(client, window=1000))["items"]
    assert it[0]["sources"]["expiry"]["page"] and it[0]["sources"]["expiry"]["status"] == "verified" and it[0]["unverified"] == []
    await correct(client, d, "expiration_date", in_days(70))                       # typed by a person: needs_review, but reviewed
    it2 = (await radar(client))["items"][0]
    assert it2["sources"]["expiry"]["status"] == "needs_review" and it2["sources"]["expiry"]["page"] is None
    assert "expiration_date" not in it2["unverified"]


async def test_items_are_ordered_by_when_you_must_act(client, llm, long_pdf):
    a = await upload_and_process(client, long_pdf)
    b = (await upload(client, long_pdf, "second.pdf")).json()
    await job_runner.wait_idle()
    b = (await client.get(f"/api/contracts/{b['id']}")).json()
    await correct(client, a, "expiration_date", in_days(85))       # act in 25 days
    await correct(client, b, "expiration_date", in_days(70))       # act in 10 days
    dates = [i["action_date"] for i in (await radar(client))["items"]]
    assert dates == sorted(dates) and len(dates) == 2


async def test_deleted_contracts_and_bad_parameters(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await correct(client, d, "expiration_date", in_days(70))
    await client.delete(f"/api/contracts/{d['id']}")
    assert (await radar(client))["items"] == []
    for bad in ({"window": 1}, {"window": 99999}, {"expired_days": -1}):
        assert (await client.get("/api/renewals", params=bad)).status_code == 422


async def test_the_radar_is_per_user(client, new_client, monkeypatch, llm, long_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    cid = (await upload(a, long_pdf)).json()["id"]
    await job_runner.wait_idle()
    d = (await a.get(f"/api/contracts/{cid}")).json()
    await correct(a, d, "expiration_date", in_days(70))
    assert len((await radar(a))["items"]) == 1
    assert (await radar(b))["items"] == [] and (await radar(b))["not_tracked"] == []
    assert (await anon.get("/api/renewals")).status_code == 401


def test_what_happens_says_unknown_where_the_contract_text_was_not_read_that_way():
    t = date(2026, 9, 20)
    s = renewal_service.what_happens(None, date(2027, 1, 1), None, False, t)
    assert "could not be determined" in s
    s2 = renewal_service.what_happens(True, date(2027, 1, 1), None, False, t)
    assert "notice period was not found" in s2
