# ContractLens

**Privacy-conscious AI contract intelligence platform**

> Convert business contracts into structured metadata, actionable obligations, timelines, and grounded Q&A with source citations.

---

## 🛠️ Quick Start

### One-click start on Windows

Double-click `Start-ContractLens.cmd` in the repository folder. It will create the local Python environment, install dependencies, use your local PostgreSQL service, apply migrations, start both application servers, and open `http://localhost:3000`.

PostgreSQL 16 must be installed and running, with a `contractlens` database and user/password `contractlens`. The first launch can take several minutes while dependencies are installed. The generated `backend/.env` uses local fallback providers, so API keys are optional for the initial startup.

You can also run the same launcher from VS Code using **Terminal → Run Task → Start ContractLens** or the Agents **Run** button. Docker remains available with `.\start-contractlens.ps1 -DatabaseMode docker`, and `auto` can use Docker when it is already running.

To create the local database after installing PostgreSQL, open `psql` as an administrator and run:

```sql
CREATE USER contractlens WITH PASSWORD 'contractlens';
CREATE DATABASE contractlens OWNER contractlens;
```

Ensure PostgreSQL's `bin` directory is on `PATH` so `psql` and `pg_isready` are available, then double-click `Start-ContractLens.cmd`.

### Manual start

```powershell
# 1. Start PostgreSQL
docker compose up -d

# 2. Backend (FastAPI on port 8001)
cd backend
.\.venv\Scripts\python -m alembic upgrade head
.\.venv\Scripts\python -m uvicorn app.main:app --port 8001 --host 127.0.0.1

# 3. Frontend (Next.js on port 3000)
cd frontend
npm run dev

# Open http://localhost:3000 in your browser
```

---

## 🌟 What it does (and what it will not pretend to do)

ContractLens is an **AI-assisted** contract review tool. It reduces manual review effort; it is **not legal advice** and does not replace a qualified legal professional. The UI says so on every page.

Its differentiator is that **every output is verifiable and honest about uncertainty**:

