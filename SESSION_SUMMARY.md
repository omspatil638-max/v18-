# ContractLens upgrade: session checkpoint

_Last updated 2026-09-20. Ten phases + repeat alerts + the "improve and extend" batch below. Work continued on branch `contractlens-product-upgrade` (another Claude session created it and keeps UNCOMMITTED edits staged in this worktree: see "Coordination")._

## Goal
Upgrade ContractLens (FastAPI + Next.js 16 + Postgres/pgvector) into an AI-assisted contract review and obligation-tracking tool that meets the 11-point problem statement. **Differentiator: every output is verifiable (click-to-source, quote-exists check) and honest about uncertainty.** It reduces manual review; it is NOT legal advice, and the UI and generated summaries say so.

## Hard rules (from the brief)
- Never invent data. Failures give `extraction_unavailable`; absent info gives `not_found`. No hardcoded fallback text or fake confidence.
- Shared item shape: value, source_quote, page, section, confidence 0-1, status (verified | needs_review | not_found | extraction_unavailable), contract_version_id. `verified` only if the quote is found in the document text.
- Dates, notice periods and amounts are computed or validated in code, not trusted from the model.
- Process the FULL document. Model and provider come from env vars.
- Security: sanitized filenames, size cap, real `%PDF-` check, ownership checks. Long work runs as a background job.
- No e-signature, drafting, Word add-ins or CRM. Do not delete working features. Commit per phase.

## Decisions (all delegated to Claude by the user)
- Only free, safe, no-training tools. The user creates their own API keys.
- LLM: Groq (`LLM_PROVIDER=groq`, default `openai/gpt-oss-120b`, strict json_schema), paced client-side to the free tier (30 RPM, 8K TPM, 200K TPD). Ollama via `openai_compatible` is the zero-egress option; the user will set it up later.
- Embeddings: local fastembed `BAAI/bge-small-en-v1.5` (384-d) + pgvector HNSW.
- Auth: own scrypt + DB sessions; `AUTH_MODE=demo|login`; demo mode is refused in production.
- **API key rule (memory `feedback_never_expose_api_keys.md`): never read, print, commit or expose the user's key.** Inspect `backend/.env` only with values masked. Keys are `SecretStr`, and `scrub()` redacts credentials from every user-facing message.

