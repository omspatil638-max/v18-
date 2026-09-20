// Small formatting helpers. They never invent data: missing input yields null.

/** Parse an ISO date (YYYY-MM-DD or full ISO) without timezone drift. Returns null if unparseable. */
function parseDate(input: string | null | undefined): Date | null {
  if (!input) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(input);
  const d = m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(input);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** en-GB date, e.g. "5 Mar 2025". Returns null when there is no (valid) date. */
export function formatDate(input: string | null | undefined, month: "short" | "long" = "short"): string | null {
  const d = parseDate(input);
  if (!d) return null;
  return d.toLocaleDateString("en-GB", { day: "numeric", month, year: "numeric" });
}

/** "Page 3 · Section 4.2" from whichever parts are known. Returns null when the page is unknown. */
export function locationLabel(page: number | null | undefined, section: string | null | undefined): string | null {
  if (page == null) return null;
  return section ? `Page ${page} · ${section}` : `Page ${page}`;
}

// Quotes are clipped so links stay well under URL length limits (a long quote is still located by its start).
const MAX_QUOTE_IN_URL = 1200;

/** Clips an over-long quote, without leaving half of a surrogate pair at the cut. */
function clipQuote(q: string): string {
  if (q.length <= MAX_QUOTE_IN_URL) return q;
  let cut = q.slice(0, MAX_QUOTE_IN_URL);
  const last = cut.charCodeAt(cut.length - 1);
  if (last >= 0xd800 && last <= 0xdbff) cut = cut.slice(0, -1);
  return cut.trimEnd();
}

/** Link into the source viewer, pre-scrolled to a page and (optionally) highlighting a quote. */
export function sourceHref(
  contractId: string,
  page: number,
  quote?: string | null,
  versionId?: string | null,
): string {
  const params = new URLSearchParams();
  if (versionId) params.set("version", versionId);
  params.set("page", String(page));
  const q = quote?.trim();
  if (q) params.set("q", clipQuote(q));
  return `/contracts/${contractId}/source?${params.toString()}`;
}

// ─── Deadline helpers ─────────────────────────────────────────────────────────
// Dates are date-only strings; they are parsed by hand so no timezone can shift them.

const MONTH_NAMES = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];

/** "March 2026" from "2026-03-14". Returns null when the input is not a date. */
export function monthLabel(input: string | null | undefined): string | null {
  const m = input ? /^(\d{4})-(\d{2})-(\d{2})/.exec(input) : null;
  if (!m) return null;
  const name = MONTH_NAMES[Number(m[2]) - 1];
  return name ? `${name} ${m[1]}` : null;
}

/** Whole days from today (local calendar day) to the given date. Negative = in the past. Null if unparseable. */
export function daysFromToday(input: string | null | undefined, now: Date = new Date()): number | null {
  const m = input ? /^(\d{4})-(\d{2})-(\d{2})/.exec(input) : null;
  if (!m) return null;
  const target = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - today) / 86_400_000);
}

/** "Due today", "Due in 3 days", "Overdue by 2 days". */
export function dueInText(days: number): string {
  if (days === 0) return "Due today";
  const n = Math.abs(days);
  const unit = n === 1 ? "day" : "days";
  return days > 0 ? `Due in ${n} ${unit}` : `Overdue by ${n} ${unit}`;
}

const RECURRENCE_LABELS: Record<string, string> = {
  DAILY: "Daily",
  WEEKLY: "Weekly",
  BIWEEKLY: "Every 2 weeks",
  MONTHLY: "Monthly",
  QUARTERLY: "Quarterly",
  SEMIANNUALLY: "Semi-annually",
  ANNUALLY: "Annually",
};

/** "Quarterly" from "QUARTERLY"; unknown codes are shown readably rather than hidden. */
export function recurrenceLabel(code: string | null | undefined): string | null {
  if (!code) return null;
  const known = RECURRENCE_LABELS[code.toUpperCase()];
  if (known) return known;
  const readable = code.replace(/_/g, " ").toLowerCase();
  return readable.charAt(0).toUpperCase() + readable.slice(1);
}

// ─── Version helpers ──────────────────────────────────────────────────────────

/** "v2" or "v2 - renegotiated". Never repeats the number when the label already starts with it. */
export function versionName(v: { version_number: number; label: string | null }): string {
  const num = `v${v.version_number}`;
  const label = v.label?.trim();
  if (!label || label.toLowerCase() === num) return num;
  const lower = label.toLowerCase();
  if (lower.startsWith(`${num} `) || lower.startsWith(`${num}-`) || lower.startsWith(`${num}:`)) return label;
  return `${num} - ${label}`;
}

const VERSION_STATE_SUFFIX: Record<string, string> = {
  PENDING: "processing",
  PROCESSING: "processing",
  FAILED: "failed",
  UNSUPPORTED: "unsupported",
};

/** Text for a version <option>: "v2 - renegotiated (current)", plus its state when it is not ready. */
export function versionOptionLabel(
  v: { id: string; version_number: number; label: string | null; status: string },
  currentVersionId: string | null | undefined,
): string {
  const parts = [versionName(v)];
  if (currentVersionId && v.id === currentVersionId) parts.push("(current)");
  const suffix = VERSION_STATE_SUFFIX[v.status];
  if (suffix) parts.push(`[${suffix}]`);
  return parts.join(" ");
}

// ─── Shared helpers for the newer pages (additive) ────────────────────────────

/** "in 5 days", "today", "3 days ago" for a whole-day offset. */
export function relativeDays(days: number): string {
  if (days === 0) return "today";
  const n = Math.abs(days);
  const unit = n === 1 ? "day" : "days";
  return days > 0 ? `in ${n} ${unit}` : `${n} ${unit} ago`;
}

/** Local-calendar "YYYY-MM-DD" for a Date (never shifted by timezone). */
export function isoDate(d: Date): string {
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${mm}-${dd}`;
}
