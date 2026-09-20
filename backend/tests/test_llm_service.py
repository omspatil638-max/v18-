import json

import httpx
import pytest

from app.core.config import settings
from app.services import llm_service as mod
from app.services.llm_service import LLMError, LLMService, LLMUnavailable, RateLimiter

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"], "additionalProperties": False}


def cfg(monkeypatch, **kw):
    defaults = dict(LLM_PROVIDER="none", LLM_MODEL="", LLM_BASE_URL="", GROQ_API_KEY="", ANTHROPIC_API_KEY="", OPENAI_API_KEY="")
    for k, v in {**defaults, **kw}.items():
        monkeypatch.setattr(settings, k, v)


@pytest.fixture
def waits(monkeypatch):
    """Record rate-limit sleeps instead of actually waiting."""
    log = []

    async def fake_sleep(s):
        log.append(s)

    monkeypatch.setattr(mod, "_sleep", fake_sleep)
    monkeypatch.setattr(mod, "_limiter", RateLimiter())
    return log


_REAL_CLIENT = httpx.AsyncClient   # captured once so repeated patching within a test does not stack


def mock_http(monkeypatch, handler):
    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return _REAL_CLIENT(*a, **kw)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)


def ok_response(content='{"x": "hi"}', finish="stop", tokens=50):
    return httpx.Response(200, json={
        "choices": [{"message": {"content": content}, "finish_reason": finish}], "usage": {"total_tokens": tokens},
    })


# ─── configuration: provider and model are env-driven, nothing hardcoded ───────

@pytest.mark.parametrize("provider", ["none", "local", "", "off"])
def test_no_or_legacy_local_provider_is_reported_unconfigured(monkeypatch, provider):
    cfg(monkeypatch, LLM_PROVIDER=provider)
    st = LLMService().status()
    assert st["provider"] == "none" and st["configured"] is False and "LLM_PROVIDER" in st["reason"]


def test_groq_needs_key_then_uses_default_model_overridable_by_env(monkeypatch):
    cfg(monkeypatch, LLM_PROVIDER="groq")
    st = LLMService().status()
    assert not st["configured"] and "GROQ_API_KEY" in st["reason"]
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="gsk_test")
    st = LLMService().status()
    assert st["configured"] and st["model"] == "openai/gpt-oss-120b"
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="gsk_test", LLM_MODEL="openai/gpt-oss-20b")
    assert LLMService().status()["model"] == "openai/gpt-oss-20b"


def test_anthropic_default_model_is_current_and_configurable(monkeypatch):
    cfg(monkeypatch, LLM_PROVIDER="claude", ANTHROPIC_API_KEY="sk-ant-x")     # legacy alias
    st = LLMService().status()
    assert st["provider"] == "anthropic" and st["configured"] and st["model"] == "claude-sonnet-5"
    assert "20241022" not in st["model"]
    cfg(monkeypatch, LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-x", LLM_MODEL="claude-opus-5")
    assert LLMService().status()["model"] == "claude-opus-5"


def test_openai_compatible_requires_explicit_base_url_and_model(monkeypatch):
    cfg(monkeypatch, LLM_PROVIDER="ollama")
    assert not LLMService().status()["configured"]
    cfg(monkeypatch, LLM_PROVIDER="openai_compatible", LLM_BASE_URL="http://localhost:11434/v1", LLM_MODEL="qwen2.5:14b")
    st = LLMService().status()
    assert st["configured"] and st["model"] == "qwen2.5:14b"


def test_unknown_provider_is_rejected_without_echoing_the_value(monkeypatch):
    """The reason names the variable and the valid options, never the value it holds
    (a mis-pasted API key would otherwise be published in /api/system/status)."""
    cfg(monkeypatch, LLM_PROVIDER="skynet")
    st = LLMService().status()
    assert not st["configured"]
    assert "skynet" not in st["reason"]
    assert "LLM_PROVIDER" in st["reason"] and "groq" in st["reason"]


async def test_unconfigured_call_raises_unavailable_never_returns_text(monkeypatch):
    cfg(monkeypatch, LLM_PROVIDER="none")
    with pytest.raises(LLMUnavailable) as e:
        await LLMService().complete_json("s", "u", SCHEMA)
    assert e.value.code == "not_configured"
    with pytest.raises(LLMUnavailable):
        await LLMService().complete_text("s", "u")


# ─── Groq / OpenAI-compatible wire behaviour ───────────────────────────────────

async def test_groq_request_uses_configured_model_strict_schema_and_low_reasoning(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="gsk_secret", LLM_MODEL="openai/gpt-oss-20b")
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers.get("authorization"), json.loads(req.content)
        return ok_response()

    mock_http(monkeypatch, handler)
    out = await LLMService().complete_json("sys", "usr", SCHEMA, "thing")
    assert out == {"x": "hi"}
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer gsk_secret"
    b = seen["body"]
    assert b["model"] == "openai/gpt-oss-20b" and b["temperature"] == 0
    assert b["response_format"] == {"type": "json_schema", "json_schema": {"name": "thing", "strict": True, "schema": SCHEMA}}
    assert b["reasoning_effort"] == "low" and "max_completion_tokens" in b and "max_tokens" not in b


async def test_generic_openai_compatible_server_uses_its_own_url_and_max_tokens(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="openai_compatible", LLM_BASE_URL="http://localhost:11434/v1/", LLM_MODEL="qwen2.5:14b")
    seen = {}

    def handler(req):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers.get("authorization"), json.loads(req.content)
        return ok_response('```json\n{"x": "fenced"}\n```')

    mock_http(monkeypatch, handler)
    assert await LLMService().complete_json("s", "u", SCHEMA) == {"x": "fenced"}
    assert seen["url"] == "http://localhost:11434/v1/chat/completions" and seen["auth"] is None
    assert "max_tokens" in seen["body"] and "reasoning_effort" not in seen["body"]


async def test_server_without_json_schema_support_falls_back_to_json_object_mode(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="openai_compatible", LLM_BASE_URL="http://x/v1", LLM_MODEL="m")
    bodies = []

    def handler(req):
        body = json.loads(req.content)
        bodies.append(body)
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="unsupported response_format json_schema")
        return ok_response()

    mock_http(monkeypatch, handler)
    assert await LLMService().complete_json("s", "u", SCHEMA) == {"x": "hi"}
    assert [b["response_format"]["type"] for b in bodies] == ["json_schema", "json_object"]
    assert "JSON Schema" in bodies[1]["messages"][0]["content"]


