"""
run_eval.py: measure extraction quality against hand-labelled contracts (CUAD-style).

Usage (backend running, real LLM configured):

    python eval/run_eval.py                       # every eval/gold/*.json
    python eval/run_eval.py eval/gold/foo.json    # one file
    python eval/run_eval.py --keep                # keep the uploaded contracts (default: delete them)
    python eval/run_eval.py --out eval/results.md # write the table to a file

It uploads each PDF through the real API, waits for processing, then scores the answer for every
key field against the labels. It reports what matters for a tool whose promise is "never invent":

  recall             of the fields that ARE in the contract, how many were found and correct
  precision          of the fields it filled in, how many were correct
  wrong_verified     fields marked "verified" whose value was wrong (the number that must be ~0)
  false_positives    fields filled in although the contract does not contain them (hallucinations)
  verified_rate      share of found fields whose quote was confirmed in the document

Gold file format (JSON):
  {
    "name": "...", "pdf": "relative/or/absolute/path.pdf",
    "fields": {                      # exact scalar value, or null = "not in the contract"
       "effective_date": "2026-01-15", "auto_renew": true, "termination_notice_period": 90, ...
    },
    "contains": {                    # free-text fields: substrings (case-insensitive) that must all appear
       "payment_terms": ["$12,500", "thirty (30) days"]
    },
    "parties": ["Northwind Analytics Inc.", "Contoso Retail LLC"]
  }

Nothing here reads or prints API keys; the script talks only to the local API.
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

ROOT = Path(__file__).resolve().parent
DEFAULT_BASE = "http://127.0.0.1:8001/api"
SCALAR_KEYS = ("date", "bool", "days")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9$%.]", "", str(s).lower())


def scalar_of(field: Dict[str, Any]) -> Any:
    """The comparable scalar of an extracted field value ({'date': ..} / {'bool': ..} / {'days': ..})."""
    v = field.get("value")
    if isinstance(v, dict):
        for k in SCALAR_KEYS:
            if k in v:
                return v[k]
    return None


def text_of(field: Dict[str, Any]) -> str:
    v = field.get("value")
    if isinstance(v, dict) and "text" in v:
        return str(v["text"])
    return field.get("display_value") or ""


def wait_ready(c: httpx.Client, cid: str, timeout: float = 600) -> Dict[str, Any]:
    end = time.time() + timeout
    while time.time() < end:
        s = c.get(f"/contracts/{cid}/status").json()
        if s["status"] in ("READY", "FAILED", "UNSUPPORTED") or s.get("progress") == 100:
            return s
        time.sleep(4)
    raise TimeoutError(f"contract {cid} did not finish within {timeout:.0f}s")


class Tally:
    def __init__(self) -> None:
        self.gold_present = self.found_correct = self.filled = 0
        self.wrong_verified = self.false_pos = self.verified = 0
        self.party_gold = self.party_found = self.party_extracted = 0
        self.details: List[str] = []

    def rows(self) -> Dict[str, Any]:
        p = self.found_correct / self.filled if self.filled else None
        r = self.found_correct / self.gold_present if self.gold_present else None
        vr = self.verified / self.filled if self.filled else None
        pp = self.party_found / self.party_extracted if self.party_extracted else None
        pr = self.party_found / self.party_gold if self.party_gold else None
        return dict(precision=p, recall=r, verified_rate=vr, wrong_verified=self.wrong_verified,
                    false_positives=self.false_pos, party_precision=pp, party_recall=pr)


def score(gold: Dict[str, Any], detail: Dict[str, Any], t: Tally) -> None:
    fields = {f["field_key"]: f for f in detail["fields"]}
    name = gold.get("name", "?")

    def record(key: str, expected: Any, field: Optional[Dict[str, Any]], ok: bool) -> None:
        found = field is not None and field["status"] in ("verified", "needs_review")
        if expected is None:                              # the contract does not contain it
            if found:
                t.filled += 1
                t.false_pos += 1
                t.details.append(f"  FALSE POSITIVE {name}:{key} -> {field['display_value']!r} [{field['status']}]")
            return
        t.gold_present += 1
        if found:
            t.filled += 1
            t.verified += field["status"] == "verified"
            if ok:
                t.found_correct += 1
            else:
                t.wrong_verified += field["status"] == "verified"
                t.details.append(f"  WRONG {name}:{key} expected {expected!r} got {field['display_value']!r} [{field['status']}]")
        else:
            t.details.append(f"  MISSED {name}:{key} expected {expected!r} (status {field['status'] if field else 'absent'})")

    for key, expected in gold.get("fields", {}).items():
        f = fields.get(key)
        got = scalar_of(f) if f else None
        record(key, expected, f, expected is not None and str(got) == str(expected))
    for key, needles in gold.get("contains", {}).items():
        f = fields.get(key)
        blob = norm(text_of(f)) if f else ""
        record(key, needles, f, all(norm(n) in blob for n in needles))

    if "parties" in gold:
        got = [norm(p["name"]) for p in detail.get("parties", []) if p["status"] in ("verified", "needs_review")]
        want = [norm(n) for n in gold["parties"]]
        t.party_gold += len(want)
        t.party_extracted += len(got)
        t.party_found += sum(1 for w in want if w in got)
        for w in gold["parties"]:
            if norm(w) not in got:
                t.details.append(f"  MISSED {name}:party {w!r}")
        for g, p in zip(got, [p["name"] for p in detail.get("parties", [])]):
            if g not in want:
                t.details.append(f"  EXTRA {name}:party {p!r}")


def pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gold", nargs="*", help="gold JSON files (default: eval/gold/*.json)")
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--keep", action="store_true", help="keep the uploaded contracts")
    ap.add_argument("--out", help="write the markdown report here")
    args = ap.parse_args()

    files = [Path(g) for g in args.gold] or sorted((ROOT / "gold").glob("*.json"))
    if not files:
        print("No gold files found.")
        return 2

    c = httpx.Client(base_url=args.base, timeout=180)
    try:
        st = c.get("/system/status").json()
    except httpx.HTTPError:
        print(f"The API is not reachable at {args.base}. Start the backend first.")
        return 2
    if not st["llm_configured"]:
        print("No LLM is configured, so there is nothing to evaluate. Set LLM_PROVIDER and its key in backend/.env.")
        return 2
    print(f"Evaluating with provider={st['llm_provider']} model={st['llm_model']}"
          f"{' (privacy redaction on)' if st.get('privacy_redaction') else ''}\n")

    total, per_doc = Tally(), []
    created: List[str] = []
    for gf in files:
        gold = json.loads(gf.read_text(encoding="utf-8"))
        pdf = (gf.parent / gold["pdf"]).resolve()
        with pdf.open("rb") as fh:
            r = c.post("/contracts/upload", files={"file": (pdf.name, fh, "application/pdf")})
        r.raise_for_status()
        cid = r.json()["id"]
        created.append(cid)
        t0 = time.time()
        s = wait_ready(c, cid)
        detail = c.get(f"/contracts/{cid}").json()
        one = Tally()
        score(gold, detail, one)
        score(gold, detail, total)
        per_doc.append((gold.get("name", gf.stem), one, time.time() - t0, s))
        print(f"{gold.get('name', gf.stem)}: {s['status']}, extraction {s.get('extraction_status')} ({time.time() - t0:.0f}s)")
        for line in one.details:
            print(line)

    lines = ["| Document | Recall | Precision | Verified | Wrong-but-verified | False positives | Party P/R |",
             "|---|---|---|---|---|---|---|"]
    for name, tl, _, _ in per_doc:
        r = tl.rows()
        lines.append(f"| {name} | {pct(r['recall'])} | {pct(r['precision'])} | {pct(r['verified_rate'])} | "
                     f"{r['wrong_verified']} | {r['false_positives']} | {pct(r['party_precision'])}/{pct(r['party_recall'])} |")
    r = total.rows()
    lines.append(f"| **All** | **{pct(r['recall'])}** | **{pct(r['precision'])}** | **{pct(r['verified_rate'])}** | "
                 f"**{r['wrong_verified']}** | **{r['false_positives']}** | **{pct(r['party_precision'])}/{pct(r['party_recall'])}** |")
    report = "\n".join(lines)
    print("\n" + report)
    if args.out:
        header = f"Model: {st['llm_provider']} / {st['llm_model']}. Labelled documents: {len(per_doc)}.\n\n"
        Path(args.out).write_text(header + report + "\n", encoding="utf-8")
        print(f"\nWrote {args.out}")

    if not args.keep:
        for cid in created:
            c.delete(f"/contracts/{cid}", params={"permanent": "true"})
    return 0 if r["wrong_verified"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