- 📄 **Upload** PDFs **and Word (.docx) files** (max `MAX_UPLOAD_MB`, the *content* must match the file type, sanitized names, macro-enabled and zip-bomb Word files refused). Processing runs as a background job with a live progress bar.
- 🖨️ **Scanned contracts are read with free, local OCR** (RapidOCR, no cloud, nothing to install beyond `pip`). OCR can misread digits and names (it once read `$12,000` as `s$12,o00`), so **nothing read from a scanned page is ever marked "verified"**: it stays "needs review" with a warning until you check it against the page image and confirm it. Set `OCR_ENABLED=false` to turn it off; `OCR_MAX_PAGES` (default 40) bounds the work, and pages beyond it are reported as an incomplete analysis. Scans take a while (roughly 20 seconds a page on a laptop CPU).
- 📝 **Word files** are converted to a PDF (text and tables) so pages and highlights work; the original is kept and downloadable. Images, text boxes and headers are not converted, and the review queue says so. Page numbers refer to the converted copy.
- 🔎 **Full-document extraction** (map-reduce over section-aware chunks): parties, effective/expiration dates, renewal terms + auto-renew flag + notice period, payment terms, termination conditions + notice period, clauses and obligations. No 12,000-character truncation.
- ✅ **Quote-exists check**: an item is `verified` only if its `source_quote` is found in the document text (whitespace/case/quote/hyphenation-insensitive). Otherwise it is `needs_review` with low confidence. The **page and section are derived in code from where the quote matched**, never taken from the model.
- 🧮 **Code-side validators**: dates must parse (ambiguous `03/04/2026` is rejected, not guessed) and appear in the quote; notice periods must be numbers found in the quote; amounts in summaries must appear in the source.
- 🚫 **No fabricated fallbacks**: if there is no LLM, the quota is exhausted or a call fails, fields show `extraction_unavailable`; if the document does not say it, `not_found`. Confidence is computed from checks (not a model's self-report) and is labelled *not calibrated*.
- 💬 **Grounded Q&A** with **hybrid retrieval**: semantic search (local `BAAI/bge-small-en-v1.5` embeddings in pgvector) fused with Postgres full-text search, a synonym table and section-heading matching, so "When does this deal lapse?" finds the *Term* clause. The answer must cite a passage that is verified to exist in the contract, otherwise the answer is **"Not found in this contract."** With no LLM, chat shows labelled search results instead of an answer. Embeddings run locally (about 67 MB downloaded once); with `EMBEDDING_PROVIDER=none` it falls back to keyword search.
- 🚩 **Human-review queue**: vague wording ("reasonable efforts", "promptly"), one-sided liability, missing or unusual termination terms, unverified or low-confidence fields and risky amendments become flags with severity, source and a resolve/dismiss workflow. Rules run in code; the model is not asked to grade risk.
- 📅 **Obligations, deadlines and renewals**: obligations per party with fixed, relative ("30 days after the Effective Date") and recurring due rules. Dates are computed in code with calendar-aware month arithmetic. The renewal-notice deadline is the expiry minus the notice period. The dashboard, timeline and alerts show what is due next, and every contract exports to `.ics` (RFC 5545, with reminders).
- 🔔 **Alerts that keep nagging until you read them**: in-app alerts at configurable lead times (30/14/7 days by default), plus **email** and **browser notifications**. If you have not marked an alert as read, it is sent again every 24 hours (configurable, at most 5 reminders), and stops the moment you mark it read: in the app, or with the one-click link in every email (no login needed). See "Setting up alerts" below.
- 🔀 **Version comparison**: upload a new version under the same contract. Sections are matched by heading, then similarity; key terms (dates, notice periods, amounts, auto-renewal) are diffed **in code**; the optional AI "why it matters" sentence is shown only if every number in it appears in the quoted text. Each change links to both versions' sources.
- 📝 **Stakeholder summary**: a one-page summary built in code from *verified* data (parties, term, money, key dates, top obligations, review items), each line linked to its source. It downloads as Markdown and prints cleanly. An optional AI-written overview is added only when it is grounded in those facts, and it is stamped "AI-assisted, not legal advice".
- 🔍 **Click-to-source PDF viewer**: every source link opens the original PDF at the right page with the cited sentence highlighted. If the text cannot be located on the page it says so instead of guessing. A plain text view is one click away.
- 🔐 **Per-user isolation** (`AUTH_MODE=login`): every contract route enforces ownership (other users get `404`).

- ✏️ **Review and correct any value**: confirm it, change it, or say the contract does not state it. Input is validated in code (an ambiguous date like `03/04/2027` is refused). If you give a quote it must exist in the document and contain the value, and then the value becomes *verified*; without one it stays *needs review*, because nothing in the document has been shown to say it. The original AI value is kept (Revert), and deadlines, alerts and flags are rebuilt. Corrections survive re-processing.
- 🩺 **Data-quality centre**: what is missing, unverified or unreadable across all contracts, with coverage bars ("expiry date: 12 of 15") and a to-do list.
- 🔁 **Renewal radar and calendar**: what ends or renews soon and *by when you must act* (the notice deadline for auto-renewing contracts), the sentence explaining what happens is built from the values, amounts are quoted as written and never added up, and a month calendar shows every deadline.
- 🔎 **Ctrl+K search** across contracts, extracted values and contract text (passages open the source with the text highlighted), plus **tags and contract types**.
- 💬 **Ask across all contracts** ("which contracts expire this quarter and auto-renew?", "notice period longer than 60 days?"): the question becomes a filter that runs *in code*; results are rows of real values with status and source. A model may only translate unusual wording into a filter and never sees contract text. Unknown values never count as matches. Questions that are about text ("which mention arbitration?") return real passages, labelled as a text search, not an answer.
- 📏 **Your own rules** ("flag payment terms over 45 days", "flag any auto-renewal", "flag a missing renewal notice period"): checked in code against known values and shown as ordinary review flags that name the rule and the value. An unknown value never fires a rule.

### Not included (by design)
E-signature, contract drafting, Word add-ins and CRM integrations are out of scope. The tool reads, checks and tracks contracts; it does not write them or sign them. There is deliberately **no single "risk score"**: a number would hide the difference between a verified fact, a rule flag, an incomplete analysis and a human judgement. Each contract shows its open flags by severity and its unverified values instead.

---

## 🤖 Turning on AI extraction (free, private-by-contract)

Out of the box `LLM_PROVIDER=none`: the app runs, stores and searches documents, and shows honest *unavailable* states.

**Recommended free option: Groq** (no credit card). Groq's Services Agreement states it is *"not permitted to use Inputs or Outputs for training or fine-tuning"*, it retains nothing by default, and offers a Zero Data Retention switch in its console.

1. Create a key at <https://console.groq.com/keys>.
2. In `backend/.env` set:
   ```
   LLM_PROVIDER=groq
   GROQ_API_KEY=gsk_...your key...
   ```
   (`LLM_MODEL` is optional; the default is `openai/gpt-oss-120b`. `openai/gpt-oss-20b` is faster and uses less quota.)
3. Restart the backend, open a contract and click **Re-run extraction**.

**Free-tier limits to expect:** ~30 requests/min, 8K tokens/min, 200K tokens/day on Groq's free plan. ContractLens paces itself automatically, so a 20-page contract takes a few minutes and roughly 8-10 contracts/day fit in the daily quota. If the daily quota runs out you will see *"rate/quota limit was reached"* rather than made-up results.

**Fully offline alternative (contract text never leaves your machine):** run [Ollama](https://ollama.com), then
```
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:14b
```
Quality depends on the local model and your hardware.

### Setting up alerts (email + notifications)
1. **Email (2 minutes, free):** double-click **`Setup-Email.cmd`**. For Gmail, turn on 2-Step Verification and create an *App Password* at <https://myaccount.google.com/apppasswords> (your normal password will not work). Paste it when asked; the input is hidden and it is saved only in `backend/.env`. The script logs in once, sends you a test email, and only then saves. Restart ContractLens, open **Reminder settings**, enter your address and turn on "Email me due alerts".
2. **Browser notifications:** nothing to install. `Start-ContractLens.cmd` creates the (free, self-hosted) Web Push keys automatically. Open **Reminder settings**, turn on "Send me browser notifications" and allow the browser prompt. They arrive even when the ContractLens tab is closed, as long as the browser is running. Works on `localhost` and `https`.
3. **Repeats:** in the same dialog choose how often to be reminded (6-72 hours) or switch repeats off. Reminders stop when you mark the alert read.
4. **Keep it running:** alerts are sent by the backend, so it must be running. `Install-Autostart.cmd` starts ContractLens quietly when you sign in to Windows (undo: `Install-Autostart.ps1 -Remove`). Docker Desktop must start at sign-in too. If your computer is off, nothing can be sent; for always-on delivery, host ContractLens on a small always-on server and set `APP_BASE_URL` to its address.

Knobs in `backend/.env`: `ALERT_MAX_REPEATS` (default 5), `ALERT_CHECK_INTERVAL_MIN` (default 30), `APP_BASE_URL` (default `http://localhost:3000`, used for links in emails).

### Privacy mode
Set `PRIVACY_MODE=redact` in `backend/.env` and email addresses, phone numbers, IBANs, card / SSN / tax ids and URLs are swapped for placeholders (`[EMAIL_1]`) before any text is sent to a hosted model, then restored in the reply, so quotes still verify against the original. **Party names, dates and amounts are not redacted**: extraction needs them. If those must not leave your machine either, use the Ollama option above; that is the real answer, and a regex cannot replace it. The system status endpoint reports whether redaction is on.

### Measuring quality
`backend/eval/run_eval.py` scores extraction against hand-labelled contracts and reports recall, precision, the verified rate, **verified-but-wrong** (must be zero) and **false positives** (fields filled in that the contract does not contain). Add your own labelled PDFs as `backend/eval/gold/*.json` (format in the script's docstring) and run `python eval/run_eval.py`. The three bundled gold files are *synthetic, clean* contracts, so a perfect score there (see `backend/eval/results.md`) shows the harness and pipeline work, **not** that real-world contracts will score 100%. To benchmark seriously, label a sample of real or public (e.g. CUAD) contracts.

### Accounts
Default `AUTH_MODE=demo` shares one unauthenticated user for local trials. For any shared or hosted use set `AUTH_MODE=login` (email + password, scrypt-hashed, cookie sessions); the server refuses to start in `APP_ENV=production` with demo mode.

---

## 🧪 Tests

```powershell
docker exec contractlens-db psql -U contractlens -d postgres -c "CREATE DATABASE contractlens_test"   # once
cd backend
.\.venv\Scripts\python -m pytest
```
Tests use a separate `contractlens_test` database and a deterministic fake LLM (they validate the pipeline, verification and security behaviour, **not** real-model accuracy).
