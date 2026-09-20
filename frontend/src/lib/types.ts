// Core domain types for ContractLens frontend.
// These mirror backend/app/schemas/schemas.py. Nothing here is ever default-filled:
// a missing value is `null`, and the UI must show an honest "missing" state.

export type ContractStatus =
  | "PENDING"
  | "PROCESSING"
  | "READY"
  | "FAILED"
  | "UNSUPPORTED";

export type ExtractionStatus =
  | "pending"
  | "running"
  | "complete"
  | "partial"
  | "unavailable"
  | "unsupported"
  | "legacy";

/** Verification status shared by every extracted item. */
export type VerificationStatus =
  | "verified"
  | "needs_review"
  | "not_found"
  | "extraction_unavailable";

export type ClauseType =
  | "PAYMENT"
  | "RENEWAL"
  | "TERMINATION"
  | "CONFIDENTIALITY"
  | "INDEMNITY"
  | "FORCE_MAJEURE"
  | "DATA_PROTECTION"
  | "LIABILITY"
  | "INTELLECTUAL_PROPERTY"
  | "OTHER";

export type ObligationStatus = "PENDING" | "COMPLETED" | "OVERDUE";

export type DeadlineType =
  | "EXPIRY"
  | "RENEWAL_NOTICE"
  | "PAYMENT"
  | "OBLIGATION"
  | "OTHER";

export type DueRuleType = "fixed" | "relative" | "recurring" | "none";

/** Known recurrence codes; typed loosely so a new one still renders. */
export type Recurrence = "WEEKLY" | "MONTHLY" | "QUARTERLY" | "SEMIANNUALLY" | "ANNUALLY" | (string & {});

export type MessageRole = "USER" | "ASSISTANT";

export type AnswerStatus = "answered" | "not_found" | "unavailable";

export type FieldKey =
  | "effective_date"
  | "expiration_date"
  | "renewal_terms"
  | "auto_renew"
  | "renewal_notice_period"
  | "payment_terms"
  | "termination_conditions"
  | "termination_notice_period";

// ─── Auth / system ────────────────────────────────────────────────────────────

export type AuthMode = "demo" | "login";

export interface User {
  id: string;
  email: string;
  name: string;
  phone: string | null;
  email_verified: boolean;
  onboarded: boolean;
}

/** "setup" = nobody has signed up yet (the very first run). */
export type AuthConfigMode = AuthMode | "setup";

export interface AuthConfig {
  mode: AuthConfigMode;
  registration_open: boolean;
  /** The server can email a verification code. */
  email_verification_available?: boolean;
}

export type EmailVerificationState = "sent" | "unavailable" | "failed";

export interface SignupRequest {
  name: string;
  email: string;
  phone: string;
  password: string;
  accept_terms: boolean;
  timezone?: string;
}

export interface SignupResponse {
  user: User;
  /** True when the contracts already stored on this computer now belong to this account. */
  claimed_existing_data: boolean;
  email_verification: EmailVerificationState;
}

export interface ResendVerificationResponse {
  message: string;
  email_verification: EmailVerificationState;
}

export interface ProfileUpdate {
  name?: string;
  phone?: string;
}

export interface SystemStatus {
  llm_provider: string;
  llm_model: string;
  llm_configured: boolean;
  llm_reason: string | null;
  auth_mode: AuthMode;
  max_upload_mb: number;
  disclaimer: string;
}

// ─── Extracted items ──────────────────────────────────────────────────────────

export interface FieldConflict {
  value?: string | null;
  page?: number | null;
  section?: string | null;
  quote?: string | null;
}

export interface FieldMention {
  page?: number | null;
  section?: string | null;
}

export interface FieldNotes {
  validation?: string;
  /** Present when the value was read from a scanned page by OCR. */
  ocr?: string;
  conflicts?: FieldConflict[];
  other_mentions?: FieldMention[];
  reason?: string;
  approximate?: string;
  /** Present when the user has confirmed, corrected or dismissed this field. */
  user_review?: UserReview;
}

export type UserReviewAction = "confirmed" | "corrected" | "not_in_contract";

/** What the field looked like before the user reviewed it. */
export interface UserReviewOriginal {
  value: Record<string, unknown> | null;
  display_value: string | null;
  source_quote: string | null;
  page: number | null;
  section: string | null;
  confidence: number | null;
  status: VerificationStatus;
  notes: Record<string, unknown> | null;
}

export interface UserReviewHistoryEntry {
  at: string;
  action: string;
  from: string | null;
  to: string | null;
}

