"""Phase 10: privacy mode. Identifiers must not reach the provider, yet quotes must still verify."""

import pytest

from app.core.config import settings
from app.services import llm_service as mod
from app.services.llm_service import LLMService, set_fake_handler
from app.services.privacy_service import Redactor
from sample_data.generate import long_contract_text, write_pdf
from tests.helpers import upload_and_process

SCHEMA = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"], "additionalProperties": False}

SECRETS = [
    "jane.doe@northwind-analytics.com", "+1 (415) 555-0134", "DE89 3704 0044 0532 0130 00",
    "123-45-6789", "4111 1111 1111 1111", "https://portal.northwind-analytics.com/legal",
]


# ─── the redactor itself ──────────────────────────────────────────────────────

@pytest.mark.parametrize("value", SECRETS)
def test_identifiers_are_replaced_and_restored_exactly(value):
    r = Redactor()
    red = r.redact(f"Please contact {value} for details.")
    assert value not in red and "[" in red
    assert r.restore_text(red) == f"Please contact {value} for details."


def test_same_value_gets_the_same_placeholder_and_different_values_differ():
    r = Redactor()
    out = r.redact("a@x.com, b@x.com, a@x.com")
    assert out == "[EMAIL_1], [EMAIL_2], [EMAIL_1]"


@pytest.mark.parametrize("text", [
    "The monthly fee is $12,000.00, due within 30 days.",
    "Effective January 15, 2026 and expiring December 31, 2027.",
    "Late amounts accrue interest at 1.5% per month.",
    "Section 3.1 (a) applies to Northwind Analytics Inc. and Contoso Retail LLC.",
    "Invoice total $1,250,000 payable in 2026.",
])
def test_amounts_dates_and_names_are_left_alone(text):
    assert Redactor().redact(text) == text


def test_restore_walks_nested_json_and_ignores_unknown_placeholders():
    r = Redactor()
    r.redact("mail me at a@x.com")
    data = {"q": ["write to [EMAIL_1]", {"n": 3}], "other": "[EMAIL_9] stays"}
    assert r.restore(data) == {"q": ["write to a@x.com", {"n": 3}], "other": "[EMAIL_9] stays"}


# ─── in the LLM service ───────────────────────────────────────────────────────

def _fake(monkeypatch, handler, privacy):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "fake")
    monkeypatch.setattr(settings, "PRIVACY_MODE", privacy)
    monkeypatch.setattr(settings, "LLM_TPM_LIMIT", 10 ** 9)
    monkeypatch.setattr(settings, "LLM_RPM_LIMIT", 10 ** 6)
    set_fake_handler(handler)


async def test_provider_sees_placeholders_and_caller_gets_originals(monkeypatch):
    seen = []

    def handler(system, user, name):
        seen.append((system, user))
        import re
        ph = re.search(r"\[EMAIL_\d+\]", user).group(0)
        return {"x": f"quote: {ph}"}

    _fake(monkeypatch, handler, "redact")
    try:
        out = await LLMService().complete_json("sys", "Notices to jane.doe@acme.com only", SCHEMA)
    finally:
        set_fake_handler(None)
    assert "@" not in seen[0][1] and "[EMAIL_1]" in seen[0][1]
    assert out == {"x": "quote: jane.doe@acme.com"}


async def test_privacy_off_sends_text_unchanged(monkeypatch):
    seen = []
    _fake(monkeypatch, lambda s, u, n: (seen.append(u), {"x": "ok"})[1], "off")
    try:
        await LLMService().complete_json("sys", "mail jane.doe@acme.com", SCHEMA)
    finally:
        set_fake_handler(None)
    assert seen == ["mail jane.doe@acme.com"]


async def test_text_completions_are_redacted_too(monkeypatch):
    seen = []
    _fake(monkeypatch, lambda s, u, n: (seen.append(u), "answer for [PHONE_1]")[1], "redact")
    try:
        out = await LLMService().complete_text("sys", "call +1 (415) 555-0134")
    finally:
        set_fake_handler(None)
    assert "555" not in seen[0] and out == "answer for +1 (415) 555-0134"


# ─── end to end: extraction still verifies quotes that contain identifiers ────

async def test_pipeline_never_sends_identifiers_and_still_verifies(client, llm, monkeypatch, tmp_path):
    text = long_contract_text() + (
        "\n\n12. Notices\nAll legal notices must be sent to jane.doe@northwind-analytics.com "
        "or by phone to +1 (415) 555-0134."
    )
    write_pdf(tmp_path / "priv.pdf", text)
    prompts = []
    inner = llm

    def spy(system, user, name):
        prompts.append(system + "\n" + user)
        return inner(system, user, name)

    monkeypatch.setattr(settings, "PRIVACY_MODE", "redact")
    set_fake_handler(spy)
    d = await upload_and_process(client, (tmp_path / "priv.pdf").read_bytes())

    assert prompts, "the model should have been called"
    joined = "\n".join(prompts)
    for secret in ("jane.doe@northwind-analytics.com", "415) 555-0134"):
        assert secret not in joined
    assert "[EMAIL_1]" in joined
    verified = [f for f in d["fields"] if f["status"] == "verified"]
    assert len(verified) >= 5                               # the ordinary extraction is unaffected
    assert (await client.get("/api/system/status")).json()["privacy_redaction"] is True
