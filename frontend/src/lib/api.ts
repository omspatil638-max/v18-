import type {
  AckTokenInfo,
  AskResponse,
  Alert,
  AlertSettings,
  AlertSettingsUpdate,
  AlignedSections,
  AuthConfig,
  BriefingPreview,
  ChatMessage,
  ComparisonResponse,
  ContractDetail,
  ContractStatusInfo,
  ContractSummary,
  ContractUpdate,
  DataQualityReport,
  ExtractedField,
  FieldReviewRequest,
  NameCount,
  RenewalsResponse,
  SearchResponse,
  DocumentKind,
  VersionSummary,
  ContractText,
  DeadlineWithContext,
  FlagQueueFilters,
  FlagStatus,
  FlagSummary,
  HighlightResponse,
  Obligation,
  ObligationStatus,
  PolicyRuleBody,
  ProfileUpdate,
  PolicyRulesResponse,
  PushSubscriptionPayload,
  QueueFlag,
  ScheduleResponse,
  ResendVerificationResponse,
  ReviewFlag,
  SignupRequest,
  SignupResponse,
  SummaryResponse,
  SystemStatus,
  User,
} from "./types";

const API_BASE = "/api";

/** An error from the backend: the message is the backend `detail`; `status` is the HTTP status. */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Extract a human-readable message from an unknown thrown value. */
export function errorMessage(e: unknown, fallback = "Something went wrong."): string {
  if (e instanceof Error && e.message) return e.message;
  if (typeof e === "string" && e) return e;
  return fallback;
}

/** Turn a FastAPI error body into text. `detail` is a string, or a list for 422 validation errors. */
function detailFromBody(body: unknown): string | null {
  if (typeof body !== "object" || body === null || !("detail" in body)) return null;
  const detail = (body as { detail: unknown }).detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) =>
        typeof d === "object" && d !== null && "msg" in d
          ? String((d as { msg: unknown }).msg)
          : null
      )
      .filter((m): m is string => Boolean(m));
    if (msgs.length > 0) return msgs.join("; ");
  }
  return null;
}

/**
 * Shared request helper.
 * - Throws an Error whose message is the backend `detail` when present.
 * - On 401 redirects to /login (unless already there).
 * - Returns undefined-cast for 204/empty responses.
 */
async function request<T>(path: string, init?: RequestInit, fallbackError = "Request failed"): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    credentials: "same-origin",
    ...init,
  });

  if (!res.ok) {
    if (
      res.status === 401 &&
      typeof window !== "undefined" &&
      !window.location.pathname.startsWith("/login") &&
      !window.location.pathname.startsWith("/welcome")
    ) {
      window.location.assign("/login");
    }
    let detail: string | null = null;
    try {
      detail = detailFromBody(await res.json());
    } catch {
      // Non-JSON error body: fall back to the generic message.
    }
    throw new ApiError(detail ?? `${fallbackError} (HTTP ${res.status})`, res.status);
  }

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** "?version_id=<id>", or "" when no version is given (the server then uses the current version). */
function versionQuery(versionId?: string | null): string {
  return versionId ? `?version_id=${encodeURIComponent(versionId)}` : "";
}

function jsonInit(method: string, body?: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  };
}

// ─── System / auth ────────────────────────────────────────────────────────────

export function fetchSystemStatus(): Promise<SystemStatus> {
  return request("/system/status", undefined, "Failed to fetch system status");
}

export function fetchAuthConfig(): Promise<AuthConfig> {
  return request("/auth/config", undefined, "Failed to fetch auth config");
}

export function fetchMe(): Promise<User> {
  return request("/auth/me", undefined, "Failed to fetch current user");
}

export function login(email: string, password: string): Promise<User> {
  return request("/auth/login", jsonInit("POST", { email, password }), "Sign-in failed");
}

export function register(email: string, name: string, password: string): Promise<User> {
  return request("/auth/register", jsonInit("POST", { email, name, password }), "Registration failed");
}

/** Creates the account and signs in. The server's 4xx `detail` is user-safe text: show it as is. */
export function signup(body: SignupRequest): Promise<SignupResponse> {
  return request("/auth/signup", jsonInit("POST", body), "Could not create the account");
}

export function verifyEmail(code: string): Promise<User> {
  return request("/auth/verify-email", jsonInit("POST", { code }), "Could not confirm the code");
}

export function resendVerification(): Promise<ResendVerificationResponse> {
  return request("/auth/resend-verification", { method: "POST" }, "Could not send a new code");
}

