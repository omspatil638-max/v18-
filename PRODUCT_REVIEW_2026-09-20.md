# ContractLens product review and upgrade recommendation

**Review date:** 2026-09-20  
**Review branch:** `contractlens-product-upgrade`  
**Audited checkout:** `ContractLens.worktrees/run-files-execution-guide`  
**Baseline:** `0261a22 Update session checkpoint: repeat alerts, push, setup scripts`  
**Scope:** repository inspection and current public-market research only. No product code was changed.

## Method and evidence boundaries

This is a repository review, not a claim that every documented behavior has been exercised in this review. “Implemented” below means there is a concrete route, service, model, UI, migration, or test in the audited checkout. “Validated” means a test or checked-in evaluation result exists; it does **not** mean production-scale or real-world accuracy has been proven.

The supplied brief did not identify a separate original problem-statement file. The primary product requirement is therefore the repository README’s stated purpose: convert contracts into structured metadata, actionable obligations, timelines, and grounded Q&A with source citations. `README.md` explicitly frames ContractLens as a post-signature review/intelligence product, rather than a drafting, signing, or CLM replacement.

External observations use vendor-published material and describe vendors’ stated capabilities, not independent efficacy comparisons.

## A. What ContractLens is today

ContractLens is a privacy-conscious, PDF-first, post-signature contract-intelligence application for a single user or a small self-hosted deployment. It uploads one contract at a time, extracts a deliberately bounded set of commercial terms, verifies model-produced quotes against document text, turns supported date rules into a schedule, presents a human-review queue, supports source-cited contract Q&A, compares versions, and sends renewal/obligation alerts.

The most distinctive product decision is epistemic: a field is not silently “filled” when unsupported. The implementation distinguishes `verified`, `needs_review`, `not_found`, and `extraction_unavailable`; code validates dates, amounts, and notice periods, and derives page/section from the matched source rather than trusting the model. That is stronger product behavior than a generic AI summary screen.

## B. Current product map