export interface UserReview {
  action: UserReviewAction;
  at: string;
  note: string | null;
  original?: UserReviewOriginal | null;
  history?: UserReviewHistoryEntry[];
}

export type FieldReviewAction = "confirm" | "correct" | "not_in_contract" | "revert";

export interface FieldReviewRequest {
  action: FieldReviewAction;
  value?: string | boolean;
  quote?: string;
  note?: string;
}

export interface ExtractedField {
  id: string;
  contract_version_id: string;
  field_key: FieldKey | string;
  item_index: number;
  value: Record<string, unknown> | null;
  display_value: string | null;
  source_quote: string | null;
  page: number | null;
  section: string | null;
  confidence: number | null;
  status: VerificationStatus;
  notes: FieldNotes | null;
}

export interface Party {
  id: string;
  contract_version_id: string;
  contract_id: string;
  role: string;
  name: string;
  source_page: number | null;
  source_section: string | null;
  source_quote: string | null;
  confidence: number | null;
  status: VerificationStatus;
}

export interface Clause {
  id: string;
  contract_version_id: string;
  contract_id: string;
  clause_type: ClauseType;
  title: string;
  content: string;
  source_page: number | null;
  source_section: string | null;
  source_quote: string | null;
  confidence: number | null;
  status: VerificationStatus;
}

export interface Obligation {
  id: string;
  contract_version_id: string;
  contract_id: string;
  responsible_party: string;
  action: string;
  /** The contract's own words for when this is due. */
  due_rule: string;
  /** Stated OR computed date (see due_computed). null when no date can be worked out. */
  due_date: string | null;
  /** true when due_date was calculated from a relative rule rather than stated in the contract. */
  due_computed: boolean;
  /** Plain-English explanation of how a computed date was worked out. */
  due_basis: string | null;
  /** Why no date could be calculated (when due_date is null). */
  due_reason: string | null;
  is_overdue: boolean;
  due_rule_type: DueRuleType | null;
  recurrence: Recurrence | null;
  /** Lifecycle status, user-toggled. */
  status: ObligationStatus;
  /** Extraction verification status. */
  verification_status: VerificationStatus;
  source_page: number | null;
  source_section: string | null;
  source_quote: string | null;
  confidence: number | null;
}

export interface Deadline {
  id: string;
  contract_version_id: string;
  contract_id: string;
  obligation_id: string | null;
  label: string;
  deadline_date: string;
  deadline_type: DeadlineType;
  created_at: string;
  source_page: number | null;
  source_section: string | null;
  source_quote: string | null;
  basis: string | null;
}

/** GET /deadlines: a deadline plus the context needed to show it outside its contract. */
export interface DeadlineWithContext extends Deadline {
  contract_title: string;
  /** Negative when overdue. */
  days_until: number;
  is_overdue: boolean;
  responsible_party: string | null;
  obligation_status: ObligationStatus | null;
  /** null for contract-level dates (expiry, renewal notice), which are treated as fine. */
  verification_status: VerificationStatus | null;
}

// ─── Schedule (obligations timeline) ──────────────────────────────────────────

export type ScheduleEntryKind = "contract_date" | "renewal_notice" | "obligation" | "occurrence";

export interface ScheduleEntry {
  id: string;
  kind: ScheduleEntryKind;
  due_on: string;
  label: string;
  /** null for contract-level dates. */
  party: string | null;
  action: string | null;
  rule_type: DueRuleType | null;
  recurrence: Recurrence | null;
  /** Plain-English how the date was derived. */
  basis: string | null;
  is_overdue: boolean;
  status: ObligationStatus | null;
  verification_status: VerificationStatus | null;
  obligation_id: string | null;
  deadline_id: string | null;
  source_page: number | null;
  source_section: string | null;
  source_quote: string | null;
}

export interface UndatedObligation {
  obligation_id: string;
  party: string;
  action: string;
  /** The contract's own words. */
  due_rule: string;
  reason: string | null;
  source_page: number | null;
  source_section: string | null;
  source_quote: string | null;
  verification_status: VerificationStatus | null;
}

export interface ScheduleResponse {
  contract_version_id: string;
  entries: ScheduleEntry[];
  undated: UndatedObligation[];
}

// ─── Contracts ────────────────────────────────────────────────────────────────