export function updateProfile(update: ProfileUpdate): Promise<User> {
  return request("/auth/profile", jsonInit("PUT", update), "Could not save your profile");
}

export function completeOnboarding(): Promise<User> {
  return request("/auth/onboarding/complete", { method: "POST" }, "Could not finish the setup");
}

export function logout(): Promise<void> {
  return request("/auth/logout", { method: "POST" }, "Sign-out failed");
}

// ─── Contracts ────────────────────────────────────────────────────────────────

export function fetchContracts(): Promise<ContractSummary[]> {
  return request("/contracts", undefined, "Failed to fetch contracts");
}

export function fetchContractById(id: string, versionId?: string | null): Promise<ContractDetail> {
  return request(`/contracts/${id}${versionQuery(versionId)}`, undefined, "Failed to fetch contract details");
}

/** Edit a contract's title, counterparty, type or tags. Returns the updated contract. */
export function updateContract(id: string, update: ContractUpdate): Promise<ContractSummary> {
  return request(`/contracts/${id}`, jsonInit("PATCH", update), "Failed to save the contract details");
}

export function fetchContractStatus(id: string, versionId?: string | null): Promise<ContractStatusInfo> {
  return request(`/contracts/${id}/status${versionQuery(versionId)}`, undefined, "Failed to fetch contract status");
}

export function fetchContractText(id: string, versionId?: string | null): Promise<ContractText> {
  return request(`/contracts/${id}/text${versionQuery(versionId)}`, undefined, "Failed to fetch document text");
}

/** Uploads a PDF or Word (.docx) file. The backend answers 202 with the new contract; processing continues in the background. */
export function uploadContract(file: File): Promise<ContractSummary> {
  const formData = new FormData();
  formData.append("file", file);
  return request("/contracts/upload", { method: "POST", body: formData }, "Upload failed");
}

/** Loads a clearly labelled synthetic sample contract (202) so the app can be tried without real data. */
export function loadSampleContract(): Promise<ContractSummary> {
  return request("/contracts/sample", { method: "POST" }, "Could not load the sample contract");
}

export function reprocessContract(id: string, versionId?: string | null): Promise<ContractStatusInfo> {
  return request(`/contracts/${id}/reprocess${versionQuery(versionId)}`, { method: "POST" }, "Failed to re-run extraction");
}

/** Soft delete by default (recoverable with restoreContract). Pass permanent to erase files too. */
export function deleteContract(id: string, permanent = false): Promise<void> {
  return request(`/contracts/${id}${permanent ? "?permanent=true" : ""}`, { method: "DELETE" }, "Failed to delete contract");
}

export function restoreContract(id: string): Promise<ContractSummary> {
  return request(`/contracts/${id}/restore`, { method: "POST" }, "Failed to restore contract");
}

/** Upload a further version (v2, an amendment, ...) of an existing contract. */
export function uploadVersion(
  contractId: string,
  file: File,
  options: { label?: string; kind?: DocumentKind; makeCurrent?: boolean } = {},
): Promise<VersionSummary> {
  const formData = new FormData();
  formData.append("file", file);
  if (options.label) formData.append("label", options.label);
  if (options.kind) formData.append("kind", options.kind);
  if (options.makeCurrent !== undefined) formData.append("make_current", String(options.makeCurrent));
  return request(`/contracts/${contractId}/versions`, { method: "POST", body: formData }, "Failed to upload the new version");
}

export function fetchVersions(contractId: string): Promise<VersionSummary[]> {
  return request(`/contracts/${contractId}/versions`, undefined, "Failed to fetch versions");
}

/** original=true returns the uploaded .docx for a Word upload; otherwise the (converted) PDF. */
export function contractFileUrl(id: string, versionId?: string | null, original = false): string {
  const base = `${API_BASE}/contracts/${id}/file${versionQuery(versionId)}`;
  if (!original) return base;
  return `${base}${base.includes("?") ? "&" : "?"}original=true`;
}

// ─── Version comparison ───────────────────────────────────────────────────────

export interface CompareParams {
  fromVersionId?: string | null;
  toVersionId?: string | null;
  includeUnchanged?: boolean;
}

