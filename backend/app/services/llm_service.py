"""
llm_service.py
--------------
Provider-agnostic LLM access with three honest outcomes:

  * a result                 – the model answered and (for JSON) the reply parsed
  * LLMUnavailable           – not configured / key rejected / daily quota exhausted
  * LLMError                 – a transient or malformed-response failure

There is intentionally NO fallback that fabricates output. Callers must handle the
two exceptions and surface "extraction unavailable" to the user.

Providers (LLM_PROVIDER):  none | groq | openai_compatible (Ollama, vLLM, ...) | anthropic
Model and endpoint always come from settings (LLM_MODEL / LLM_BASE_URL).
"""

import asyncio
import json
import logging
import re
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

import httpx

from app.core.config import settings
from app.services.privacy_service import Redactor

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


async def _sleep(seconds: float) -> None:
    """Indirection so tests can run rate-limit waits instantly."""
    await asyncio.sleep(seconds)


# Anything that looks like a credential, whoever produced it: our own config, a
# provider's error body (some echo the Authorization header), or a stray paste.
_KEY_SHAPED = re.compile(r"(?:gsk_|sk-ant-|sk-|xai-|AIza|hf_|Bearer\s+)[A-Za-z0-9_\-]{8,}")


def scrub(text: Optional[str]) -> str:
    """
    Remove credential material from any text that may reach a user, a log or the API.
    Applied to every LLM error message and to the status reason, so a key can never
    travel outward even when it was configured in the wrong place.
    """
    if not text:
        return ""
    out = str(text)
    for configured in (settings.groq_api_key, settings.anthropic_api_key, settings.openai_api_key):
        if configured and len(configured) >= 8:
            out = out.replace(configured, "<redacted>")
    # Also catch a value pasted into the wrong variable, or echoed back by a provider.
    return _KEY_SHAPED.sub("<redacted>", out)


def looks_like_api_key(value: Optional[str]) -> bool:
    """True if a config value appears to be a credential (so we can say so without printing it)."""
    v = (value or "").strip()
    return bool(v) and (bool(_KEY_SHAPED.fullmatch(v)) or (len(v) >= 24 and re.fullmatch(r"[A-Za-z0-9_\-]{24,}", v) is not None))


class LLMUnavailable(Exception):
    """The LLM cannot be used right now (config, auth, quota). Not a per-request glitch."""

    def __init__(self, code: str, message: str):
        message = scrub(message)
        super().__init__(message)
        self.code = code
        self.message = message


class LLMError(Exception):
    """A single call failed (network, 5xx, bad JSON, truncated output, request too large)."""

    def __init__(self, code: str, message: str):
        message = scrub(message)
        super().__init__(message)
        self.code = code
        self.message = message