export type DocumentKind = "MAIN" | "AMENDMENT" | "SOW" | "EXHIBIT" | "OTHER";
export type FlagSeverity = "HIGH" | "MEDIUM" | "LOW";
export type FlagStatus = "OPEN" | "RESOLVED" | "DISMISSED";
export type FlagCode =
  | "LOW_CONFIDENCE"
  | "QUOTE_NOT_FOUND"
  | "MISSING_FIELD"
  | "DATE_CONFLICT"
  | "VAGUE_WORDING"
  | "AUTO_RENEWAL"
  | "UNCAPPED_LIABILITY"
  | "UNUSUAL_TERMINATION"
  | "AMENDMENT_CHANGES_TERM"
  | "EXTRACTION_INCOMPLETE"
  | "SCANNED_PAGES"
  | "CONVERTED_DOCUMENT"
  | "POLICY_RULE";
export type FlagTargetType = "field" | "party" | "clause" | "obligation" | "document";

export interface VersionSummary {
  id: string;
  contract_id: string;
  version_number: number;
  label: string;
  status: ContractStatus;
  stage: string | null;
  progress: number;
  extraction_status: ExtractionStatus;
  extraction_error: string | null;
  effective_date: string | null;
  expiry_date: string | null;
  page_count: number;
  created_at: string;
  processed_at: string | null;
  filename: string | null;
}

export interface ContractDocument {
  id: string;
  contract_id: string;
  version_id: string | null;
  kind: DocumentKind;
  filename: string;
  page_count: number;
  size_bytes: number;
  created_at: string;
}

export interface ReviewFlag {
  id: string;
  contract_id: string;
  contract_version_id: string;
  /** One of FlagCode; typed loosely so an unknown future code still renders. */
  code: FlagCode | string;
  severity: FlagSeverity;
  reason: string;
  target_type: FlagTargetType | null;
  target_id: string | null;
  target_label: string | null;
  source_page: number | null;
  source_section: string | null;
  source_quote: string | null;
  status: FlagStatus;
  created_at: string;
  resolved_at?: string | null;
}

/** A flag in the cross-contract review queue (GET /flags). */
export interface QueueFlag extends ReviewFlag {
  contract_title: string;
  version_label: string;
}

/** GET /flags/summary */
export interface FlagSummary {
  open: number;
  high: number;
  medium: number;
  low: number;
}

export interface FlagQueueFilters {
  /** Defaults to OPEN on the server when omitted. */
  status?: FlagStatus | "ALL";
  severity?: FlagSeverity;
  contractId?: string;
}

export interface ContractSummary {
  id: string;
  title: string;
  counterparty: string | null;
  filename: string;
  current_version_id: string | null;
  version_count: number;
  deleted_at: string | null;
  status: ContractStatus;
  stage: string | null;
  progress: number;
  extraction_status: ExtractionStatus;
  extraction_error: string | null;
  effective_date: string | null;
  expiry_date: string | null;
  renewal_terms: string | null;
  payment_terms: string | null;
  termination_conditions: string | null;
  page_count: number;
  created_at: string;
  processed_at: string | null;
  parties: Party[];
  /** Review-flag counts for the current version. Treat undefined as 0. */
  open_flag_count?: number;
  high_flag_count?: number;
  /** User-set label such as "NDA". null when not set. */
  contract_type: string | null;
  tags: string[];
}

/** PATCH /contracts/{id}. Send only what changes; contract_type "" clears it. */
export interface ContractUpdate {
  title?: string;
  counterparty?: string | null;
  contract_type?: string;
  tags?: string[];
}

/** How the text was obtained when it did not come straight from a text PDF. */
export interface OcrSource {
  kind: "ocr";
  engine: string;
  pages: number[];
  skipped_pages: number[];
  empty_pages: number[];
  avg_confidence: number | null;
  low_confidence_pages: number[];
  demoted?: number;
}

export interface DocxSource {
  kind: "docx";
  converted: true;
  notes: string[];
  demoted?: number;
}

export type ExtractionSource = OcrSource | DocxSource;

/** Known extraction_meta keys; others may exist. */
export type ExtractionMeta = Record<string, unknown> & { source?: ExtractionSource };

export interface ContractDetail extends ContractSummary {
  extraction_meta: ExtractionMeta | null;
  versions: VersionSummary[];
  documents: ContractDocument[];
  flags: ReviewFlag[];
  fields: ExtractedField[];
  clauses: Clause[];
  obligations: Obligation[];
  deadlines: Deadline[];
}

export interface ContractStatusInfo {
  id: string;
  current_version_id: string | null;
  status: ContractStatus;
  stage: string | null;
  progress: number;
  extraction_status: ExtractionStatus;
  extraction_error: string | null;
}