function compareQuery(p: CompareParams, withUnchanged: boolean): string {
  const params = new URLSearchParams();
  if (p.fromVersionId) params.set("from_version_id", p.fromVersionId);
  if (p.toVersionId) params.set("to_version_id", p.toVersionId);
  if (withUnchanged && p.includeUnchanged) params.set("include_unchanged", "true");
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** Changes between two versions. Server defaults: to = current version, from = the one before it. */
export function fetchComparison(contractId: string, params: CompareParams = {}): Promise<ComparisonResponse> {
  return request(
    `/contracts/${contractId}/compare${compareQuery(params, true)}`,
    undefined,
    "Failed to compare the versions",
  );
}

/** Clause-by-clause alignment of two versions (with a word diff for changed clauses), in document order. */
export function fetchAlignedSections(contractId: string, params: CompareParams = {}): Promise<AlignedSections> {
  return request(
    `/contracts/${contractId}/compare/sections${compareQuery(params, false)}`,
    undefined,
    "Failed to load the side-by-side comparison",
  );
}

/** Re-runs a comparison in the background (202). Poll fetchComparison until its status is no longer "running". */
export function runComparison(contractId: string, params: CompareParams = {}): Promise<void> {
  return request(
    `/contracts/${contractId}/compare${compareQuery(params, false)}`,
    { method: "POST" },
    "Failed to start the comparison",
  );
}

// ─── Obligations / deadlines / clauses ────────────────────────────────────────

export function fetchObligations(contractId: string): Promise<Obligation[]> {
  return request(`/contracts/${contractId}/obligations`, undefined, "Failed to fetch obligations");
}

export function updateObligationStatus(
  obligationId: string,
  status: ObligationStatus
): Promise<Obligation> {
  return request(
    `/obligations/${obligationId}`,
    jsonInit("PATCH", { status }),
    "Failed to update obligation status"
  );
}

/** Upcoming deadlines across all contracts, soonest first. Completed obligations never appear. */
export function fetchGlobalDeadlines(
  daysAhead: number = 365,
  includeOverdue: boolean = true,
): Promise<DeadlineWithContext[]> {
  return request(
    `/deadlines?days_ahead=${daysAhead}&include_overdue=${includeOverdue}`,
    undefined,
    "Failed to fetch global deadlines",
  );
}

/** Dated schedule for one contract (contract dates, renewal notice, obligations, recurring occurrences) plus obligations that have no calculable date. */
export function fetchSchedule(contractId: string, versionId?: string): Promise<ScheduleResponse> {
  return request(
    `/contracts/${contractId}/schedule${versionId ? `?version_id=${versionId}` : ""}`,
    undefined,
    "Failed to fetch the schedule",
  );
}

/** Same-origin URL of the calendar export. Use as <a href download>: the browser sends the session cookie. */
export function calendarUrl(contractId?: string): string {
  return contractId ? `${API_BASE}/contracts/${contractId}/calendar.ics` : `${API_BASE}/calendar.ics`;
}

// ─── Review flags ─────────────────────────────────────────────────────────────

/** Cross-contract review queue: flags on the current version of each live contract. */
export function fetchFlagQueue(filters: FlagQueueFilters = {}): Promise<QueueFlag[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.severity) params.set("severity", filters.severity);
  if (filters.contractId) params.set("contract_id", filters.contractId);
  const qs = params.toString();
  return request(`/flags${qs ? `?${qs}` : ""}`, undefined, "Failed to fetch review flags");
}

export function fetchFlagSummary(): Promise<FlagSummary> {
  return request("/flags/summary", undefined, "Failed to fetch review flag summary");
}

/** Fired on `window` after a flag changes status, so counters elsewhere (sidebar, summary) can refetch. */
export const FLAGS_CHANGED_EVENT = "contractlens:flags-changed";

/** Fired on `window` (by the sidebar Search button) to open the Ctrl+K search palette. */
export const OPEN_SEARCH_EVENT = "contractlens:open-search";

export async function updateFlagStatus(flagId: string, status: FlagStatus): Promise<ReviewFlag> {
  const updated = await request<ReviewFlag>(
    `/flags/${flagId}`,
    jsonInit("PATCH", { status }),
    "Failed to update review flag"
  );
  if (typeof window !== "undefined") window.dispatchEvent(new Event(FLAGS_CHANGED_EVENT));
  return updated;
}

