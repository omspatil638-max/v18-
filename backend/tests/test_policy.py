"""Your own rules: checked in code against known values, shown as ordinary flags, never guessing."""

import pytest

from app.core.config import settings
from app.services import job_runner, policy_service as ps
from tests.helpers import upload, upload_and_process

PASSWORD = "correct horse battery"


def rule(**over):
    body = {"name": "Long termination notice", "field": "termination_notice_days", "operator": "gt", "value": 60,
            "severity": "HIGH", "message": None, "enabled": True}
    body.update(over)
    return body


async def add(client, **over):
    r = await client.post("/api/policy-rules", json=rule(**over))
    assert r.status_code == 201, r.text
    return r.json()["rules"][-1]


async def policy_flags(client, cid):
    flags = (await client.get(f"/api/contracts/{cid}/flags")).json()
    return [f for f in flags if f["code"] == "POLICY_RULE"]


def field(d, key):
    return next(f for f in d["fields"] if f["field_key"] == key)


# ─── reading the payment period ───────────────────────────────────────────────

@pytest.mark.parametrize("text,days", [
    ("payable within thirty (30) days of the invoice date", 30),
    ("Net 45, invoiced monthly", 45),
    ("payable within 60 days of receipt", 60),
    ("net30", 30),
    ("Customer shall pay within thirty (30) days; disputes within fifteen (15) days", 30),
    ("a monthly fee of $12,500 invoiced monthly", None),
    ("paid on the first of each month", None),
    ("", None), (None, None),
])
def test_the_payment_period_is_read_only_when_it_is_stated(text, days):
    assert ps.payment_days_of(text) == days


# ─── managing rules ───────────────────────────────────────────────────────────

async def test_rules_can_be_listed_added_changed_and_removed(client):
    r = (await client.get("/api/policy-rules")).json()
    assert r["rules"] == [] and r["max_rules"] == 30
    keys = {c["key"] for c in r["catalog"]}
    assert keys == {"termination_notice_days", "renewal_notice_days", "payment_days", "auto_renew", "missing_field"}
    assert r["missing_targets"]["expiration_date"] == "Expiration date" and r["operator_labels"]["gt"] == "more than"
    made = await add(client)
    assert made["name"] == "Long termination notice" and made["enabled"] is True and made["value"] == 60
    r2 = await client.put(f"/api/policy-rules/{made['id']}", json=rule(value=90, severity="LOW", enabled=False))
    assert r2.status_code == 200 and r2.json()["rules"][0]["value"] == 90 and r2.json()["rules"][0]["enabled"] is False
    assert (await client.delete(f"/api/policy-rules/{made['id']}")).status_code == 204
    assert (await client.get("/api/policy-rules")).json()["rules"] == []


@pytest.mark.parametrize("over", [
    {"name": "  "}, {"field": "colour"}, {"operator": "contains"}, {"operator": "is_true"}, {"value": None}, {"value": -1},
    {"value": 99999}, {"value": True}, {"severity": "EXTREME"},
    {"field": "auto_renew", "operator": "gt"}, {"field": "missing_field", "operator": "missing", "target": "nope", "value": None},
])
async def test_invalid_rules_are_rejected(client, over):
    r = await client.post("/api/policy-rules", json=rule(**over))
    assert r.status_code == 422, over
    assert (await client.get("/api/policy-rules")).json()["rules"] == []


async def test_there_is_a_cap_on_the_number_of_rules(client):
    for i in range(30):
        await add(client, name=f"rule {i}")
    r = await client.post("/api/policy-rules", json=rule(name="one too many"))
    assert r.status_code == 422 and "at most 30" in r.json()["detail"]


# ─── rules become flags ───────────────────────────────────────────────────────

async def test_a_rule_that_fires_becomes_a_flag_that_names_the_rule_the_value_and_the_source(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)                      # termination notice is 90 days
    assert await policy_flags(client, d["id"]) == []
    await add(client)
    f = (await policy_flags(client, d["id"]))[0]
    assert f["severity"] == "HIGH" and f["status"] == "OPEN" and f["target_label"] == "Long termination notice"
    assert "Your rule" in f["reason"] and "90 days" in f["reason"] and "more than 60 days" in f["reason"]
    assert f["source_page"] and f["source_quote"]
    hl = await client.get(f"/api/contracts/{d['id']}/pages/{f['source_page']}/highlights", params={"q": f["source_quote"]})
    assert hl.json()["matched"] is True