export interface PageText {
  page: number;
  text: string;
}

export interface ContractText {
  contract_version_id: string;
  page_count: number;
  pages: PageText[];
}

// ─── Version comparison ───────────────────────────────────────────────────────

export type ChangeType = "UNCHANGED" | "MODIFIED" | "ADDED" | "REMOVED";

/** Key-term categories are computed in code; "section" is a clause-level text change. Typed loosely so a new one still renders. */
export type ChangeCategory =
  | "termination_notice_period"
  | "auto_renew"
  | "payment_terms"
  | "effective_date"
  | "expiration_date"
  | "renewal_notice_period"
  | "renewal_terms"
  | "termination_conditions"
  | "party"
  | "section"
  | (string & {});

export interface VersionChange {
  id: string;
  contract_id: string;
  from_version_id: string;
  to_version_id: string;
  change_type: ChangeType;
  category: ChangeCategory;
  label: string;
  old_value: string | null;
  new_value: string | null;
  old_quote: string | null;
  new_quote: string | null;
  old_page: number | null;
  new_page: number | null;
  old_section: string | null;
  new_section: string | null;
  is_material: boolean;
  /** AI-written explanation of the impact. May be missing; the UI must say so rather than invent one. */
  impact_text: string | null;
}

export type ComparisonStatus = "ready" | "running" | "not_run" | "versions_not_ready";
export type ImpactStatus = "complete" | "partial" | "unavailable" | "none";

export interface ComparisonSummary {
  added: number;
  removed: number;
  modified: number;
  unchanged: number;
  material: number;
  key_changes: number;
}

export interface ComparisonResponse {
  contract_id: string;
  from_version: VersionSummary;
  to_version: VersionSummary;
  status: ComparisonStatus;
  impact_status: ImpactStatus;
  notes: string[];
  summary: ComparisonSummary;
  changes: VersionChange[];
}

export type DiffOp = "equal" | "delete" | "insert";

export interface DiffSegment {
  op: DiffOp;
  text: string;
}

export interface AlignedSide {
  label: string | null;
  page: number | null;
  text: string;
}

export interface AlignedSection {
  change_type: ChangeType;
  old: AlignedSide | null;
  new: AlignedSide | null;
  similarity: number | null;
  /** Word-level diff; populated only for MODIFIED sections. */
  diff: DiffSegment[];
  is_material: boolean;
}

export interface AlignedSections {
  from_version_id: string;
  to_version_id: string;
  sections: AlignedSection[];
}

// ─── Reminders ────────────────────────────────────────────────────────────────

export interface Reminder {
  id: string;
  deadline_id: string;
  contract_id: string;
  contract_title: string;
  remind_at: string;
  acknowledged: boolean;
  message: string;
  deadline_type: DeadlineType;
  days_until: number;
  /** Alerts (replacing reminders): lead time, due date and lifecycle. */
  lead_days?: number;
  fire_on?: string;
  deadline_date?: string;
  status?: "PENDING" | "SENT" | "ACKNOWLEDGED" | "DISMISSED";
}

// ─── Alerts ───────────────────────────────────────────────────────────────────

export type AlertStatus = "PENDING" | "SENT" | "ACKNOWLEDGED" | "DISMISSED";

export interface Alert {
  id: string;
  contract_id: string;
  deadline_id: string;
  contract_title: string;
  lead_days: number;
  fire_on: string;
  status: AlertStatus;
  message: string;
  deadline_type: DeadlineType;
  deadline_date: string;
  /** Negative when overdue. */
  days_until: number;
  remind_at: string;
  acknowledged: boolean;
  /** How many reminder emails have been sent for this alert. Absent on older servers. */
  email_count?: number;
  /** ISO timestamp of the last email, or null when none was sent. */
  emailed_at?: string | null;
}

export interface AlertSettings {
  lead_days: number[];
  default_lead_days: number[];
  email_to: string | null;
  email_enabled: boolean;
  smtp_configured: boolean;
  /** Re-send due alerts until they are marked read. */
  repeat_enabled: boolean;
  /** Hours between reminders (1-168). */
  repeat_hours: number;
  /** Read-only: the most reminders sent per alert before the server stops. */
  max_repeats: number;
  push_enabled: boolean;
  /** Read-only: the server has push keys configured. */
  push_available: boolean;
  /** Read-only: how many of this user's browsers are registered for push. */
  push_subscriptions: number;
  /** One email a morning, only when something needs attention. */
  briefing_enabled: boolean;
  /** Local hour of day (0-23) in `timezone`. */
  briefing_hour: number;
  /** IANA time zone name. */
  timezone: string;
  /** All notifications are paused until this date (YYYY-MM-DD), or null. */
  paused_until: string | null;
  /** Read-only: the account email address. */
  account_email: string | null;
  /** Read-only: the account email has been confirmed with a code. */
  email_verified: boolean;
}