| Area | Current capability | User problem solved | Evidence in code | User visibility |
|---|---|---|---|---|
| Upload and processing | PDF-only upload with content/header validation, sanitized storage, persisted status/progress, reprocessing | Start analysis without waiting in a request | `backend/app/api/contracts.py`, `services/upload_validation.py`, `services/job_runner.py`; `tests/test_upload_validation.py` | Dashboard upload and processing banner |
| Text extraction | PyMuPDF text extraction; unreadable/scanned documents terminate honestly as unsupported | Avoid pretending OCR/text exists | `services/pdf_service.py`, `services/job_runner.py`; `tests/test_pages.py` | Processing/error states; source page viewer |
| Structured extraction | Chunked map-reduce extraction of parties, eight key fields, clauses, and obligations | Find core facts without reading every page | `services/extraction_service.py`, `models/models.py`, `services/job_runner.py` | Contract overview, obligations, summary |
| Verification and uncertainty | Quote existence normalisation; code-derived location; validators; explicit unavailable/not-found states | Make AI claims reviewable and prevent invented defaults | `services/extraction_service.py`, `services/validators.py`, `models/models.py`; `tests/test_phase3_validators.py`, `test_phase3_pipeline.py` | Status/confidence chips and source links across contract pages |
| Review queue | Deterministic flags for vague language, one-sided liability, missing/unusual termination, extraction issues and amendments; resolve/dismiss workflow | Prioritise human attention | `services/flag_service.py`, `api/flags.py`; `tests/test_flags.py` | Global `/review` and embedded “Needs human review” on overview |
| Source evidence | PDF page image, quote highlight, source/section links, text fallback | Let reviewer check a claim in context | `api/pages.py`, `services/pdf_highlight_service.py`, `frontend/src/app/contracts/[id]/source/page.tsx`; `tests/test_pages.py` | Clickable source reference from field, flag, schedule, summary, chat |
| Dates and obligations | Fixed/relative/recurring schedules, calendar-aware arithmetic, undated bucket, obligation completion | Turn terms into work and show limits of calculation | `services/deadline_service.py`, `services/schedule_service.py`, `api/obligations.py`; `tests/test_schedule.py` | Obligations page and timeline, grouped by party/date |
| Alerts | Lead-time alerts, read acknowledgement, email/browser push, repeat-until-read, `.ics` export | Prevent missed renewal/obligation windows | `services/alert_service.py`, `api/alerts.py`, `api/push.py`, `services/ics_service.py`; `tests/test_repeat_alerts.py` | Dashboard deadlines, reminder dialog, email acknowledgement page |
| Grounded Q&A | Hybrid Postgres full-text + local semantic retrieval, section matching/synonyms; cite-or-respond-not-found | Ask a contract question without ungrounded chat | `services/rag_service.py`, `services/vector_service.py`, `services/text_index.py`, `api/chat.py`; `tests/test_chat.py`, `test_chat_robustness.py`, `test_chat_understanding.py`, `test_hybrid.py` | Per-contract Chat page; citations link to source |
| Versioning/change review | Multiple contract versions, heading/similarity alignment, deterministic key-term diff, material changes and guarded impact statement | Understand a new draft/amendment | `services/compare_service.py`, `api/compare.py`, migration `003_versions_and_unified_model.py`; `tests/test_versions.py`, `test_compare.py` | Version picker, Compare page, version-upload dialog |
| Stakeholder summary | Code-built source-linked summary from verified terms, optional bounded AI overview, Markdown print/download | Communicate essential terms quickly | `services/summary_service.py`, `api/summary.py`; `tests/test_summary.py` | Summary page/download |
| Privacy and model choices | None/local/Groq/Anthropic-compatible providers; optional regex redaction; local embeddings | Allow a privacy-conscious operating mode | `core/config.py`, `services/privacy_service.py`, `services/llm_service.py`; `tests/test_privacy.py`, `test_secrets_not_leaked.py` | System status and setup documentation; not a first-class review control |
| Authentication/ownership | Demo mode or registration/login with scrypt password hashing, hashed session tokens, owner-scoped 404s | Keep one user’s contracts from another user | `api/auth.py`, `api/deps.py`, `core/security.py`; `tests/test_ownership.py` | Login when configured; no role/team workspace UI |
| Evaluation | JSON gold-set evaluator measuring precision/recall, verified rate, wrong-but-verified, false positives | Detect regressions in extraction claims | `backend/eval/run_eval.py`, `eval/gold/*`, `eval/results.md` | Developer-only; not in product UI |

### Data and execution model

The core hierarchy is sound: `User → Contract → ContractVersion → Document` plus extracted fields, parties, clauses, obligations, deadlines, review flags, chunks, summaries, messages, alerts, and version changes (`backend/app/models/models.py`). `contract_version_id` is retained on the evidence-bearing records, which is essential to source truth after amendments. Migrations 001–005 show a coherent evolution toward verified extraction, versioning, embeddings, and alerts.

Processing is deliberately serialized by an in-process asyncio semaphore (`services/job_runner.py`). Job state is persisted and interrupted jobs requeue on restart. This is credible for a desktop/small-instance product, but it is not yet a multi-worker queue architecture.

### Existing user journey

`Dashboard/upload → background processing → contract overview → source-backed fields + review flags → optional summary / source / obligations / timeline / chat / compare → alerts and calendar export`.

This is functionally rich. The weakness is that the navigation exposes destinations rather than the user’s next decision. “Timeline,” “Obligations,” “Summary,” “Source,” “Chat,” and “Compare” are all peer tabs, while the decision-critical state is split between the overview, a global review queue, timeline, and dashboard.

## C. Problem-statement coverage matrix