async def test_a_rule_that_does_not_fire_leaves_no_flag(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await add(client, value=120)
    assert await policy_flags(client, d["id"]) == []


@pytest.mark.parametrize("over,fires", [
    ({"operator": "gt", "value": 89}, True), ({"operator": "gt", "value": 90}, False),
    ({"operator": "gte", "value": 90}, True), ({"operator": "lt", "value": 91}, True),
    ({"operator": "lt", "value": 90}, False), ({"operator": "lte", "value": 90}, True),
    ({"operator": "eq", "value": 90}, True), ({"operator": "eq", "value": 60}, False),
])
async def test_every_comparison_is_exact(client, llm, long_pdf, over, fires):
    d = await upload_and_process(client, long_pdf)
    await add(client, **over)
    assert bool(await policy_flags(client, d["id"])) is fires


async def test_payment_period_auto_renewal_and_missing_value_rules(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)                       # pays within 30 days, auto-renews
    await add(client, name="Slow payment", field="payment_days", operator="gt", value=45, severity="LOW")
    await add(client, name="Any auto-renewal", field="auto_renew", operator="is_true", value=None, severity="MEDIUM")
    await add(client, name="Needs a liability note", field="auto_renew", operator="is_false", value=None)
    names = {f["target_label"] for f in await policy_flags(client, d["id"])}
    assert names == {"Any auto-renewal"}
    await client.post(f"/api/contracts/{d['id']}/fields/{field(d, 'termination_notice_period')['id']}/review", json={"action": "not_in_contract"})
    await add(client, name="Must state termination notice", field="missing_field", operator="missing", value=None,
              target="termination_notice_period", severity="HIGH")
    flags = {f["target_label"]: f for f in await policy_flags(client, d["id"])}
    assert "Must state termination notice" in flags and "was not found" in flags["Must state termination notice"]["reason"]


async def test_unknown_values_never_fire_a_rule(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await client.post(f"/api/contracts/{d['id']}/fields/{field(d, 'payment_terms')['id']}/review",
                      json={"action": "correct", "value": "Paid monthly."})              # no period stated
    await add(client, name="Slow payment", field="payment_days", operator="gt", value=0)
    assert await policy_flags(client, d["id"]) == []


async def test_a_rule_on_a_value_that_was_never_analysed_does_not_claim_it_is_missing(client, long_pdf):
    d = await upload_and_process(client, long_pdf)                       # no LLM: every field is extraction_unavailable
    await add(client, name="Needs notice", field="missing_field", operator="missing", value=None, target="termination_notice_period")
    assert await policy_flags(client, d["id"]) == []


async def test_disabling_or_deleting_a_rule_removes_its_flag_and_enabling_restores_it(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    made = await add(client)
    assert await policy_flags(client, d["id"])
    await client.put(f"/api/policy-rules/{made['id']}", json=rule(enabled=False))
    assert await policy_flags(client, d["id"]) == []
    await client.put(f"/api/policy-rules/{made['id']}", json=rule(enabled=True))
    assert len(await policy_flags(client, d["id"])) == 1
    await client.delete(f"/api/policy-rules/{made['id']}")
    assert await policy_flags(client, d["id"]) == []


async def test_a_decision_on_a_rule_flag_survives_editing_the_rule(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    made = await add(client)
    fid = (await policy_flags(client, d["id"]))[0]["id"]
    assert (await client.patch(f"/api/flags/{fid}", json={"status": "RESOLVED"})).status_code == 200
    await client.put(f"/api/policy-rules/{made['id']}", json=rule(value=80))
    f = (await policy_flags(client, d["id"]))[0]
    assert f["status"] == "RESOLVED" and "more than 80 days" in f["reason"]


async def test_correcting_a_value_re_checks_the_rules(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await add(client)
    assert await policy_flags(client, d["id"])
    r = await client.post(f"/api/contracts/{d['id']}/fields/{field(d, 'termination_notice_period')['id']}/review",
                          json={"action": "correct", "value": "30 days"})
    assert r.status_code == 200
    assert await policy_flags(client, d["id"]) == []


async def test_rules_apply_to_contracts_uploaded_later_and_survive_reprocessing(client, llm, long_pdf):
    await add(client)
    d = await upload_and_process(client, long_pdf)
    assert len(await policy_flags(client, d["id"])) == 1
    await client.post(f"/api/contracts/{d['id']}/reprocess")
    await job_runner.wait_idle()
    assert len(await policy_flags(client, d["id"])) == 1


async def test_flags_from_a_rule_carry_a_custom_message(client, llm, long_pdf):
    d = await upload_and_process(client, long_pdf)
    await add(client, message="Legal wants 60 days or less. Ask for a change.")
    assert (await policy_flags(client, d["id"]))[0]["reason"] == "Legal wants 60 days or less. Ask for a change."


async def test_rules_are_private_to_their_owner(client, new_client, monkeypatch, llm, long_pdf):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    cid = (await upload(a, long_pdf)).json()["id"]
    await job_runner.wait_idle()
    made = (await b.post("/api/policy-rules", json=rule())).json()["rules"][0]
    assert await policy_flags(a, cid) == []                              # b's rule does not touch a's contracts
    assert (await a.get("/api/policy-rules")).json()["rules"] == []
    assert (await a.put(f"/api/policy-rules/{made['id']}", json=rule())).status_code == 404
    assert (await a.delete(f"/api/policy-rules/{made['id']}")).status_code == 404
    assert (await anon.get("/api/policy-rules")).status_code == 401
    assert (await anon.post("/api/policy-rules", json=rule())).status_code == 401