class _Retry(Exception):
    def __init__(self, message: str, retry_after: Optional[float] = None, daily: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily


# ─── Rate limiting (sliding 60 s window over requests and tokens) ─────────────

class RateLimiter:
    def __init__(self) -> None:
        self._events: Deque[List[float]] = deque()  # [timestamp, tokens]
        self._lock = asyncio.Lock()

    async def acquire(self, est_tokens: int) -> List[float]:
        rpm, tpm = settings.LLM_RPM_LIMIT, settings.LLM_TPM_LIMIT
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._events and now - self._events[0][0] >= 60:
                    self._events.popleft()
                used = sum(e[1] for e in self._events)
                if len(self._events) < rpm and (not self._events or used + est_tokens <= tpm):
                    break
                wait = 60 - (now - self._events[0][0]) if self._events else 1
                await _sleep(max(0.25, wait))
            entry = [time.monotonic(), float(est_tokens)]
            self._events.append(entry)
            return entry

    @staticmethod
    def settle(entry: List[float], actual_tokens: int) -> None:
        entry[1] = float(actual_tokens)


_limiter = RateLimiter()


def _estimate_tokens(*texts: str, max_out: int) -> int:
    return int(sum(len(t) for t in texts) / 3.2) + min(max_out, 1500)


# ─── Provider implementations ─────────────────────────────────────────────────

def _strip_json(text: str) -> Any:
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", t)
    if m:
        t = m.group(1).strip()
    if not (t.startswith("{") or t.startswith("[")):
        s = min([i for i in (t.find("{"), t.find("[")) if i != -1], default=-1)
        if s == -1:
            raise LLMError("bad_json", "The model did not return JSON.")
        e = max(t.rfind("}"), t.rfind("]"))
        t = t[s:e + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError as exc:
        raise LLMError("bad_json", f"The model returned malformed JSON: {exc}") from exc


class _OpenAICompatible:
    """Groq and any OpenAI-compatible server (Ollama, vLLM, LM Studio, ...)."""

    def __init__(self, base_url: str, api_key: str, model: str, is_groq: bool):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.is_groq = is_groq

    async def _post(self, payload: Dict[str, Any]) -> Tuple[str, int, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=settings.LLM_TIMEOUT_S) as client:
                resp = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise _Retry(f"network error: {exc.__class__.__name__}") from exc

        if resp.status_code == 429:
            retry_after = None
            try:
                retry_after = float(resp.headers.get("retry-after", ""))
            except ValueError:
                pass
            body = resp.text.lower()
            raise _Retry("rate limited", retry_after, daily=("per day" in body or "tpd" in body or "rpd" in body))
        if resp.status_code in (401, 403):
            raise LLMUnavailable("auth", "The LLM API key was rejected. Check the key in backend/.env.")
        if resp.status_code == 404:
            raise LLMUnavailable("model_not_found", f"Model or endpoint not found ({self.model}). Check LLM_MODEL / LLM_BASE_URL.")
        if resp.status_code == 413:
            raise LLMError("too_large", "The request is too large for the model's per-minute token limit.")
        if resp.status_code >= 500:
            raise _Retry(f"server error {resp.status_code}")
        if resp.status_code >= 400:
            raise LLMError("bad_request", f"LLM request rejected ({resp.status_code}): {resp.text[:300]}")

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        finish = choice.get("finish_reason") or ""
        used = int((data.get("usage") or {}).get("total_tokens") or 0)
        if finish == "length":
            raise LLMError("truncated", "The model's reply was cut off (output limit reached).")
        return content, used, finish

    def _base_payload(self, messages: List[Dict[str, str]], max_tokens: int) -> Dict[str, Any]:
        p: Dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0}
        p["max_completion_tokens" if self.is_groq else "max_tokens"] = max_tokens
        if self.model.startswith("openai/gpt-oss") and settings.LLM_REASONING_EFFORT:
            p["reasoning_effort"] = settings.LLM_REASONING_EFFORT
        return p

    async def json(self, system: str, user: str, schema: Dict[str, Any], name: str, max_tokens: int) -> Tuple[Any, int]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        payload = self._base_payload(messages, max_tokens)
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        }
        try:
            content, used, _ = await self._post(payload)
        except LLMError as exc:
            # Some servers do not implement json_schema; retry in plain JSON mode with the schema in the prompt.
            if exc.code != "bad_request" or "response_format" not in exc.message.lower() and "schema" not in exc.message.lower():
                raise
            messages[0]["content"] += "\n\nReturn ONLY a JSON object matching this JSON Schema:\n" + json.dumps(schema)
            payload = self._base_payload(messages, max_tokens)
            payload["response_format"] = {"type": "json_object"}
            content, used, _ = await self._post(payload)
        return _strip_json(content), used

    async def text(self, system: str, user: str, max_tokens: int) -> Tuple[str, int]:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        content, used, _ = await self._post(self._base_payload(messages, max_tokens))
        return content.strip(), used