export function fetchContractFlags(contractId: string, versionId?: string, status?: FlagStatus): Promise<ReviewFlag[]> {
  const params = new URLSearchParams();
  if (versionId) params.set("version_id", versionId);
  if (status) params.set("status", status);
  const qs = params.toString();
  return request(`/contracts/${contractId}/flags${qs ? `?${qs}` : ""}`, undefined, "Failed to fetch review flags");
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

/** dueOnly = only alerts whose lead time has been reached (needs attention now). Acknowledged ones are excluded. */
export function fetchAlerts(options: { dueOnly?: boolean } = {}): Promise<Alert[]> {
  const dueOnly = options.dueOnly ?? false;
  return request(
    `/alerts?unacknowledged_only=true&due_only=${dueOnly}`,
    undefined,
    "Failed to fetch alerts",
  );
}

export function acknowledgeAlert(alertId: string): Promise<{ message: string }> {
  return request(`/alerts/${alertId}/acknowledge`, { method: "PATCH" }, "Failed to acknowledge alert");
}

export function fetchAlertSettings(): Promise<AlertSettings> {
  return request("/alerts/settings", undefined, "Failed to load reminder settings");
}

export function updateAlertSettings(update: AlertSettingsUpdate): Promise<AlertSettings> {
  return request("/alerts/settings", jsonInit("PUT", update), "Failed to save reminder settings");
}

/** Sends a test email to the saved address. The server answers 503 with the reason when SMTP is not configured. */
export function sendTestEmail(): Promise<{ message: string }> {
  return request("/alerts/test-email", { method: "POST" }, "Failed to send the test email");
}

/** What the morning briefing would say right now. Sends nothing. */
export function fetchBriefingPreview(): Promise<BriefingPreview> {
  return request("/alerts/briefing/preview", undefined, "Failed to preview the briefing");
}

/** Marks every currently due, unacknowledged alert as read (stops email and push repeats). */
export function acknowledgeAllAlerts(): Promise<{ acknowledged: number }> {
  return request("/alerts/acknowledge-all", { method: "POST" }, "Failed to mark the alerts as read");
}

/** Public, no sign-in: what an email "mark as read" link refers to. Read-only. */
export function fetchAckToken(token: string): Promise<AckTokenInfo> {
  return request(`/alerts/ack/${encodeURIComponent(token)}`, undefined, "Failed to check this link");
}

/** Public, no sign-in: marks the alerts behind an email link as read. Always POST, never GET. */
export function submitAckToken(token: string): Promise<{ acknowledged: number }> {
  return request(`/alerts/ack/${encodeURIComponent(token)}`, { method: "POST" }, "Failed to mark the alerts as read");
}

// ─── Browser push ─────────────────────────────────────────────────────────────

/** `public_key` is null when push is not configured on the server. */
export function fetchPushPublicKey(): Promise<{ public_key: string | null }> {
  return request("/push/public-key", undefined, "Failed to check browser notification support");
}

export function subscribePush(subscription: PushSubscriptionPayload): Promise<{ message: string }> {
  return request("/push/subscribe", jsonInit("POST", subscription), "Failed to register this browser");
}

export function unsubscribePush(endpoint: string): Promise<{ message: string }> {
  return request("/push/unsubscribe", jsonInit("POST", { endpoint }), "Failed to unregister this browser");
}

/** Sends a test push. The server answers 503 with the reason when push is not configured or nothing is registered. */
export function sendTestPush(): Promise<{ message: string; sent: number }> {
  return request("/push/test", { method: "POST" }, "Failed to send the test notification");
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

export function fetchChatHistory(contractId: string): Promise<ChatMessage[]> {
  return request(`/contracts/${contractId}/chat`, undefined, "Failed to fetch chat history");
}

export function sendChatMessage(contractId: string, content: string): Promise<ChatMessage> {
  return request(`/contracts/${contractId}/chat`, jsonInit("POST", { content }), "Failed to send chat message");
}

// ─── PDF viewer (server-rendered page images) ─────────────────────────────────

/** Same-origin PNG of one page. Use directly as <img src>; the session cookie is sent. */
export function pageImageUrl(
  contractId: string,
  page: number,
  options: { scale?: number; versionId?: string | null } = {},
): string {
  const params = new URLSearchParams();
  if (options.scale) params.set("scale", String(Math.min(3, Math.max(0.5, options.scale))));
  if (options.versionId) params.set("version_id", options.versionId);
  const qs = params.toString();
  return `${API_BASE}/contracts/${contractId}/pages/${page}/image${qs ? `?${qs}` : ""}`;
}

/** Where a quote sits on a page, as normalised rectangles. `matched` is false when the quote is not on that page. */
export function fetchHighlights(
  contractId: string,
  page: number,
  quote: string,
  versionId?: string | null,
  signal?: AbortSignal,
): Promise<HighlightResponse> {
  const params = new URLSearchParams({ q: quote });
  if (versionId) params.set("version_id", versionId);
  return request(
    `/contracts/${contractId}/pages/${page}/highlights?${params.toString()}`,
    { signal },
    "Failed to locate the quote on the page",
  );
}

// ─── Stakeholder summary ──────────────────────────────────────────────────────

export function fetchSummary(contractId: string, versionId?: string | null): Promise<SummaryResponse> {
  return request(`/contracts/${contractId}/summary${versionQuery(versionId)}`, undefined, "Failed to load the summary");
}

/** Re-writes the optional AI overview in the background (202). Reload the summary a few seconds later. */
export function regenerateSummaryOverview(contractId: string, versionId?: string | null): Promise<{ message: string }> {
  return request(
    `/contracts/${contractId}/summary${versionQuery(versionId)}`,
    { method: "POST" },
    "Failed to rewrite the overview",
  );
}

/** Same-origin Markdown download. Use as <a href download>. */
export function summaryMarkdownUrl(contractId: string, versionId?: string | null): string {
  return `${API_BASE}/contracts/${contractId}/summary.md${versionQuery(versionId)}`;
}

// ─── Field review (confirm / correct / not in contract / revert) ─────────────

/** Fired on `window` after a field is reviewed, so counters elsewhere (sidebar data-quality badge) can refetch. */
export const FIELDS_CHANGED_EVENT = "contractlens:fields-changed";

/** The server's 422 detail is user-safe text: show it as is. */
export async function reviewField(
  contractId: string,
  fieldId: string,
  body: FieldReviewRequest,
): Promise<ExtractedField> {
  const updated = await request<ExtractedField>(
    `/contracts/${contractId}/fields/${fieldId}/review`,
    jsonInit("POST", body),
    "Failed to save your review",
  );
  if (typeof window !== "undefined") window.dispatchEvent(new Event(FIELDS_CHANGED_EVENT));
  return updated;
}

// ─── Data quality / renewals / search / tags ─────────────────────────────────

export function fetchDataQuality(): Promise<DataQualityReport> {
  return request("/data-quality", undefined, "Failed to load the data quality report");
}

export function fetchRenewals(windowDays = 90, expiredDays = 30): Promise<RenewalsResponse> {
  return request(
    `/renewals?window=${windowDays}&expired_days=${expiredDays}`,
    undefined,
    "Failed to load renewals",
  );
}

export function searchAll(query: string, signal?: AbortSignal): Promise<SearchResponse> {
  return request(`/search?q=${encodeURIComponent(query)}`, { signal }, "Search failed");
}

export function askContracts(question: string): Promise<AskResponse> {
  return request("/portfolio/ask", jsonInit("POST", { question }), "Could not answer that question");
}

export function fetchTags():Promise<NameCount[]> {
  return request("/tags", undefined, "Failed to load tags");
}

export function fetchContractTypes(): Promise<NameCount[]> {
  return request("/contract-types", undefined, "Failed to load contract types");
}

// ─── Your rules ──────────────────────────────────────────────────────────────

/** Every rule change re-checks all contracts on the server, so flag and data-quality counters must refetch. */
function announceRulesChanged(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(FLAGS_CHANGED_EVENT));
  window.dispatchEvent(new Event(FIELDS_CHANGED_EVENT));
}

export function fetchPolicyRules(): Promise<PolicyRulesResponse> {
  return request("/policy-rules", undefined, "Failed to load your rules");
}

/** The server's 422 detail is user-safe text: show it as is. */
export async function createPolicyRule(body: PolicyRuleBody): Promise<PolicyRulesResponse> {
  const res = await request<PolicyRulesResponse>("/policy-rules", jsonInit("POST", body), "Failed to save the rule");
  announceRulesChanged();
  return res;
}

export async function updatePolicyRule(id: string, body: PolicyRuleBody): Promise<PolicyRulesResponse> {
  const res = await request<PolicyRulesResponse>(
    `/policy-rules/${encodeURIComponent(id)}`,
    jsonInit("PUT", body),
    "Failed to save the rule",
  );
  announceRulesChanged();
  return res;
}

export async function deletePolicyRule(id: string): Promise<void> {
  await request<void>(`/policy-rules/${encodeURIComponent(id)}`, { method: "DELETE" }, "Failed to delete the rule");
  announceRulesChanged();
}
