import uuid

import pytest

from app.core.config import settings
from app.services import job_runner
from tests.helpers import upload

PASSWORD = "correct horse battery"


@pytest.fixture
def login_mode(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_MODE", "login")


async def register(client, email, name="Tester"):
    r = await client.post("/api/auth/register", json={"email": email, "name": name, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return r.json()


async def test_user_b_cannot_read_or_modify_user_a_contract(client, new_client, login_mode, llm, long_pdf):
    a, b, anon = client, new_client(), new_client()
    await register(a, "a@example.com")
    await register(b, "b@example.com")

    cid = (await upload(a, long_pdf, "a.pdf")).json()["id"]
    await job_runner.wait_idle()
    detail = (await a.get(f"/api/contracts/{cid}")).json()
    ob_id = detail["obligations"][0]["id"]
    clause_id = detail["clauses"][0]["id"]
    reminder = (await a.get("/api/reminders")).json()
    assert reminder, "user A should have reminders to acknowledge"

    reads = [
        f"/api/contracts/{cid}", f"/api/contracts/{cid}/status", f"/api/contracts/{cid}/fields",
        f"/api/contracts/{cid}/text", f"/api/contracts/{cid}/file", f"/api/contracts/{cid}/obligations",
        f"/api/contracts/{cid}/clauses", f"/api/contracts/{cid}/clauses/{clause_id}",
        f"/api/contracts/{cid}/deadlines", f"/api/contracts/{cid}/timeline", f"/api/contracts/{cid}/chat",
        f"/api/contracts/{cid}/chunks/{uuid.uuid4()}",
    ]
    for path in reads:
        assert (await b.get(path)).status_code == 404, f"B could read {path}"
        assert (await anon.get(path)).status_code == 401, f"anonymous could read {path}"
        assert (await a.get(path)).status_code in (200, 404), path      # 404 only for the fake chunk id

    assert (await b.post(f"/api/contracts/{cid}/chat", json={"content": "Can we terminate early?"})).status_code == 404
    assert (await b.post(f"/api/contracts/{cid}/reprocess")).status_code == 404
    assert (await b.patch(f"/api/obligations/{ob_id}", json={"status": "COMPLETED"})).status_code == 404
    assert (await b.patch(f"/api/reminders/{reminder[0]['id']}/acknowledge")).status_code == 404
    assert (await b.delete(f"/api/contracts/{cid}")).status_code == 404

    assert (await b.get("/api/contracts")).json() == []
    assert (await b.get("/api/deadlines")).json() == []
    assert (await b.get("/api/reminders")).json() == []

    # ... and none of B's attempts had any effect on A's data.
    still = (await a.get(f"/api/contracts/{cid}")).json()
    assert still["obligations"][0]["status"] == "PENDING"
    assert (await a.get("/api/reminders")).json()[0]["acknowledged"] is False
    assert len((await a.get("/api/contracts")).json()) == 1


async def test_owner_can_use_and_delete_own_contract(client, login_mode, llm, long_pdf):
    await register(client, "owner@example.com")
    cid = (await upload(client, long_pdf, "mine.pdf")).json()["id"]
    await job_runner.wait_idle()
    assert (await client.get(f"/api/contracts/{cid}")).status_code == 200
    assert (await client.delete(f"/api/contracts/{cid}")).status_code == 204
    assert (await client.get(f"/api/contracts/{cid}")).status_code == 404


async def test_login_flow_and_wrong_password(client, new_client, login_mode):
    await register(client, "u@example.com")
    other = new_client()
    assert (await other.post("/api/auth/login", json={"email": "u@example.com", "password": "wrong password!"})).status_code == 401
    assert (await other.post("/api/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})).status_code == 401
    ok = await other.post("/api/auth/login", json={"email": "U@Example.com", "password": PASSWORD})
    assert ok.status_code == 200
    assert "httponly" in ok.headers["set-cookie"].lower() and "samesite=lax" in ok.headers["set-cookie"].lower()
    assert (await other.get("/api/auth/me")).json()["email"] == "u@example.com"
    assert (await other.post("/api/auth/logout")).status_code == 204
    assert (await other.get("/api/auth/me")).status_code == 401


async def test_register_validates_input_and_rejects_duplicates(client, login_mode):
    weak = await client.post("/api/auth/register", json={"email": "x@example.com", "name": "X", "password": "short"})
    assert weak.status_code == 422
    bad = await client.post("/api/auth/register", json={"email": "not-an-email", "name": "X", "password": PASSWORD})
    assert bad.status_code == 422
    await register(client, "dup@example.com")
    dup = await client.post("/api/auth/register", json={"email": "DUP@example.com", "name": "X", "password": PASSWORD})
    assert dup.status_code == 409


async def test_login_is_throttled_after_repeated_failures(client, new_client, login_mode):
    await register(client, "t@example.com")
    attacker = new_client()
    codes = [(await attacker.post("/api/auth/login", json={"email": "t@example.com", "password": f"bad password {i}"})).status_code
             for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    from app.api import auth
    auth._FAILS.clear()


async def test_demo_mode_is_refused_in_production(monkeypatch):
    import pytest as _pytest
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_MODE", "demo")
    with _pytest.raises(RuntimeError, match="not allowed"):
        settings.validate_for_startup()


async def test_insecure_session_cookie_is_refused_in_production(monkeypatch):
    import pytest as _pytest
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    monkeypatch.setattr(settings, "COOKIE_SECURE", False)
    with _pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        settings.validate_for_startup()


async def test_fake_llm_provider_is_refused_outside_tests(monkeypatch):
    import pytest as _pytest
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "AUTH_MODE", "login")
    monkeypatch.setattr(settings, "COOKIE_SECURE", True)
    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    with _pytest.raises(RuntimeError, match="only allowed when APP_ENV=test"):
        settings.validate_for_startup()