| Requirement | Current implementation | Evidence/path | Coverage | Gap |
|---|---|---|---|---|
| Structured contract metadata | Parties, effective/expiry/renewal/payment/termination fields | `services/extraction_service.py`, overview | COMPLETE | Domain coverage is intentionally narrow; no custom schema |
| Actionable obligations | Extract, calculate supported dates, complete obligations | `api/obligations.py`, `services/schedule_service.py` | COMPLETE | No assignee/team ownership or workflow escalation |
| Timeline and deadlines | Renewal notice, dates, recurrences, timeline and ICS | `services/deadline_service.py`, timeline page | COMPLETE | No portfolio calendar/workload view beyond dashboard list |
| Grounded Q&A | Hybrid retrieval, citations, not-found answer | `services/rag_service.py`, chat page | COMPLETE | Per-contract only; no portfolio questions |
| Source citations / verification | Quote match, derived locations, PDF highlight | `services/extraction_service.py`, `api/pages.py` | COMPLETE | Source can be visually buried behind page navigation |
| Explicit uncertainty | Status model and UI labels | `FieldStatus`, `StatusChip.tsx`, `emptyStateText` | COMPLETE | Users may still not grasp “confidence not calibrated” without a simple explanation |
| Deterministic validation | Date/amount/notice validation in code | `services/validators.py`, phase-3 tests | COMPLETE | Does not cover all legal/business term types |
| Review flags | Rules, severity, human lifecycle | `services/flag_service.py`, `/review` | COMPLETE | Flags are not tied to a reviewer, disposition rationale, or policy/playbook |
| Version comparison | Version upload, aligned sections, deterministic field changes | `api/compare.py`, `services/compare_service.py` | COMPLETE | No version lineage / amendment package or aggregate impact roll-up |
| Privacy mode | Provider choice, redaction, local embeddings | `services/privacy_service.py`, config | PARTIAL | Redaction excludes party names/dates/amounts by design; no tenant-level data governance or retention controls |
| Authentication and ownership | Session auth and contract ownership | `api/auth.py`, `api/deps.py` | COMPLETE | No RBAC, organisations, sharing, audit log |
| Reliable asynchronous processing | Persisted states and restart recovery | `services/job_runner.py` | PARTIAL | Single-process queue; no durable job broker/retry policy/dead-letter metrics |
| Evaluation harness | Synthetic gold corpus and measures | `eval/run_eval.py`, `eval/results.md` | PARTIAL | Current checked-in 100% is on three synthetic clean documents, not a real representative corpus |
| Accessible/responsive UI | Mobile drawer, focus management, loading/error states present | `AppShell.tsx`, `Sidebar.tsx`, page components | PARTIAL | No frontend test suite found; cross-browser/accessibility testing evidence absent |

## D. Competitive landscape: factual context

The market has converged on two adjacent product shapes:

