"""The data-quality centre: an honest to-do list of what is missing, unverified or unreadable."""

from app.core.config import settings
from app.services import job_runner
from tests.helpers import upload, upload_and_process

PASSWORD = "correct horse battery"


def issue(report, code):
    return next((i for i in report["issues"] if i["code"] == code), None)


async def report_of(client):
    r = await client.get("/api/data-quality")
    assert r.status_code == 200, r.text
    return r.json()


async def test_an_empty_account_has_nothing_to_report(client):
    r = await report_of(client)
    assert r["contracts_total"] == 0 and r["issues"] == [] and r["open_issues"] == 0
    assert all(c["total"] == 0 and c["have"] == 0 for c in r["coverage"])


async def test_coverage_counts_only_values_that_are_verified_or_confirmed(client, llm, long_pdf):
    await upload_and_process(client, long_pdf)
    r = await report_of(client)
    assert r["contracts_total"] == 1 and r["analysed"] == 1
    cov = {c["field_key"]: c for c in r["coverage"]}
    assert cov["expiration_date"] == {"field_key": "expiration_date", "label": cov["expiration_date"]["label"], "have": 1, "total": 1}
    assert cov["effective_date"]["have"] == 1


async def test_an_unreadable_file_is_listed_as_could_not_be_read(client, blank_pdf):
    await upload(client, blank_pdf, "scan.pdf")
    await job_runner.wait_idle()
    r = await report_of(client)
    i = issue(r, "cannot_read")
    assert i and i["severity"] == "high" and i["count"] == 1 and i["contracts"][0]["title"]
    assert r["analysed"] == 0


async def test_without_an_llm_the_contract_is_reported_as_not_analysed_not_as_clear(client, long_pdf):
    await upload_and_process(client, long_pdf)                 # no `llm` fixture: extraction is unavailable
    r = await report_of(client)
    i = issue(r, "analysis_incomplete")
    assert i and i["severity"] == "high" and "AI extraction did not run" in i["contracts"][0]["detail"]
    assert issue(r, "no_expiry") is None                       # it does not claim the date is missing when nothing was read


async def test_a_missing_expiry_is_reported_until_a_person_says_it_is_absent(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    exp = next(f for f in d["fields"] if f["field_key"] == "expiration_date")
    assert issue(await report_of(client), "no_expiry") is None
    await client.post(f"/api/contracts/{d['id']}/fields/{exp['id']}/review", json={"action": "not_in_contract"})
    r = await report_of(client)
    assert issue(r, "no_expiry") is None                       # a person confirmed it: resolved, not nagging
    assert next(c for c in r["coverage"] if c["field_key"] == "expiration_date")["have"] == 0    # but coverage stays honest


async def test_issues_are_sorted_most_serious_first_and_carry_a_reason_and_action(client, llm, long_pdf, blank_pdf):
    await upload_and_process(client, long_pdf)
    await upload(client, blank_pdf, "scan.pdf")
    await job_runner.wait_idle()
    r = await report_of(client)
    order = {"high": 0, "medium": 1, "low": 2}
    sev = [order[i["severity"]] for i in r["issues"]]
    assert sev == sorted(sev)
    assert all(i["why"] and i["action"] and i["title"] and i["count"] == len(i["contracts"]) for i in r["issues"])
    assert r["open_issues"] == sum(i["count"] for i in r["issues"])


async def test_deleted_contracts_are_not_reported(client, blank_pdf):
    cid = (await upload(client, blank_pdf, "scan.pdf")).json()["id"]
    await job_runner.wait_idle()
    assert issue(await report_of(client), "cannot_read")
    await client.delete(f"/api/contracts/{cid}")
    assert (await report_of(client))["contracts_total"] == 0


async def test_the_report_is_per_user(client, new_client, monkeypatch, blank_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    await upload(a, blank_pdf, "scan.pdf")
    await job_runner.wait_idle()
    assert (await report_of(a))["contracts_total"] == 1
    assert (await report_of(b))["contracts_total"] == 0
    assert (await anon.get("/api/data-quality")).status_code == 401