class _Anthropic:
    def __init__(self, api_key: str, model: str):
        import anthropic
        self._anthropic = anthropic
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=settings.LLM_TIMEOUT_S)
        self.model = model

    def _map_error(self, exc: Exception):
        a = self._anthropic
        if isinstance(exc, a.AuthenticationError) or isinstance(exc, a.PermissionDeniedError):
            raise LLMUnavailable("auth", "The Anthropic API key was rejected.") from exc
        if isinstance(exc, a.NotFoundError):
            raise LLMUnavailable("model_not_found", f"Model not found ({self.model}). Check LLM_MODEL.") from exc
        if isinstance(exc, a.RateLimitError):
            ra = None
            try:
                ra = float(exc.response.headers.get("retry-after", ""))
            except Exception:
                pass
            raise _Retry("rate limited", ra) from exc
        if isinstance(exc, (a.APIConnectionError, a.APITimeoutError, a.InternalServerError)):
            raise _Retry(f"provider error: {exc.__class__.__name__}") from exc
        raise LLMError("bad_request", f"Anthropic request failed: {exc}") from exc

    async def json(self, system: str, user: str, schema: Dict[str, Any], name: str, max_tokens: int) -> Tuple[Any, int]:
        try:
            resp = await self.client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=0, system=system,
                messages=[{"role": "user", "content": user}],
                tools=[{"name": name, "description": "Return the structured result.", "input_schema": schema}],
                tool_choice={"type": "tool", "name": name},
            )
        except Exception as exc:  # noqa: BLE001
            self._map_error(exc)
        if getattr(resp, "stop_reason", "") == "max_tokens":
            raise LLMError("truncated", "The model's reply was cut off (output limit reached).")
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                used = (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)
                return block.input, used
        raise LLMError("bad_json", "The model did not return structured output.")

    async def text(self, system: str, user: str, max_tokens: int) -> Tuple[str, int]:
        try:
            resp = await self.client.messages.create(
                model=self.model, max_tokens=max_tokens, temperature=0, system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:  # noqa: BLE001
            self._map_error(exc)
        text = "".join(getattr(b, "text", "") for b in resp.content)
        return text.strip(), (resp.usage.input_tokens or 0) + (resp.usage.output_tokens or 0)


class _Fake:
    """Deterministic provider for tests. Only constructible when APP_ENV=test."""

    handler: Optional[Callable[[str, str, str], Any]] = None

    async def json(self, system: str, user: str, schema: Dict[str, Any], name: str, max_tokens: int) -> Tuple[Any, int]:
        if _Fake.handler is None:
            raise LLMUnavailable("not_configured", "Fake LLM has no handler installed.")
        out = _Fake.handler(system, user, name)
        if isinstance(out, str):
            out = _strip_json(out)
        return out, 100

    async def text(self, system: str, user: str, max_tokens: int) -> Tuple[str, int]:
        if _Fake.handler is None:
            raise LLMUnavailable("not_configured", "Fake LLM has no handler installed.")
        out = _Fake.handler(system, user, "text")
        return (out if isinstance(out, str) else json.dumps(out)), 100


def set_fake_handler(handler: Optional[Callable[[str, str, str], Any]]) -> None:
    """Tests: install fn(system, user, schema_name) -> dict | str, or None to remove."""
    _Fake.handler = handler


# ─── Service ──────────────────────────────────────────────────────────────────

class LLMService:
    def status(self) -> Dict[str, Any]:
        """Describe the current configuration without making any network call."""
        provider, model = settings.llm_provider, settings.llm_model
        reason: Optional[str] = None
        # NOTE: never interpolate a configuration VALUE into `reason`. It is shown in the
        # UI and a mis-pasted credential would be published. Name the variable instead.
        if provider == "invalid":
            if looks_like_api_key(settings.LLM_PROVIDER):
                reason = ("An API key appears to have been pasted into LLM_PROVIDER. In backend/.env set "
                          "LLM_PROVIDER=groq and put the key in GROQ_API_KEY instead, then restart the backend.")
            else:
                reason = "LLM_PROVIDER is not recognised. Use one of: none, groq, openai_compatible, anthropic."
        elif provider == "none":
            reason = "No LLM is configured (LLM_PROVIDER=none). Set LLM_PROVIDER=groq and GROQ_API_KEY in backend/.env."
        elif provider == "groq" and not settings.groq_api_key:
            reason = "GROQ_API_KEY is empty. Create a free key at console.groq.com and add it to backend/.env."
        elif provider == "anthropic" and not settings.anthropic_api_key:
            reason = "ANTHROPIC_API_KEY is empty."
        elif provider == "openai_compatible" and not (settings.LLM_BASE_URL and model):
            reason = "openai_compatible needs LLM_BASE_URL and LLM_MODEL (for Ollama: http://localhost:11434/v1)."
        elif provider == "fake" and settings.APP_ENV != "test":
            reason = "LLM_PROVIDER=fake is only available when APP_ENV=test."

        if looks_like_api_key(settings.LLM_MODEL):
            reason = ("An API key appears to have been pasted into LLM_MODEL. Put the key in GROQ_API_KEY "
                      "and leave LLM_MODEL empty to use the default model.")

        # `model` is echoed to the UI, so it is scrubbed as a last line of defence.
        return {"provider": provider, "model": scrub(model), "configured": reason is None, "reason": scrub(reason) or None}

    @property
    def is_configured(self) -> bool:
        return self.status()["configured"]

    def _provider(self):
        st = self.status()
        if not st["configured"]:
            raise LLMUnavailable("not_configured", st["reason"])
        p, model = st["provider"], st["model"]
        if p == "groq":
            return _OpenAICompatible(GROQ_BASE_URL, settings.groq_api_key, model, is_groq=True)
        if p == "openai_compatible":
            return _OpenAICompatible(settings.LLM_BASE_URL, settings.openai_api_key, model, is_groq=False)
        if p == "anthropic":
            return _Anthropic(settings.anthropic_api_key, model)
        return _Fake()

    async def _run(self, est_tokens: int, call: Callable[[], Any]) -> Any:
        last: Optional[str] = None
        for attempt in range(settings.LLM_MAX_RETRIES + 1):
            entry = await _limiter.acquire(est_tokens)
            try:
                result, used = await call()
                _limiter.settle(entry, used or est_tokens)
                return result
            except _Retry as exc:
                last = str(exc)
                _limiter.settle(entry, est_tokens)
                wait = exc.retry_after if exc.retry_after is not None else min(2 ** attempt * 2, 30)
                if exc.daily or wait > settings.LLM_MAX_WAIT_S:
                    raise LLMUnavailable(
                        "rate_limited",
                        f"The LLM provider's rate/quota limit was reached (retry in about {int(wait)}s). "
                        "Try again later or use a provider with higher limits.",
                    ) from exc
                logger.warning("LLM %s; retrying in %.0fs (attempt %d)", exc, wait, attempt + 1)
                await _sleep(wait)
        raise LLMError("unavailable_network", f"The LLM did not respond after retries ({last}).")

    async def complete_json(
        self, system: str, user: str, schema: Dict[str, Any], name: str = "result", max_tokens: Optional[int] = None
    ) -> Any:
        provider = self._provider()
        max_out = max_tokens or settings.LLM_MAX_OUTPUT_TOKENS
        redactor = Redactor() if settings.privacy_redact else None
        if redactor:
            system, user = redactor.redact(system), redactor.redact(user)
        est = _estimate_tokens(system, user, json.dumps(schema), max_out=max_out)
        result = await self._run(est, lambda: provider.json(system, user, schema, name, max_out))
        return redactor.restore(result) if redactor else result

    async def complete_text(self, system: str, user: str, max_tokens: Optional[int] = None) -> str:
        provider = self._provider()
        max_out = max_tokens or settings.LLM_MAX_OUTPUT_TOKENS
        redactor = Redactor() if settings.privacy_redact else None
        if redactor:
            system, user = redactor.redact(system), redactor.redact(user)
        est = _estimate_tokens(system, user, max_out=max_out)
        result = await self._run(est, lambda: provider.text(system, user, max_out))
        return redactor.restore_text(result) if redactor else result


llm_service = LLMService()