async def test_429_waits_for_retry_after_then_succeeds(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="k")
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "7"}, text="rate limit reached (TPM)")
        return ok_response()

    mock_http(monkeypatch, handler)
    assert await LLMService().complete_json("s", "u", SCHEMA) == {"x": "hi"}
    assert len(calls) == 2 and 7 in waits


async def test_daily_quota_exhaustion_raises_unavailable_without_retrying(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="k")
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "4000"}, text="Rate limit reached: tokens per day (TPD)")

    mock_http(monkeypatch, handler)
    with pytest.raises(LLMUnavailable) as e:
        await LLMService().complete_json("s", "u", SCHEMA)
    assert e.value.code == "rate_limited" and len(calls) == 1


async def test_rejected_key_raises_unavailable_auth(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="bad")
    mock_http(monkeypatch, lambda req: httpx.Response(401, text="invalid api key"))
    with pytest.raises(LLMUnavailable) as e:
        await LLMService().complete_json("s", "u", SCHEMA)
    assert e.value.code == "auth"


async def test_unknown_model_raises_unavailable_model_not_found(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="k", LLM_MODEL="nope/none")
    mock_http(monkeypatch, lambda req: httpx.Response(404, text="model not found"))
    with pytest.raises(LLMUnavailable) as e:
        await LLMService().complete_json("s", "u", SCHEMA)
    assert e.value.code == "model_not_found" and "nope/none" in e.value.message


async def test_truncated_and_malformed_replies_are_errors_not_guesses(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="k")
    mock_http(monkeypatch, lambda req: ok_response('{"x": "cut', finish="length"))
    with pytest.raises(LLMError) as e:
        await LLMService().complete_json("s", "u", SCHEMA)
    assert e.value.code == "truncated"
    mock_http(monkeypatch, lambda req: ok_response("Sure! Here is the answer."))
    with pytest.raises(LLMError) as e:
        await LLMService().complete_json("s", "u", SCHEMA)
    assert e.value.code == "bad_json"


async def test_persistent_server_errors_exhaust_retries_then_fail(monkeypatch, waits):
    cfg(monkeypatch, LLM_PROVIDER="groq", GROQ_API_KEY="k")
    monkeypatch.setattr(settings, "LLM_MAX_RETRIES", 2)
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(503, text="overloaded")

    mock_http(monkeypatch, handler)
    with pytest.raises(LLMError) as e:
        await LLMService().complete_json("s", "u", SCHEMA)
    assert e.value.code == "unavailable_network" and len(calls) == 3


# ─── rate limiter ──────────────────────────────────────────────────────────────

async def test_rate_limiter_paces_requests_to_the_token_budget(monkeypatch):
    now = [1000.0]
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        now[0] += s

    monkeypatch.setattr(mod, "_sleep", fake_sleep)
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(settings, "LLM_TPM_LIMIT", 6500)
    monkeypatch.setattr(settings, "LLM_RPM_LIMIT", 25)

    rl = RateLimiter()
    e1 = await rl.acquire(4000)
    assert slept == []                      # first call goes straight through
    RateLimiter.settle(e1, 4000)
    await rl.acquire(4000)                  # 4000 + 4000 > 6500: must wait for the window to clear
    assert slept and sum(slept) >= 59       # ~60 s window


async def test_rate_limiter_uses_actual_usage_to_avoid_over_waiting(monkeypatch):
    now = [1000.0]
    slept = []

    async def fake_sleep(s):
        slept.append(s)
        now[0] += s

    monkeypatch.setattr(mod, "_sleep", fake_sleep)
    monkeypatch.setattr(mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(settings, "LLM_TPM_LIMIT", 6500)
    rl = RateLimiter()
    e1 = await rl.acquire(4000)
    RateLimiter.settle(e1, 900)             # the call really used only 900 tokens
    await rl.acquire(4000)
    assert slept == []
