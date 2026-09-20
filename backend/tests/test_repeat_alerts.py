"""Unread alerts are re-sent (email and browser push) until they are marked read."""

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import SecretStr

from app.core.config import settings
from app.services import alert_service, push_service
from tests.helpers import upload_and_process

FAR = date.today() + timedelta(days=2000)          # everything is due by then
T0 = datetime.now(timezone.utc)
PASSWORD = "correct horse battery"


def hours(n: float) -> datetime:
    return T0 + timedelta(hours=n)


class _FarDate(date):
    @classmethod
    def today(cls):
        return FAR


@pytest.fixture(autouse=True)
def far_today(monkeypatch):
    """The API and the cycle agree that it is 'FAR' days from now, so every alert is due."""
    from app.api import alerts as alerts_api
    monkeypatch.setattr(alerts_api, "date", _FarDate)


@pytest.fixture
def smtp(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.test")
    monkeypatch.setattr(settings, "SMTP_FROM", "alerts@contractlens.test")
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://app.test")
    monkeypatch.setattr(settings, "ALERT_MAX_REPEATS", 2)
    alert_service.set_sender(sent.append)
    yield sent
    alert_service.set_sender(None)


@pytest.fixture
def push(monkeypatch):
    sent = []
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", "BPublicKeyForTestsOnly")
    monkeypatch.setattr(settings, "VAPID_PRIVATE_KEY", SecretStr("private-for-tests-only"))
    monkeypatch.setattr(settings, "ALERT_MAX_REPEATS", 2)
    monkeypatch.setattr(settings, "APP_BASE_URL", "http://app.test")
    push_service.set_sender(lambda sub, payload: sent.append((sub, payload)))
    yield sent
    push_service.set_sender(None)


async def setup_email(client, long_pdf, **extra):
    await upload_and_process(client, long_pdf)
    r = await client.put("/api/alerts/settings", json={"email_to": "me@example.com", "email_enabled": True, **extra})
    assert r.status_code == 200, r.text


# ─── email repeats ────────────────────────────────────────────────────────────

async def test_an_unread_alert_is_emailed_again_after_the_interval_until_the_cap(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf)
    r = await alert_service.run_alert_cycle(today=FAR, now=T0)
    assert r["users_emailed"] == 1 and len(smtp) == 1
    assert "reminder" not in smtp[0]["Subject"].lower()

    assert (await alert_service.run_alert_cycle(today=FAR, now=hours(23)))["users_emailed"] == 0      # not yet
    assert len(smtp) == 1

    r2 = await alert_service.run_alert_cycle(today=FAR, now=hours(25))
    assert r2["users_emailed"] == 1 and r2["reminders_sent"] > 0 and len(smtp) == 2
    assert "reminder 1" in smtp[1]["Subject"].lower() and "not been marked as read" in smtp[1].get_content()

    await alert_service.run_alert_cycle(today=FAR, now=hours(50))                                      # reminder 2 (the cap)
    assert len(smtp) == 3
    assert (await alert_service.run_alert_cycle(today=FAR, now=hours(75)))["users_emailed"] == 0       # cap reached: stop
    assert len(smtp) == 3


async def test_marking_alerts_read_stops_the_reminders(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf)
    await alert_service.run_alert_cycle(today=FAR, now=T0)
    ack = await client.post("/api/alerts/acknowledge-all")
    assert ack.status_code == 200
    r = await alert_service.run_alert_cycle(today=FAR, now=hours(48))
    assert r["users_emailed"] == 0 and len(smtp) == 1


async def test_a_single_acknowledged_alert_is_left_out_of_later_reminders(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf)
    await alert_service.run_alert_cycle(today=FAR, now=T0)
    alerts = (await client.get("/api/alerts?due_only=true")).json()
    assert len(alerts) >= 2
    assert (await client.patch(f"/api/alerts/{alerts[0]['id']}/acknowledge")).status_code == 200
    await alert_service.run_alert_cycle(today=FAR, now=hours(25))
    later = smtp[-1].get_content()
    assert later.count("- ") <= len(alerts) - 1 + 3                       # digest lines are per unique deadline; smaller than before
    assert len(smtp) == 2


async def test_repeat_off_sends_only_the_first_email(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf, repeat_enabled=False)
    await alert_service.run_alert_cycle(today=FAR, now=T0)
    assert (await alert_service.run_alert_cycle(today=FAR, now=hours(100)))["users_emailed"] == 0
    assert len(smtp) == 1


async def test_a_custom_interval_is_respected(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf, repeat_hours=6)
    await alert_service.run_alert_cycle(today=FAR, now=T0)
    assert (await alert_service.run_alert_cycle(today=FAR, now=hours(5)))["users_emailed"] == 0
    assert (await alert_service.run_alert_cycle(today=FAR, now=hours(7)))["users_emailed"] == 1


async def test_a_failed_send_is_not_counted_and_is_retried(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf)
    alert_service.set_sender(lambda m: (_ for _ in ()).throw(ConnectionRefusedError("down")))
    assert (await alert_service.run_alert_cycle(today=FAR, now=T0))["failed"] == 1
    alert_service.set_sender(smtp.append)
    r = await alert_service.run_alert_cycle(today=FAR, now=hours(0.1))          # retried straight away as a FIRST email
    assert r["users_emailed"] == 1 and "reminder" not in smtp[0]["Subject"].lower()


async def test_the_email_contains_a_working_one_click_link_and_the_dashboard_link(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf)
    await alert_service.run_alert_cycle(today=FAR, now=T0)
    body = smtp[0].get_content()
    assert "http://app.test/ack/" in body and "http://app.test/" in body and "not legal advice" in body.lower()
    token = body.split("http://app.test/ack/")[1].split()[0]

    seen = await client.get(f"/api/alerts/ack/{token}")
    assert seen.json()["valid"] is True and seen.json()["pending"] > 0
    assert (await client.get(f"/api/alerts/ack/{token}")).json()["pending"] == seen.json()["pending"]   # GET never mutates
    done = await client.post(f"/api/alerts/ack/{token}")
    assert done.status_code == 200 and done.json()["acknowledged"] == seen.json()["pending"]
    assert (await client.get(f"/api/alerts/ack/{token}")).json()["pending"] == 0
    assert (await alert_service.run_alert_cycle(today=FAR, now=hours(30)))["users_emailed"] == 0


async def test_bad_ack_tokens_do_nothing(client):
    for tok in ("x" * 30, "short", "a" * 70):
        assert (await client.get(f"/api/alerts/ack/{tok}")).json() == {"valid": False, "pending": 0}
        assert (await client.post(f"/api/alerts/ack/{tok}")).status_code == 404


async def test_deleted_contracts_are_not_nagged_about(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf)
    cid = (await client.get("/api/contracts")).json()[0]["id"]
    await client.delete(f"/api/contracts/{cid}")
    assert (await alert_service.run_alert_cycle(today=FAR, now=T0))["users_emailed"] == 0 and smtp == []


async def test_settings_validate_and_report_the_repeat_options(client, monkeypatch):
    monkeypatch.setattr(settings, "ALERT_MAX_REPEATS", 4)
    s = (await client.get("/api/alerts/settings")).json()
    assert s["repeat_enabled"] is True and s["repeat_hours"] == 24 and s["max_repeats"] == 4
    assert s["push_enabled"] is False and s["push_subscriptions"] == 0
    assert (await client.put("/api/alerts/settings", json={"repeat_hours": 0})).status_code == 422
    assert (await client.put("/api/alerts/settings", json={"repeat_hours": 500})).status_code == 422
    ok = (await client.put("/api/alerts/settings", json={"repeat_hours": 12, "repeat_enabled": False})).json()
    assert ok["repeat_hours"] == 12 and ok["repeat_enabled"] is False


async def test_alert_objects_report_how_often_they_were_emailed(client, llm, long_pdf, smtp):
    await setup_email(client, long_pdf)
    before = (await client.get("/api/alerts?due_only=true")).json()
    await alert_service.run_alert_cycle(today=FAR, now=T0)
    await alert_service.run_alert_cycle(today=FAR, now=hours(25))
    after = (await client.get("/api/alerts")).json()
    assert all(a["email_count"] == 0 and a["emailed_at"] is None for a in before)
    assert any(a["email_count"] == 2 and a["emailed_at"] for a in after)


# ─── browser push ─────────────────────────────────────────────────────────────

SUB = {"endpoint": "https://push.example.test/abc123456789", "keys": {"p256dh": "p256dh-key-value", "auth": "auth-secret"}}


async def setup_push(client, long_pdf):
    await upload_and_process(client, long_pdf)
    assert (await client.post("/api/push/subscribe", json=SUB)).status_code == 200
    assert (await client.put("/api/alerts/settings", json={"push_enabled": True})).status_code == 200


async def test_push_is_unavailable_until_the_server_has_keys(client, monkeypatch):
    monkeypatch.setattr(settings, "VAPID_PUBLIC_KEY", "")
    assert (await client.get("/api/push/public-key")).json() == {"public_key": None}
    assert (await client.post("/api/push/subscribe", json=SUB)).status_code == 503
    assert (await client.post("/api/push/test")).status_code == 503
    assert (await client.get("/api/alerts/settings")).json()["push_available"] is False


async def test_push_is_sent_repeated_and_stopped_by_reading(client, llm, long_pdf, push):
    assert (await client.get("/api/push/public-key")).json()["public_key"] == "BPublicKeyForTestsOnly"
    await setup_push(client, long_pdf)
    assert (await client.get("/api/alerts/settings")).json()["push_subscriptions"] == 1

    r = await alert_service.run_alert_cycle(today=FAR, now=T0)
    assert r["users_pushed"] == 1 and len(push) == 1
    import json
    p = json.loads(push[0][1])
    assert "need attention" in p["title"] and p["url"] == "http://app.test/" and p["body"]
    assert push[0][0]["endpoint"] == SUB["endpoint"]

    assert (await alert_service.run_alert_cycle(today=FAR, now=hours(2)))["users_pushed"] == 0
    r2 = await alert_service.run_alert_cycle(today=FAR, now=hours(25))
    assert r2["users_pushed"] == 1 and "Reminder" in json.loads(push[1][1])["title"]

    await client.post("/api/alerts/acknowledge-all")
    assert (await alert_service.run_alert_cycle(today=FAR, now=hours(60)))["users_pushed"] == 0
    assert len(push) == 2


async def test_expired_subscriptions_are_removed(client, llm, long_pdf, push):
    await setup_push(client, long_pdf)

    def gone(sub, payload):
        raise push_service.PushGone()

    push_service.set_sender(gone)
    r = await alert_service.run_alert_cycle(today=FAR, now=T0)
    assert r["users_pushed"] == 0
    assert (await client.get("/api/alerts/settings")).json()["push_subscriptions"] == 0


async def test_push_test_endpoint_and_unsubscribe(client, push):
    assert (await client.post("/api/push/test")).status_code == 503                      # nothing registered yet
    assert (await client.post("/api/push/subscribe", json=SUB)).status_code == 200
    ok = await client.post("/api/push/test")
    assert ok.status_code == 200 and ok.json()["sent"] == 1 and len(push) == 1
    assert (await client.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]})).status_code == 200
    assert (await client.get("/api/alerts/settings")).json()["push_subscriptions"] == 0


async def test_only_https_push_endpoints_are_accepted(client, push):
    bad = {**SUB, "endpoint": "http://push.example.test/abc123456789"}
    assert (await client.post("/api/push/subscribe", json=bad)).status_code == 422


async def test_push_endpoints_are_owner_only(client, new_client, monkeypatch, push):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    a, b, anon = client, new_client(), new_client()
    for c, e in ((a, "a@x.com"), (b, "b@x.com")):
        assert (await c.post("/api/auth/register", json={"email": e, "name": "T", "password": PASSWORD})).status_code == 201
    assert (await a.post("/api/push/subscribe", json=SUB)).status_code == 200
    await b.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]})           # b cannot remove a's browser
    assert (await a.get("/api/alerts/settings")).json()["push_subscriptions"] == 1
    assert (await anon.post("/api/push/subscribe", json=SUB)).status_code == 401
    assert (await anon.post("/api/alerts/acknowledge-all")).status_code == 401