1. **Lifecycle platforms**: repositories, configurable workflows, collaboration/approvals, e-signature, integrations, reporting and portfolio operations. DocuSign describes AI extraction, repository, renewal/obligation management and workflow automation in its IAM/CLM materials; Ironclad describes a repository, renewal briefs, structured obligations and workflow testing; Juro describes a searchable repository, AI extraction, reminders, reporting and in-browser collaboration. [DocuSign Agreement Manager](https://www.docusign.com/en-ca/products/platform/agreement-manager), [Ironclad product descriptions](https://legal.ironcladapp.com/fy2026-product-descriptions), [Juro intelligent repository](https://juro.com/intelligent-repository)
2. **Review/negotiation copilots**: speed initial review, playbook deviation detection, redlines, drafting, and Word-native work. Spellbook describes Word-based risk spotting/redlines and custom playbooks; Juro describes playbook-aligned review/redlines; Luminance describes comparison to model clauses and Word-based negotiation support. [Spellbook Reviews](https://www.spellbook.legal/reviews-light-mode), [Juro AI](https://juro.com/ai), [Luminance corporate brochure](https://www.luminance.com/files/brochures/Corporate-AI%20for%20End-to-End%20Contract%20Processing-2024.pdf)

ContractLens should not attempt to become both in the near term. Its evidence model is much closer to **post-signature decision support for contracts a business already has** than it is to a collaborative drafting or enterprise-workflow system.

| Capability | Existing approach | ContractLens today | Opportunity | Value | Complexity |
|---|---|---|---|---|---|
| Playbook review/redlines | Spellbook/Juro/DocuSign position review against company standards with suggested edits | Deterministic general flags, no policy/playbook or drafting | Add evidence-backed policy checks before considering redlines; avoid generating legal language as default | High for legal-led review, lower for post-signature users | High |
| Portfolio intelligence | DocuSign/Juro repositories support extracted metadata, reports and cross-contract queries | Contract list/dashboard plus per-contract search/chat | Portfolio “what needs attention?” query and filters over verified fields, alerts and flags | High | Medium |
| Obligation operations | Ironclad/DocuSign describe assigned/structured obligations and reminders | Dates, completion, alerts; party exists but no owner/assignee | Obligation inbox with accountable owner, evidence, due rule, status and acknowledgement | High | Medium |
| Renewal intelligence | Vendors offer reminders and renewal briefs | Accurate notice calculations, alerts, source evidence | Renewal decision packet: window, spend/terms, changes, open flags, evidence and disposition | High and differentiated by verification | Medium |
| Change impact | Vendors compare/redline around negotiation | Version diff with guarded impact text | Turn changes into an explicit impact checklist: changed fact → affected obligation/deadline/flag → action | High and differentiated | Medium |
| Contract Q&A | Repository assistants provide portfolio and document questions | Strong per-contract cited Q&A | Keep per-contract provenance, then add narrow cross-contract answers with contract/version citation per fact | High | Medium-high |
| Collaboration and workflow | CLMs use roles, approvals, comments, audit trails and integrations | Single-user/demo or login ownership; resolve/dismiss only | Minimal assignment, comments and decision/audit record—not a full CLM workflow engine | Medium | Medium-high |
| Word/PDF negotiation | Spellbook/Luminance focus Word redlines; platforms support drafting/signing | PDF reading and post-signature versions | Reject for core roadmap; integrate/export later only if the target user demands it | Low for stated core | High |
| OCR and intake breadth | Enterprise platforms commonly ingest legacy/scanned/docs | Text PDFs only; scanned PDFs explicit unsupported | Add OCR as a separately labelled preprocessing capability, never silently turn an uncertain scan into verified evidence | Medium | Medium-high |

## E. Product differentiation recommendation

### Product vision

**ContractLens should become the evidence-backed contract action cockpit for existing agreements:** a user can see what matters now, verify why it matters in the original text, decide what to do, assign/record that decision, and be warned before an obligation or renewal window is missed.

This is materially different from “AI contract summary.” It keeps the product’s strongest constraint—evidence, validation, and explicit uncertainty—and organizes it around action.

### What it can own

- **Trustworthy actionability:** every surfaced deadline, risk cue, extracted term, comparison claim and answer carries status plus a route to its exact version/source.
- **Honest operational coverage:** “not found,” “unsupported,” and “could not calculate” remain visible states, so an empty dashboard cannot be mistaken for a healthy portfolio.
- **Change-to-consequence:** a new version should answer not merely “what text changed?” but “which verified term, deadline, obligation, flag, and upcoming decision is affected?”
- **Renewal and obligation decision support:** do not just notify; assemble the evidence needed to decide and record the outcome.

Competitors also advertise citations/confidence in places—Juro says its responses include confidence and source highlighting—so citations alone are not a durable claim. The differentiator should be **systematic source verification plus deterministic date/amount/notice validation, made central to action workflows**, not a generic “more accurate AI” claim. [Juro intelligent repository](https://juro.com/intelligent-repository)

## F. UX and information architecture restructuring

### Current structure

```text
Global: Dashboard | Needs review | list of contracts
Contract: Overview | Summary | Source | Obligations | Timeline | Compare | Chat
```

The overview is already an implicit cockpit, but the interface makes users discover important actions by opening peer feature pages. A user deciding whether to renew must mentally join: overview terms, timeline, review flags, summary, maybe compare, and source. This is avoidable cognitive work.

### Proposed structure

```text
Home / Portfolio
  Attention now · renewals · overdue obligations · incomplete analysis · recent changes

Contract workspace
  1. Overview        “What is this and what must I decide?”
  2. Review          “What requires human judgement?”
  3. Commitments     “Who must do what and when?”
  4. Changes         “What changed and what does it affect?”
  5. Ask & Evidence  “Find an answer and inspect its proof”

Advanced / contextual controls
  Versions · source document · export · reprocess · alert settings · deletion
```

**Overview** should lead with P0: processing integrity, next renewal/expiry decision, overdue/near-term commitments, high-severity open review items, and material version changes. Below that, it can show P1 term facts (parties, term, money, renewal) and a compact “what is unknown/unavailable” block. This is the user’s landing point after processing.

**Review** should consolidate current per-contract flags, globally filtered queue behavior, and a visible evidence/action pattern: finding → source → resolution/disposition → optional owner. Keep the global queue as a portfolio filter rather than a second conceptual workflow.

**Commitments** should combine the current obligations and timeline pages around the action unit: accountable party/owner, action, due rule, calculated date, status, source, and recurrence. A date/party view toggle remains useful but should not make the user choose a different product area.

**Changes** should retain the current strong section/key-term comparison, but show a consequence panel with impacted extracted facts, dates, commitments, and review flags. Version upload remains here.

**Ask & Evidence** should unite chat and the source viewer. Every answer should keep the current citations, and evidence should open in a side panel or predictable context instead of feeling like a destination change. Do not hide the original document; it is the trust anchor.

### Information priority

| Information | Priority | Where it belongs |
|---|---|---|
| Processing failed/partial/unsupported, extraction unavailable | P0 | Overview top banner; portfolio attention list |
| Renewal-notice deadline, expiry, overdue obligations, high open review flags | P0 | Overview top action strip; portfolio attention list |
| Material changes in current version | P0 | Overview + Changes |
| Contract identity, parties, current term, renewal state, payment summary | P1 | Overview |
| All review flags, source quote/status, commitment dates/status | P1 | Review / Commitments |
| Stakeholder narrative summary, clause list, completed/dismissed items | P2 | Expandable sections; summary export |
| Raw document, version selector, reprocess, `.ics`/Markdown downloads, alert configuration, delete/restore | Advanced/contextual | Context menu or evidence panel |

### Concrete UX findings

- The current sidebar emphasizes dashboard and review queue, then a raw contract list. It provides no portfolio “attention now” view, no search/filter, and no way to distinguish action status before opening a contract (`frontend/src/components/Sidebar.tsx`).
- Review items appear both embedded in the overview and on a global queue. This is useful reuse but lacks a single action-state mental model; expose it as a filtered view of the same Review area.
- Current timeline is thoughtful—party/date groupings, recurrence folding, unschedulable obligations, source links—but obligations are separately navigated, forcing users to infer their relationship.
- Summary is code-built from verified data and valuable for sharing, but it is a supporting output. It should not compete with the decision-focused Overview as a peer primary destination.
- Chat is source-backed and should stay discoverable, but chat is a means of investigating—not the default work surface. Put it beside evidence and suggested common questions.
- “Confidence” is shown throughout, while the README correctly says it is not calibrated. Lead with verification status and explain confidence as a heuristic secondary signal; do not visually equate `0.92` with measured probability.

## G. AI UX review

### Strengths to preserve

- Deterministic facts and AI-assisted outputs are largely distinguishable in the model/status design.
- Source links are routinely present for supported outputs; PDF highlighting is a strong evidence interaction.
- `not_found`, `extraction_unavailable`, partial processing, unsupported scans, and uncalculable dates are deliberately separated.
- The chat fallback returns labelled search results when an LLM is unavailable; it does not invent an answer.
- Flags are rules-based prompts, not model-declared legal risk.

### Improvements needed

1. Replace generic confidence prominence with a three-part evidence label: **verified source**, **validation result**, and **AI assistance**. Show numeric confidence only behind “How was this assessed?”
2. Give every finding a standard action footer: **View evidence / Mark reviewed / Record decision / Create or update commitment**. Current resolve/dismiss is useful but cannot capture why a decision was made.
3. Treat missing coverage as actionable. “Extraction unavailable” and “partial” should appear alongside open issues in the portfolio attention queue, not only inside the contract page.
4. Preserve wording discipline. The current renewal notice UI says it was “worked out by AI” even though the calculation is code-side from extracted inputs. Say “calculated from extracted, source-linked terms” and label any AI-derived extraction separately.
5. In comparison, show the evidence status of old/new values before any optional impact statement. The implementation already guards numbers in impact text; the UI should make that boundary obvious.

## H. Industrial product review

| Dimension | Repository finding | Risk / implication | Recommendation |
|---|---|---|---|
| Auth and ownership | Scrypt hashes, hashed opaque session tokens, per-owner 404; production blocks demo mode | Good single-user baseline; no organisation/role model | Before shared deployment, add org/role/permission model and security test matrix |
| File safety | PDF header/size validation and storage-path containment | PDF parser vulnerabilities and malware scanning are not evidenced | Use isolated scanning/storage and content-security controls before public deployment |
| AI/data privacy | Local embedding option, no-provider mode, redaction option, secrets via `SecretStr` | Hosted LLM redaction intentionally leaves core contract facts; no retention/audit/region controls | Make provider/data-flow disclosure per analysis explicit; add retention/deletion policy and audit evidence |
| API security | Owner-scoped dependencies, CORS config, cookie production guard | CSRF posture, rate limiting, security headers, session revocation/rotation, and authz roles not evidenced | Threat model and add controls before internet exposure |
| Reliability | Persisted processing states and restart requeue; catches failures | In-process single semaphore has no distributed durability, backoff schedule, DLQ, or worker isolation | Move jobs to a durable queue before multi-user scale; expose job history/retry reason |
| Performance | Chunks/indexes, FTS plus pgvector, chunked extraction | Vector column is modelled as JSONB despite pgvector migration name; no query plans/load tests found | Confirm actual vector index/query path; benchmark large PDFs/concurrent users; establish limits/SLOs |
| Observability | Python logging and persisted status/error fields | No tracing, error tracking, metrics dashboard, model cost/latency telemetry or audit log evidenced | Define structured events for upload → extraction → validation → alert → user disposition |
| Data integrity | Version-aware records, foreign keys/cascades, soft delete/restore paths | No immutable evidence snapshots/audit history for changes to flags/obligations/alerts | Add append-only decision/audit events before regulated/high-stakes workflows |
| Testing | Broad deterministic backend suite; synthetic eval corpus | No frontend test files found; eval result is only 3 synthetic PDFs | Add component/E2E/accessibility tests and representative held-out real/public corpus |

The checked-in evaluation reports 100% precision/recall/verified rate and zero wrong-but-verified for three synthetic documents (`backend/eval/results.md`). Treat this as a pipeline regression signal, not a production quality claim, exactly as the README cautions.

## I. Feature recommendations

### P0 — Critical

| Feature | User problem | Differentiation | Complexity | Recommendation |
|---|---|---|---|---|
| Action cockpit and “analysis coverage” state | Users cannot tell whether silence means healthy, incomplete, or unprocessed | Makes honest uncertainty operational | Medium | Implement first as UI/API composition over existing statuses, alerts, flags, schedules and changes |
| Unified commitments workspace | Obligations and dates are separated even though they drive one action | Evidence-backed commitment tracking rather than a static calendar | Medium | Consolidate timeline + obligations; retain both views |
| Renewal decision packet | Reminder alone does not explain what to decide | Source-linked renewal window, terms, changes and open issues | Medium | Build from existing verified fields, schedule, flags, summary and compare; do not introduce predictive “savings” claims |
| Change impact checklist | Diff burden is still on the reviewer | Connect verified text changes to actions/deadlines | Medium | Extend existing comparison output with deterministic relationships and explicit “unknown” gaps |
| Production foundations | Current design is desktop/small-instance oriented | Trust is the product; reliability/security must match it | Medium-high | Durable jobs, job events, rate limits, hardened upload/storage, observability, deployment configuration |

### P1 — Important

| Feature | User problem | Differentiation | Complexity | Recommendation |
|---|---|---|---|---|
| Portfolio filters and cross-contract verified search | Important work is spread across contract pages | Portfolio answers can cite a contract/version per statement | Medium | Start with filters over structured verified fields/flags/deadlines, then bounded aggregate Q&A |
| Commitment ownership and decision record | “Completed” lacks accountable owner or rationale | Evidence + human decision trail | Medium | Add owner, reviewer, resolution reason, action notes and timestamps |
| Policy/playbook checks | General flags cannot represent a company’s negotiation position | Source-grounded deviation evidence | High | Add only after defining target legal user and policy authoring/approval UX; do not auto-redline first |
| OCR intake with confidence boundary | Scanned PDFs are rejected | Honest broader intake | Medium-high | Add a clearly labelled OCR stage and require extra source review where text confidence is low |
| Evidence side panel | Evidence navigation can interrupt flow | Keeps source verification immediate | Medium | Reuse pages/highlight APIs; maintain full-document accessibility |

### P2 — Future

| Feature | User problem | Differentiation | Complexity | Recommendation |
|---|---|---|---|---|
| Organisational roles and sharing | Teams need accountable shared work | Enables but does not differentiate | High | Build after audit/event model and clear tenancy boundaries |
| Integration/export API | Contract facts need to reach business systems | Extends trusted data downstream | High | Expose only verified/status-rich data; start read-only/webhooks |
| Cross-contract entity/relationship graph | Agreements/amendments/SOWs interact | Valuable for complex estates | High | Defer until entity resolution and source-version semantics are designed |

### Reject / avoid for the core roadmap

- **Generic AI drafting, automatic redlines, e-signature, and full CLM workflow replication.** These are large adjacent markets already served by Word-native and lifecycle platforms, and they dilute the post-signature evidence/action thesis.
- **A single “risk score.”** It would conceal differences between verified facts, deterministic rule flags, incomplete analysis, and human judgement.
- **Silent OCR or silent completion of missing data.** This would undermine the product’s strongest trust property.
- **Autonomous legal or renewal decisions.** The product should assemble evidence and prompt accountable action, not represent that it can decide legal/commercial outcomes.

## J. Keep, consolidate, de-emphasize

| Decision | Features | Why |
|---|---|---|
| Keep | Source links/highlights, verification statuses, deterministic validators, not-found/unavailable split, grounded Q&A, version-aware evidence, flags, schedule engine | These are the product’s trust foundation and work together |
| Consolidate | Obligations + Timeline; embedded flags + global review queue; Chat + Source; summary facts + overview | Existing value is scattered across peer pages rather than organized around a decision |
| De-emphasize | Narrative stakeholder summary as a primary navigation destination; raw version controls; download controls | Useful outputs/controls, but not the immediate question “what should I do?” |
| Hide behind advanced controls | Reprocess, alert plumbing/setup, debug-like confidence detail, raw text, delete/restore, exports | Important but can distract from review and action |
| Potentially remove later | None recommended now | No feature should be deleted before usage evidence exists; simpler information architecture can remove perceived clutter without loss |

## K. Industrialization roadmap

1. **Foundation:** document target user/deployment, establish real/public eval corpus, baseline security threat model, and capture processing/LLM metrics.
2. **UX restructuring:** deliver action cockpit, analysis-coverage state, unified commitments, and evidence-in-context using existing data—no new model behavior required.
3. **Core product improvements:** renewal decision packet, change impact checklist, decision records and portfolio filters.
4. **AI reliability:** broaden corpus, track verified-but-wrong separately by field/category, calibrate/refine user-facing confidence presentation, add OCR boundary if needed.
5. **Performance/security:** durable worker queue, object storage/scan isolation, rate limits, role model, audit events, indexing/load testing and backups.
6. **Advanced intelligence:** verified cross-contract questions, relationship-aware amendments/SOWs, narrowly scoped policy checks.
7. **Deployment readiness:** tenancy, observability/alerts, retention/deletion operations, incident/runbook and accessibility/E2E release gates.

## L. Proposed implementation plan — not approved for implementation

| Change | Objective | Frontend | Backend/data/API | Tests | Migration | Risk / rollback / dependencies |
|---|---|---|---|---|---|---|
| Action cockpit | Put action state first without changing extraction | Dashboard and contract overview; shared attention components | Compose existing contract/status/flag/deadline/change responses; possibly a read-only `/attention` endpoint | Component states for ready/partial/unavailable/overdue; API contract tests | No if composed; maybe no | Low-medium. Feature-flag/new route; revert navigation safely. Depends on agreed P0 definitions |
| Unified Commitments | Combine date/action work views | Replace tabs with `commitments`; reuse timeline/obligation components | Read model combining schedule + obligation status | Schedule, completion, recurrence, undated and accessibility tests | No initially | Medium. Keep legacy routes as redirects during transition |
| Renewal decision packet | Make renewal alert decision-ready | Overview callout and renewal detail panel | Read model over verified renewal fields, schedule, flags, summary, latest changes | Date-window edge cases; partial analysis states; source-link tests | No | Medium. Never show recommendation without source/status; rollback is removal of panel |
| Change impact checklist | Connect diffs to affected work | Compare page consequence panel | Extend comparison service/read response with deterministic links | Compare fixtures for dates/obligations/flags/version sources | Possibly no; add stored links only if performance requires | Medium. Mark inferences vs evidence; preserve existing compare response |
| Decision/assignment records | Make human action accountable | Review and commitments action dialogs | Add `decision_events`/assignee fields; owner lookup; endpoints | Ownership, audit immutability, filtering, UI flows | Yes | Medium-high. Append-only events permit rollback at read/UI layer. Depends on user/org model decision |
| Portfolio verified search | Answer portfolio operational questions | Dashboard filters/search and results evidence cards | Filter endpoint over verified fields, flags/deadlines; later retrieval aggregate | Permission isolation, filter correctness, citations/version source tests | Indexes likely | Medium-high. Start structured/read-only, avoid unconstrained aggregate LLM |
| Durable processing | Reliable multi-user operation | Processing history/retry UI | Queue worker, idempotent jobs, job events/attempts | Crash/retry/idempotency/integration load tests | Likely | High. Run queue alongside current path with feature flag; needs deployment choice |
| OCR preprocessing | Support scans without weakening trust | Explicit OCR stage/confidence and review notice | OCR service, per-page OCR metadata, provenance | Scanned fixtures, low-confidence, quote source checks | Likely | High. Opt-in ingestion; retain original and mark OCR-derived evidence |

## Appendix: external sources consulted

- [DocuSign Agreement Manager](https://www.docusign.com/en-ca/products/platform/agreement-manager) — repository, extraction, reports, access/audit and renewal/obligation management claims.
- [DocuSign enterprise / Iris overview](https://www.docusign.com/solutions/enterprise) — AI-assisted lifecycle and human-control positioning.
- [Ironclad FY2026 product descriptions](https://legal.ironcladapp.com/fy2026-product-descriptions) — repository, AI intake, renewal brief and structured-obligation claims.
- [Juro intelligent repository](https://juro.com/intelligent-repository) — repository, custom data/reporting, reminders, source/confidence claims.
- [Juro AI overview](https://juro.com/ai) — playbook review/redline and lifecycle positioning.
- [Spellbook Reviews](https://www.spellbook.legal/reviews-light-mode) — Word-native review/redline and playbook positioning.
- [Luminance Corporate brochure](https://www.luminance.com/files/brochures/Corporate-AI%20for%20End-to-End%20Contract%20Processing-2024.pdf) — Word negotiation/Q&A/redrafting positioning.