export interface AlertSettingsUpdate {
  lead_days?: number[];
  email_to?: string;
  email_enabled?: boolean;
  repeat_enabled?: boolean;
  repeat_hours?: number;
  push_enabled?: boolean;
  briefing_enabled?: boolean;
  briefing_hour?: number;
  timezone?: string;
  /** 0 resumes; 1 to 90 pauses every notification for that many days. */
  pause_days?: number;
}

export interface BriefingPreviewItem {
  title: string;
  label: string;
  deadline_date: string | null;
  days_left: number | null;
  source_page: number | null;
}

/** What the morning briefing would say right now. Fetching it sends nothing. */
export interface BriefingPreview {
  subject: string | null;
  body: string;
  count: number;
  would_send: boolean;
  reason: string | null;
  items: BriefingPreviewItem[];
}

/** Public one-click page (opened from an email link): what the token refers to. */
export interface AckTokenInfo {
  valid: boolean;
  pending: number;
}

export interface PushSubscriptionPayload {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

export interface Citation {
  chunk_id: string | null;
  source_page: number;
  source_section: string;
  snippet: string;
  verified: boolean;
}

export interface ChatMessage {
  id: string;
  contract_version_id?: string | null;
  role: MessageRole;
  content: string;
  citations?: Citation[] | null;
  answer_status?: AnswerStatus | null;
  created_at: string;
}

export function isProcessing(status: ContractStatus): boolean {
  return status === "PENDING" || status === "PROCESSING";
}

// ─── PDF viewer highlights ────────────────────────────────────────────────────

/** A rectangle on a page, normalised 0..1 of the page size (top-left origin). */
export interface HighlightRect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface HighlightResponse {
  page: number;
  page_count: number;
  /** Page size in PDF points. */
  width: number;
  height: number;
  /** False (with no rects) when the quote is not on this page. */
  matched: boolean;
  /** One rect per text line of the match. */
  rects: HighlightRect[];
}

// ─── Stakeholder summary ──────────────────────────────────────────────────────

export type SummaryStatus = "ready" | "partial" | "unavailable";
export type SummarySectionKey = "parties" | "term" | "money" | "dates" | "obligations" | "review";
export type SummaryLineKind = "party" | "field" | "deadline" | "obligation" | "flag";

export interface SummaryLine {
  text: string;
  status: VerificationStatus;
  page: number | null;
  section: string | null;
  quote: string | null;
  kind: SummaryLineKind;
  ref_id: string | null;
  severity: FlagSeverity | null;
}

export interface SummarySection {
  key: SummarySectionKey;
  title: string;
  lines: SummaryLine[];
}

export interface SummaryResponse {
  contract_id: string;
  contract_version_id: string;
  title: string;
  version_label: string | null;
  status: SummaryStatus;
  overview: string | null;
  overview_status: "ai" | "unavailable";
  notes: string[];
  sections: SummarySection[];
  word_count: number;
  disclaimer: string;
  generated_at: string;
}

// ─── Data quality ─────────────────────────────────────────────────────────────

export type QualitySeverity = "high" | "medium" | "low";

export interface QualityCoverage {
  field_key: string;
  label: string;
  have: number;
  total: number;
}

export interface QualityContractRef {
  contract_id: string;
  title: string;
  detail: string;
  field_key: string | null;
}

export interface QualityIssue {
  code: string;
  severity: QualitySeverity;
  title: string;
  why: string;
  action: string;
  count: number;
  contracts: QualityContractRef[];
}

export interface DataQualityReport {
  generated_at: string;
  contracts_total: number;
  processing: number;
  analysed: number;
  open_issues: number;
  coverage: QualityCoverage[];
  issues: QualityIssue[];
}

// ─── Renewal radar ────────────────────────────────────────────────────────────

export type RenewalKind = "give_notice_by" | "expires" | "notice_missed" | "expired";
export type RenewalBucket = "30" | "60" | "90" | "later" | "overdue" | "notice_missed" | "expired";

export interface RenewalSummary {
  due_30: number;
  due_60: number;
  due_90: number;
  auto_renewing: number;
  notice_missed: number;
  expired_recently: number;
}

export interface RenewalSource {
  status: VerificationStatus;
  page: number | null;
  section: string | null;
  quote: string | null;
}

export interface RenewalItem {
  contract_id: string;
  title: string;
  counterparty: string | null;
  contract_type: string | null;
  tags: string[];
  kind: RenewalKind;
  action_date: string;
  days_to_action: number;
  bucket: RenewalBucket;
  expiry_date: string | null;
  days_to_expiry: number | null;
  auto_renew: boolean | null;
  notice_period: string | null;
  notice_deadline: string | null;
  notice_basis: string | null;
  what_happens: string;
  /** Exactly as written in the contract. Never summed. */
  amounts: string[];
  sources: {
    expiry: RenewalSource | null;
    auto_renew: RenewalSource | null;
    notice: RenewalSource | null;
  };
  /** Field keys whose value has not been verified against the document. */
  unverified: string[];
  open_flags: number;
}

export interface RenewalNotTracked {
  contract_id: string;
  title: string;
  reason: string;
}

export interface RenewalsResponse {
  generated_at: string;
  window_days: number;
  summary: RenewalSummary;
  items: RenewalItem[];
  not_tracked: RenewalNotTracked[];
}

// ─── Search / tags ────────────────────────────────────────────────────────────

export interface SearchContractHit {
  contract_id: string;
  title: string;
  detail: string | null;
  contract_type: string | null;
  tags: string[];
  expiry_date: string | null;
}

export interface SearchFieldHit {
  contract_id: string;
  title: string;
  label: string;
  value: string;
  status: VerificationStatus;
  page: number | null;
  quote: string | null;
}

export interface SearchPassageHit {
  contract_id: string;
  title: string;
  page: number;
  section: string | null;
  /** Plain text; matched words are wrapped in U+00AB / U+00BB. Never HTML. */
  snippet: string;
  quote: string | null;
}

export interface SearchResponse {
  query: string;
  contracts: SearchContractHit[];
  fields: SearchFieldHit[];
  passages: SearchPassageHit[];
}

export interface NameCount {
  name: string;
  count: number;
}

// ─── Ask your contracts ───────────────────────────────────────────────────────

export type AskStatus = "answered" | "no_match" | "passages" | "not_understood" | "unavailable_ai";
export type AskInterpretedBy = "rules" | "ai" | "keyword" | "none";
export type AskFactStatus = "verified" | "needs_review" | "flag" | "user" | "party" | "not_found";

export interface AskFact {
  label: string;
  value: string;
  status: AskFactStatus;
  page: number | null;
  quote: string | null;
  reviewed: boolean;
}

export interface AskMatch {
  contract_id: string;
  title: string;
  counterparty: string | null;
  contract_type: string | null;
  tags: string[];
  facts: AskFact[];
}

export interface AskPassage {
  contract_id: string;
  title: string;
  page: number;
  section: string | null;
  /** Plain text; matched words are wrapped in U+00AB / U+00BB. Never HTML. */
  snippet: string;
  quote: string | null;
}

export interface AskResponse {
  question: string;
  status: AskStatus;
  interpreted_by: AskInterpretedBy;
  interpretation: string[];
  summary: string;
  total_contracts: number;
  matches: AskMatch[];
  passages: AskPassage[];
  examples: string[];
}

// ─── Your rules (policy rules) ────────────────────────────────────────────────

export type PolicyOperator = "gt" | "gte" | "lt" | "lte" | "eq" | "is_true" | "is_false" | "missing";
export type PolicyFieldKind = "number" | "bool" | "missing";

export interface PolicyRule {
  id: string;
  name: string;
  /** A catalog key. */
  field: string;
  operator: PolicyOperator;
  value: number | null;
  /** For kind "missing": a key of `missing_targets`. */
  target: string | null;
  severity: FlagSeverity;
  message: string | null;
  enabled: boolean;
  created_at: string;
}

export interface PolicyCatalogItem {
  key: string;
  label: string;
  kind: PolicyFieldKind;
  unit: string | null;
  operators: PolicyOperator[];
}

export interface PolicyRulesResponse {
  rules: PolicyRule[];
  catalog: PolicyCatalogItem[];
  operator_labels: Record<string, string>;
  missing_targets: Record<string, string>;
  max_rules: number;
}

export interface PolicyRuleBody {
  name: string;
  field: string;
  operator: PolicyOperator;
  value?: number | null;
  target?: string | null;
  severity: FlagSeverity;
  message?: string | null;
  enabled: boolean;
}
