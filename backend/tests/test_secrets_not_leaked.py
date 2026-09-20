"""
Credentials must never escape backend/.env: not through logs, tracebacks,
settings dumps, or any API response.
"""

import json
import logging

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings, settings
from app.services.llm_service import LLMService

CANARY = "gsk_THIS_MUST_NEVER_APPEAR_abcdef1234567890"


@pytest.fixture
def loaded(monkeypatch):
    """A settings object holding the canary key, exactly as .env would load it."""
    monkeypatch.setattr(settings, "GROQ_API_KEY", SecretStr(CANARY))
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    return settings


def test_key_is_masked_in_repr_str_and_dumps(loaded):
    assert CANARY not in repr(loaded)
    assert CANARY not in str(loaded)
    assert CANARY not in str(loaded.GROQ_API_KEY)
    assert str(loaded.GROQ_API_KEY) == "**********"
    assert CANARY not in str(loaded.model_dump())
    assert CANARY not in loaded.model_dump_json()


def test_key_does_not_leak_through_a_traceback(loaded):
    """A crash that formats settings (a very common accident) must not expose it."""
    try:
        raise RuntimeError(f"boom: {loaded!r} {loaded.GROQ_API_KEY!r}")
    except RuntimeError as exc:
        assert CANARY not in str(exc)


def test_key_does_not_leak_through_logging(loaded, caplog):
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("test").warning("settings=%s key=%s", loaded, loaded.GROQ_API_KEY)
    assert CANARY not in caplog.text


def test_validation_error_on_settings_does_not_echo_the_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", CANARY)
    monkeypatch.setenv("MAX_UPLOAD_MB", "not-a-number")
    with pytest.raises(Exception) as exc:
        Settings(_env_file=None)
    assert CANARY not in str(exc.value)


def test_accessor_still_returns_the_real_value_for_api_calls(loaded):
    assert loaded.groq_api_key == CANARY
    assert LLMService().status()["configured"] is True


async def test_key_is_never_returned_by_any_api_response(client, loaded):
    for path in ("/api/system/status", "/api/contracts", "/api/auth/config", "/health"):
        r = await client.get(path)
        assert CANARY not in r.text, f"{path} leaked the API key"
    status = (await client.get("/api/system/status")).json()
    assert "api_key" not in json.dumps(status).lower()


async def test_key_is_sent_only_to_the_provider_endpoint(monkeypatch, loaded):
    """The one place the real key may appear: the Authorization header to Groq itself."""
    seen = []
    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(lambda req: (
            seen.append((str(req.url), req.headers.get("authorization"), req.content.decode())),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"x":1}'},
                                                   "finish_reason": "stop"}], "usage": {"total_tokens": 10}}),
        )[1])
        return real(*a, **kw)

    monkeypatch.setattr("app.services.llm_service.httpx.AsyncClient", factory)
    monkeypatch.setattr(settings, "LLM_TPM_LIMIT", 10 ** 9)
    await LLMService().complete_json("sys", "usr", {"type": "object", "properties": {},
                                                    "required": [], "additionalProperties": False})
    url, auth, body = seen[0]
    assert url.startswith("https://api.groq.com/")      # never any other host
    assert auth == f"Bearer {CANARY}"                   # the key goes only in the auth header
    assert CANARY not in body                           # never inside the prompt payload


# ─── Regression: a key pasted into the WRONG variable must never be echoed ──────
# This actually happened: the key went into LLM_PROVIDER, and the "unknown provider"
# message published it in /api/system/status, which the dashboard renders.

def test_key_pasted_into_llm_provider_is_never_echoed(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", CANARY)
    st = LLMService().status()
    assert st["configured"] is False
    assert CANARY not in json.dumps(st)
    assert "GROQ_API_KEY" in st["reason"] and "LLM_PROVIDER" in st["reason"]


def test_key_pasted_into_llm_model_is_never_echoed(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "GROQ_API_KEY", SecretStr("gsk_realkey_0000000000000000"))
    monkeypatch.setattr(settings, "LLM_MODEL", CANARY)
    st = LLMService().status()
    assert CANARY not in json.dumps(st)
    assert st["configured"] is False and "LLM_MODEL" in st["reason"]


async def test_misplaced_key_is_not_served_by_the_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", CANARY)
    r = await client.get("/api/system/status")
    assert CANARY not in r.text
    assert r.json()["llm_configured"] is False


def test_provider_error_body_echoing_a_key_is_scrubbed():
    from app.services.llm_service import LLMError, LLMUnavailable, scrub
    exc = LLMError("bad_request", f"LLM request rejected (400): {{'auth': 'Bearer {CANARY}'}}")
    assert CANARY not in exc.message and "<redacted>" in exc.message
    exc2 = LLMUnavailable("model_not_found", f"Model or endpoint not found ({CANARY}).")
    assert CANARY not in exc2.message
    assert scrub(f"key={CANARY} and sk-ant-abcdefghijklmnop") == "key=<redacted> and <redacted>"


def test_scrub_removes_the_configured_key_even_if_it_is_not_key_shaped(monkeypatch):
    from app.services.llm_service import scrub
    odd = "ThisIsAnOddlyFormattedCredential123"
    monkeypatch.setattr(settings, "GROQ_API_KEY", SecretStr(odd))
    assert odd not in scrub(f"provider said: {odd}")
