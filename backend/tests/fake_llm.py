"""
A deterministic stand-in for an LLM, used ONLY in tests (LLM_PROVIDER=fake needs APP_ENV=test).

It "reads" an excerpt with regexes and answers in the real extraction schema, quoting the
document verbatim. That exercises the whole pipeline (chunking, verification, validators,
persistence, deadlines) without a network call. It says nothing about real-model quality.
"""

import re
from datetime import datetime
from typing import Callable, List, Optional, Set

from app.services.llm_service import LLMError

DATE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"


def iso(text: str) -> str:
    return datetime.strptime(text, "%B %d, %Y").date().isoformat()


def flat(text: str) -> str:
    return " ".join(text.split())


def _v(value=None, quote=None, conf=0.9):
    return {"value": value, "source_quote": quote, "confidence": conf}


class ContractReadingLLM:
    def __init__(self) -> None:
        self.calls: List[str] = []
        self.excerpts: List[str] = []
        self.fail_calls: Set[int] = set()              # 1-based extraction-call numbers that raise LLMError
        self.raise_always: Optional[Exception] = None
        self.transform_quote: Optional[Callable[[str], str]] = None
        self.answer_mode = "normal"                    # normal | refuse | hallucinate
        self.truncate_over: Optional[int] = None       # raise "truncated" for excerpts longer than this
        self.overview_mode = "normal"                  # normal | ungrounded (invents a number) | invented_name
        self.overview_prompts: List[str] = []
        self.impact_mode = "normal"                    # normal | ungrounded (invents a number) | partial
        self.impact_prompts: List[str] = []
        self.questions: List[str] = []                  # every question the "model" was asked
        self.last_prompt = ""                          # the last Q&A prompt (to assert what context it received)
        self.noisy_parties = False                     # also return role labels as parties, as the real model did
        self.effective_override: Optional[str] = None  # force a wrong effective date (to test cross-field checks)

    # ── entry point ──────────────────────────────────────────────────────────
    def __call__(self, system: str, user: str, name: str):
        self.calls.append(name)
        if self.raise_always is not None:
            raise self.raise_always
        if name == "contract_extraction":
            n = sum(1 for c in self.calls if c == "contract_extraction")
            if n in self.fail_calls:
                raise LLMError("bad_json", "simulated malformed reply")
            return self._extract(user)
        if name == "contract_answer":
            return self._answer(user)
        if name == "change_impacts":
            return self._impacts(user)
        if name == "summary_overview":
            return self._overview(user)
        raise AssertionError(f"unexpected schema name {name}")

    # ── extraction ───────────────────────────────────────────────────────────
    def _q(self, quote: Optional[str]) -> Optional[str]:
        if quote is not None and self.transform_quote:
            return self.transform_quote(quote)
        return quote

    def _extract(self, user: str):
        body = user.split("<contract_excerpt>", 1)[1].rsplit("</contract_excerpt>", 1)[0]
        if self.truncate_over is not None and len(body) > self.truncate_over:
            raise LLMError("truncated", "simulated output limit")
        self.excerpts.append(body)
        t = flat(body)
        out = {
            "parties": [], "effective_date": _v(), "expiration_date": _v(),
            "renewal": {"summary": None, "auto_renews": None, "notice_period_value": None,
                        "notice_period_unit": None, "source_quote": None, "confidence": 0.0},
            "payment_terms": {"summary": None, "source_quote": None, "confidence": 0.0},
            "termination": {"summary": None, "notice_period_value": None, "notice_period_unit": None,
                            "source_quote": None, "confidence": 0.0},
            "clauses": [], "obligations": [],
        }

        m = re.search(r'by and between (.+?), an? [^()]*\("Provider"\), and (.+?), an? [^()]*\("Customer"\)', t)
        if m:
            q = self._q(m.group(0))
            out["parties"] = [
                {"name": m.group(1), "role": "Provider", "source_quote": q, "confidence": 0.9},
                {"name": m.group(2), "role": "Customer", "source_quote": q, "confidence": 0.9},
            ]

        if self.noisy_parties:
            # What the live Groq run actually returned from later sections of the contract.
            out["parties"] += [
                {"name": "Provider", "role": "Provider", "source_quote": "Provider may use data derived from the Services", "confidence": 0.9},
                {"name": "the Customer", "role": "Customer", "source_quote": "Customer will comply with the licence terms", "confidence": 0.9},
                {"name": "Receiving Party", "role": "Recipient", "source_quote": "Each party will protect the other's Confidential Information", "confidence": 0.9},
            ]

        m = re.search(rf"entered into as of {DATE}", t)
        if m:
            out["effective_date"] = _v(self.effective_override or iso(m.group(1)), self._q(m.group(0)))
        m = re.search(rf"expires on {DATE}", t)
        if m:
            out["expiration_date"] = _v(iso(m.group(1)), self._q(m.group(0)))

        m = re.search(r"This Agreement renews automatically[^.]*\.", t)
        if m:
            n = re.search(r"at least \w+ \((\d+)\) days", m.group(0))
            out["renewal"] = {
                "summary": "Renews automatically for one-year terms unless notice of non-renewal is given.",
                "auto_renews": True,
                "notice_period_value": int(n.group(1)) if n else None,
                "notice_period_unit": "days" if n else None,
                "source_quote": self._q(m.group(0)), "confidence": 0.9,
            }

        m = re.search(r"Customer shall pay Provider a monthly fee of (\$[\d,]+)[^.]*\.", t)
        if m:
            out["payment_terms"] = {
                "summary": f"Monthly fee of {m.group(1)}, payable within 30 days of the invoice date.",
                "source_quote": self._q(m.group(0)), "confidence": 0.9,
            }
            out["clauses"].append({"clause_type": "PAYMENT", "title": "Fees and Payment",
                                   "source_quote": self._q(m.group(0)), "confidence": 0.9})

        m = re.search(r"Either party may terminate this Agreement for convenience by giving \w+ \((\d+)\) days written notice[^.]*\.", t)
        if m:
            out["termination"] = {
                "summary": f"Either party may terminate for convenience on {m.group(1)} days' written notice.",
                "notice_period_value": int(m.group(1)), "notice_period_unit": "days",
                "source_quote": self._q(m.group(0)), "confidence": 0.9,
            }
            out["clauses"].append({"clause_type": "TERMINATION", "title": "Termination for convenience",
                                   "source_quote": self._q(m.group(0)), "confidence": 0.9})

        m = re.search(rf"Customer shall deliver an annual security audit report to Provider by {DATE}\.", t)
        if m:
            out["obligations"].append({
                "responsible_party": "Customer", "action": "Deliver an annual security audit report to Provider",
                "due_rule": f"by {m.group(1)}", "due_date": iso(m.group(1)),
                "source_quote": self._q(m.group(0)), "confidence": 0.9,
            })
        m = re.search(r"Provider shall deliver a quarterly service performance report to Customer within fifteen \(15\) days after the end of each calendar quarter\.", t)
        if m:
            out["obligations"].append({
                "responsible_party": "Provider", "action": "Deliver a quarterly service performance report to Customer",
                "due_rule": "within 15 days after the end of each calendar quarter", "due_date": None,
                "source_quote": self._q(m.group(0)), "confidence": 0.9,
            })
        return out

    # ── summary overview ─────────────────────────────────────────────────────
    def _overview(self, user: str):
        """A two-sentence overview built only from the facts shown (or, in 'ungrounded' mode, with an invented figure)."""
        self.overview_prompts.append(user)
        names = re.findall(r"([A-Z][\w.&]*(?: [A-Z][\w.&]*)*) \((?:Provider|Customer)\)", user)
        eff = re.search(r"Effective (\d{1,2} \w{3} \d{4})", user)
        exp = re.search(r"Expires (\d{1,2} \w{3} \d{4})", user)
        who = " and ".join(names[:2]) if names else "the parties"
        text = f"An agreement between {who}"
        if eff:
            text += f", effective {eff.group(1)}"
        if exp:
            text += f" and expiring {exp.group(1)}"
        text += "."
        if self.overview_mode == "ungrounded":
            text += " It includes a 45% early-payment discount."
        if self.overview_mode == "invented_name":
            text += " It was negotiated by Zephyr Holdings."
        return {"overview": text}

    # ── version-change impact explanations ───────────────────────────────────
    def _impacts(self, user: str):
        """Explains each change using only what the prompt shows (or, in 'ungrounded' mode, invents a number)."""
        self.impact_prompts.append(user)
        out = []
        for m in re.finditer(r"\[(\d+)\] (.+?) \((\w+)\)\n  OLD: (.*)\n  NEW: (.*)", user):
            idx, label, kind, old, new = int(m.group(1)), m.group(2), m.group(3), m.group(4), m.group(5)
            if self.impact_mode == "ungrounded":
                text = f"{label} changes the outcome and could add a 45% cost."
            elif old.startswith("(absent)"):
                text = f"{label} is newly added: {new[:80]}."
            elif new.startswith("(absent)"):
                text = f"{label} was removed; it previously said {old[:80]}."
            else:
                text = f"{label} moved from {old[:60]} to {new[:60]}, so plan for the new terms."
            out.append({"id": idx, "impact": text})
        if self.impact_mode == "partial":
            out = out[:1]
        return {"impacts": out}

    # ── Q&A ──────────────────────────────────────────────────────────────────
    NOT_IN ={"response_type": "not_in_contract", "answer": "", "citations": []}

    def _answer(self, user: str):
        """Reads the same <facts> and <excerpts> the real model sees, and cites verbatim."""
        self.questions.append(user.rsplit("Question:", 1)[1].strip())
        facts = user.split("<facts>", 1)[1].split("</facts>", 1)[0] if "<facts>" in user else ""
        excerpts_block = user.split("<excerpts>", 1)[1].rsplit("</excerpts>", 1)[0]
        question = user.rsplit("Question:", 1)[1].strip().lower()
        self.last_prompt = user

        if self.answer_mode == "refuse":
            return dict(self.NOT_IN)
        if self.answer_mode == "hallucinate":
            return {"response_type": "answer", "answer": "Yes, with 5 days notice.",
                    "citations": [{"excerpt": 1, "quote": "Either party may terminate at any time on five (5) days notice."}]}
        if self.answer_mode == "paraphrase":
            # A citation that is NOT verbatim but clearly points at a real passage.
            return {"response_type": "answer",
                    "answer": "Either party may terminate for convenience with ninety (90) days written notice.",
                    "citations": [{"excerpt": 1, "quote": "either side can terminate for convenience giving ninety days written notice"}]}
        if self.answer_mode == "not_a_question":
            return {"response_type": "not_a_question", "answer": "Ask me anything about this contract.", "citations": []}

        parts = re.split(r"\n\n(?=\[\d+\] )", excerpts_block.strip())

        def cite_excerpt(pattern: str, answer: str):
            for part in parts:
                m = re.match(r"\[(\d+)\]", part)
                if not m:
                    continue
                hit = re.search(pattern, flat(part))
                if hit:
                    return {"response_type": "answer", "answer": answer,
                            "citations": [{"excerpt": int(m.group(1)), "quote": hit.group(0)}]}
            return None

        def cite_fact(label: str, answer: str):
            for line in facts.splitlines():
                if line.startswith(f"- {label}"):
                    q = re.search(r"passage: “(.+?)”", line)
                    if q:
                        return {"response_type": "answer", "answer": answer,
                                "citations": [{"excerpt": 0, "quote": q.group(1)}]}
            return None

        if re.search(r"terminat|cancel|early|exit", question):
            r = cite_excerpt(r"Either party may terminate this Agreement for convenience by giving[^.]*\.",
                             "Yes. Either party may terminate for convenience by giving ninety (90) days' written notice.")
            if r:
                return r
        if re.search(r"expire|end|until|last|how long", question):
            r = cite_excerpt(r"expires on [A-Z][a-z]+ \d{1,2}, \d{4}", "The initial term expires on December 31, 2028.")
            if r:
                return r
        if re.search(r"part(y|ies)|who is|who are|between", question):
            r = cite_fact("Party", "The parties are Northwind Analytics Inc. (Provider) and Contoso Retail LLC (Customer).")
            if r:
                return r
        if re.search(r"pay|fee|cost|price|owe", question):
            r = cite_fact("Payment terms", "The monthly fee is $12,500.")
            if r:
                return r
        if re.search(r"summar|about|overview", question):
            r = cite_fact("Effective date", "A services agreement between Northwind Analytics Inc. and Contoso Retail LLC effective 15 January 2026.")
            if r:
                return r
        return dict(self.NOT_IN)