## Environment
- Repo: `D:\Contract\ContractLens.worktrees\run-files-execution-guide` (git worktree). Backend venv: `backend\.venv`. Windows 11, PowerShell + Git Bash.
- DB: docker container `contractlens-db` (pgvector pg16) on 127.0.0.1:5432. Databases `contractlens` (real, migrated to 007) and `contractlens_test`.
- Ports: backend 8001, frontend 3000 (loopback only). Launcher: `Start-ContractLens.cmd` -> `start-contractlens.ps1` (works from a cold start).
- Tests: `cd backend; .venv\Scripts\python -m pytest` (about 600 passing; uses `contractlens_test`).
- Evaluation: `python backend/eval/run_eval.py` (real model, running backend).
- Setup helpers (repo root): `Setup-Email.cmd` (hidden-input SMTP setup, verifies login, sends a test mail, then saves to `backend/.env`), `Setup-Notifications.cmd` (VAPID keys; the launcher also runs it), `Install-Autostart.cmd` / `Install-Autostart.ps1 -Remove` (quiet start at Windows sign-in; NOT installed, it changes the user's system).

## Phases (all committed)
1. Foundation and honesty: status model, quote-exists verification, computed confidence, secrets hygiene, login.
2. Unified data model: Contract -> Version -> Document, plus per-version fields, parties, clauses, obligations, deadlines, chunks, flags (migrations 002-003).
3. Structured extraction: map-reduce over section-aware chunks, code validators, ellipsis-quote verification, chat fixes (synonyms, small talk, NUL handling).
4. Review flags: rule-based queue with resolve/dismiss, dashboard and sidebar counts.
5. Obligations, timeline, renewals, alerts: calendar-aware schedule math, recurring occurrences, `.ics`, in-app alerts, optional SMTP digest.
6. Version comparison: section matching, key-term diff in code, grounded impact sentences, auto-compare on new version.
7. Q&A and summary: hybrid retrieval (semantic + FTS + synonyms + headings, RRF), summary built from verified facts with an optional grounded AI overview.
8. Source viewer: server-rendered page PNG plus normalised highlight rectangles; PDF/Text view toggle.
9. Polish: responsive drawer shell, accessible confirm dialog with undo toast (soft delete, permanent option), dashboard search/filter/sort, dead-code cleanup.
10. Privacy mode (`PRIVACY_MODE=redact`, reversible identifier placeholders) and the extraction eval harness with gold files.

## Alerts that repeat until read (added after Phase 10, commit 51bf21e)
- Channels: email (SMTP) and browser push (Web Push, VAPID keys in `backend/.env`, `pywebpush`). Both optional and honest when unconfigured.
- `alert_service.run_alert_cycle(today, now)`: first notification at once, then a reminder every `repeat_hours` (default 24, user setting 1-168) while the alert is unread, at most `ALERT_MAX_REPEATS` (default 5) reminders per channel; repeat can be switched off. Per-alert counters `email_count`/`push_count` and timestamps. Scheduler interval `ALERT_CHECK_INTERVAL_MIN` = 30. Skips deleted contracts, non-current versions and completed obligations.
- "Read" = acknowledged: in-app button, `POST /alerts/acknowledge-all`, or the one-click link in each email (`{APP_BASE_URL}/ack/{token}`; per-user unguessable token in `alert_settings.ack_token`; GET only reads, POST acknowledges; public page `/ack/[token]`).
- Push: `/push/public-key|subscribe|unsubscribe|test`, dead subscriptions (404/410) are deleted, https endpoints only, owner-only. Frontend: `public/sw.js`, `lib/notifications.ts`, in-page fallback `lib/useInPageAlerts.ts`, Reminder settings dialog sections for repeats and browser notifications.
- Migration `005_alert_repeats_and_push` (alerts counters, alert_settings repeat/push/ack_token, `push_subscriptions`).
- Verified: a real SMTP socket end to end (first mail, none at 2 h, "reminder 1" at 25 h, working read link); a real subscription in headless Edge and a real push accepted by Windows' push service (`wns2-*.notify.windows.com`); test data cleaned up afterwards.
- Not verified: a notification actually appearing on screen (headless Edge cannot show one), delivery to a real mailbox (no SMTP credentials yet), the ack page with pending alerts in a browser.
- Limits: the backend must be running to send anything; email links point at `APP_BASE_URL` (default `http://localhost:3000`).

## Improve-and-extend batch (this session, after the alerts work)
Order followed the plan in chat ("what I'd do first"). Everything is computed in code and source-linked; no single risk score by design.
- **Review a value** (`services/review_service.py`, `POST /contracts/{id}/fields/{fid}/review`): confirm / correct / not_in_contract / revert. Validated in code, optional quote must exist AND contain the value (then `verified`, page/section derived), else stays `needs_review`. Original kept in `notes.user_review`; deadlines/alerts/flags rebuilt (alert read/emailed state preserved by (label,date,lead)); survives reprocessing (capture/reapply in job_runner); reviewed fields stop raising low-confidence flags. UI: `FieldReviewDialog`.
- **Data-quality centre** (`quality_service`, `GET /data-quality`, page `/data-quality`, sidebar badge): coverage + to-do issues; reviewed-absent values are resolved.
- **Renewal radar** (`renewal_service`, `GET /renewals`, page `/renewals`) and **month calendar** (`/calendar`, uses `/deadlines`): action date = notice deadline for auto-renewing contracts; amounts quoted as written, never summed.
- **Ctrl+K search** (`search_service`, `GET /search`, `CommandPalette`), **tags + contract type** (migration 006, `PATCH /contracts/{id}`, `/tags`, `/contract-types`, library page `/contracts` with upload panel).
- **Ask across contracts** (`portfolio_service`, `POST /portfolio/ask`, page `/ask`): rules-first question -> Filter -> run in code; a model may only translate odd wording into the filter (sees no contract text, output validated); non-filter questions return real passages labelled as text search.
- **Scanned PDFs (OCR)** (`ocr_service`, RapidOCR local): image-only pages are OCR'd (render scale 3.0; 2.0 misread "$12,000" as "s$12,o00"); anything read from OCR pages is demoted from `verified` to `needs_review` with `notes.ocr`; `SCANNED_PAGES` flag; `extraction_meta.source`. Config: `OCR_ENABLED`, `OCR_MAX_PAGES` (40), `OCR_RENDER_SCALE`, `OCR_LOW_CONFIDENCE`. About 20 s per page on CPU.
- **Word (.docx)** (`docx_service`): content-sniffed, zip-bomb/macro checks, converted to PDF via PyMuPDF Story (text + tables), original kept (`<stem>.original.docx`, `/file?original=true`, removed on permanent delete), `CONVERTED_DOCUMENT` flag.
- **Your own rules** (`policy_service`, migration 007, `/policy-rules`, page `/rules`): numeric/bool/missing checks over known values -> `POLICY_RULE` flags naming the rule; unknown values never fire; any rule change re-checks all analysed contracts and keeps decided flags.
- New requirements: `python-docx`, `rapidocr-onnxruntime`.
- Verified live with the real model: OCR of a scan of the sample read all 8 fields correctly and held every one at `needs_review`; the Word copy verified normally; rules on the real DB created flags and left the counts unchanged after removal; UI (Ctrl+K, review dialog change+revert, Ask, radar, calendar, data quality, rules) driven in headless Edge. Test uploads were permanently deleted.
- **Real findings on the user's own data** (worth telling them): "Northstar Technologies Software Services Agreement" expires 2026-09-30, auto-renews, and NO renewal notice period was found; its obligation "written non-renewal notice no later than September 23, 2026" was due within days of 2026-09-20.

## Coordination (important)
- Another session works on branch `contractlens-product-upgrade` and holds STAGED, uncommitted edits to: `PRODUCT_REVIEW_2026-09-20.md`, `frontend/src/app/page.tsx` (dashboard), `contracts/[id]/{chat,compare,obligations,source,summary,timeline}/page.tsx`, `components/ui/Badges.tsx`. I did not touch or commit them (commit with `git commit -- <explicit paths>`).
- Known consequence: the DASHBOARD upload still says "PDF only" (its file is off limits); upload of .docx works from `/contracts` (library) and the version dialog. Fix `app/page.tsx` once the other session commits.
- Its review recommends: no single risk score (followed), OCR only with an explicit boundary (followed), durable job queue, roles/tenancy (NOT done), decision/assignment records (NOT done).

## Not done from the plan (remaining)
- Welcome/onboarding (name, email confirmation code, notification permission), morning briefing with preview/pause/quiet hours/catch-up, Snooze/Done in email (from the notifications plan).
- Menu restructure to Home/Contracts/Needs review/Deadlines/Obligations, Home slim-down, 4-tab contract page, Source as side panel.
- Side-by-side redline + PDF export of comparison; plain-language summary mode + one-page PDF; source viewer thumbnails/notes; saved views, bulk actions, dark mode; batch upload, watched folder, CSV/Excel export + evidence pack; activity log; notes/tasks per contract; amendments "effective terms"; counterparty page; backup/restore + app lock; correction-driven accuracy tests; job visibility/retry UI.

## Verified live (real Groq model, not the fake LLM)
- Demo scenario on `northwind_msa_v1.pdf` / `v2.pdf`: both versions extracted, exactly three key changes found (notice 30 -> 60 days, auto-renewal No -> Yes, fee $10,000 -> $12,000), alerts and `.ics` generated, chat answers cite a source whose highlight lands on the right text, an unanswerable question returns "Not found", summary lines all link to sources.
- Eval on three synthetic contracts: 100% recall and precision, 0 wrong-but-verified, 0 false positives. These are clean synthetic documents; this is a smoke test, not a claim about real-world accuracy.
- UI checked in headless Edge: desktop pages, PDF highlight, summary, 375px overflow on 11 pages (none), drawer, dialog Escape, delete and undo.
- Secrets scan of live endpoints, the source tree and git history: no key material.

## Known limits (be honest about these)
- `fake_llm.py` is a deterministic stand-in used ONLY in tests (needs `APP_ENV=test`). It exercises chunking, verification, validators, persistence, flags and alerts without network or cost. It says nothing about real-model quality; that was checked manually with Groq and by `eval/run_eval.py`.
- Confidence is computed from checks and is not calibrated.
- Privacy mode redacts identifiers (emails, phones, IBANs, card/SSN/tax ids, URLs) but not names, dates or amounts, because extraction needs them. For full privacy use Ollama.
- The optional AI overview and impact sentences depend on the model being reachable and on the free-tier daily quota; without them the code-built content still shows.
- Not tested: real-mailbox delivery of the new features, OCR on real photographed/skewed scans (only a clean synthetic scan), Word files with images/text boxes/tracked changes beyond the noted limits, delivery to a real mailbox (SMTP was tested against a local mail server only), browsers other than Edge, print output on paper.
- The real database also holds a few smoke-test uploads from development.

## Follow-ups for the user
- Rotate the Groq key: it was once rendered in a page during an early bug (fixed, with regression tests).
- Optionally label some of your own contracts under `backend/eval/gold/` to measure accuracy on real documents.
- Set up Ollama when ready (see README).
- Run `Setup-Email.cmd` (Gmail needs an App Password), then turn on email and browser notifications in Reminder settings.
- Optionally run `Install-Autostart.cmd` so alerts keep arriving after sign-in.

## Lessons
- Never patch Python source through shell heredocs with backslash escapes: `\b` and `\x00` were silently turned into control bytes. Use the Edit/Write tools and scan for control bytes.
- Never interpolate config values into user-facing messages.
- Verify against the real model, not only the fake one: real output abbreviates quotes with ellipses, which the fake never did.
- Pass explicit paths to `git add`; the tree may hold the user's own staged edits.
- Tests that fake "today" must move every layer together (cycle and API); one layer on real time gave a misleading failure.
- A link in an email must never mutate on GET: mail scanners prefetch links. Use GET to read and POST to act.
