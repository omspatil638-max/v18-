"""
privacy_service.py
------------------
Optional privacy mode (PRIVACY_MODE=redact): personal identifiers are replaced by placeholders
before any text leaves for a hosted LLM, and put back in the model's answer afterwards.

    "write to jane.doe@acme.com or +1 (415) 555-0134"  ->  "write to [EMAIL_1] or [PHONE_1]"

What is redacted: email addresses, phone numbers, IBANs, US SSN / EIN style identifiers, payment
card numbers, URLs, and long bank-account-like digit runs.
What is NOT redacted, on purpose: party names, dates and monetary amounts. The extraction task is
to find exactly those, and the quote-exists check needs them. If names and amounts must not leave
the machine either, the answer is a local model (LLM_PROVIDER=openai_compatible with Ollama), not
a bigger regex.

Placeholders are restored inside every string of the model's JSON reply, so quotes still match the
original document and stay verifiable. A model that mangles a placeholder produces a quote that
fails verification, which is reported as needs_review, never silently trusted.
"""

import re
from typing import Any, Dict, List, Tuple

# Order matters: specific shapes first, then the generic digit runs.
_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")),
    ("URL", re.compile(r"\b(?:https?://|www\.)[^\s<>\"')]+", re.I)),
    ("IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")),
    ("CARD", re.compile(r"\b(?:\d{4}[ \-]){3}\d{4}\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("TAXID", re.compile(r"\b\d{2}-\d{7}\b")),
    ("PHONE", re.compile(r"(?<![\w$])(?:\+\d{1,3}[ .\-]?)?(?:\(\d{2,4}\)[ .\-]?|\d{2,4}[ .\-])\d{3,4}[ .\-]\d{3,4}(?!\w)")),
    ("ACCOUNT", re.compile(r"(?<![\w$.,])\d{9,18}(?![\w.,]\d)")),
]
_PLACEHOLDER = re.compile(r"\[([A-Z]+)_(\d+)\]")


class Redactor:
    """One instance per LLM call: placeholders are stable within the call and reversible."""

    def __init__(self) -> None:
        self._by_value: Dict[str, str] = {}
        self._by_placeholder: Dict[str, str] = {}
        self._counts: Dict[str, int] = {}

    def _placeholder(self, kind: str, value: str) -> str:
        key = f"{kind}:{value}"
        if key not in self._by_value:
            self._counts[kind] = self._counts.get(kind, 0) + 1
            ph = f"[{kind}_{self._counts[kind]}]"
            self._by_value[key] = ph
            self._by_placeholder[ph] = value
        return self._by_value[key]

    def redact(self, text: str) -> str:
        if not text:
            return text
        for kind, pattern in _PATTERNS:
            text = pattern.sub(lambda m, k=kind: self._placeholder(k, m.group(0)), text)
        return text

    def restore_text(self, text: str) -> str:
        if not self._by_placeholder or "[" not in text:
            return text
        return _PLACEHOLDER.sub(lambda m: self._by_placeholder.get(m.group(0), m.group(0)), text)

    def restore(self, data: Any) -> Any:
        """Restore placeholders in every string of a JSON-like structure."""
        if isinstance(data, str):
            return self.restore_text(data)
        if isinstance(data, list):
            return [self.restore(x) for x in data]
        if isinstance(data, dict):
            return {k: self.restore(v) for k, v in data.items()}
        return data

    @property
    def redaction_count(self) -> int:
        return len(self._by_placeholder)
