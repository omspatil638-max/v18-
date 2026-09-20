import { AlertTriangle, BellRing, CalendarClock, CheckCircle2, Repeat } from "lucide-react";
import { recurrenceLabel } from "@/lib/format";
import type { DeadlineType, DueRuleType, Recurrence, VerificationStatus } from "@/lib/types";

const base = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap";

/** true when a date derives from data that has not been verified against the document. null = contract-level date, treated as fine. */
export function isUnverified(status: VerificationStatus | null | undefined): boolean {
  return status != null && status !== "verified";
}

export function UnverifiedBadge({ dark = false }: { dark?: boolean }) {
  return (
    <span
      title="This date comes from an item the extraction could not fully verify against the document. Check the source before relying on it."
      className={`${base} ${
        dark ? "bg-amber-950/60 text-amber-300 border-amber-700/70" : "bg-amber-100 text-amber-800 border-amber-200"
      }`}
    >
      <AlertTriangle className="w-3 h-3 shrink-0" aria-hidden="true" />
      Unverified
    </span>
  );
}

export function OverdueBadge({ dark = false, label = "Overdue" }: { dark?: boolean; label?: string }) {
  return (
    <span
      className={`${base} ${
        dark ? "bg-red-950/70 text-red-200 border-red-700" : "bg-red-100 text-red-800 border-red-300"
      }`}
    >
      <AlertTriangle className="w-3 h-3 shrink-0" aria-hidden="true" />
      {label}
    </span>
  );
}

export function CompletedBadge({ dark = false }: { dark?: boolean }) {
  return (
    <span
      className={`${base} ${
        dark ? "bg-emerald-950/60 text-emerald-300 border-emerald-800" : "bg-green-100 text-green-800 border-green-200"
      }`}
    >
      <CheckCircle2 className="w-3 h-3 shrink-0" aria-hidden="true" />
      Completed
    </span>
  );
}

/** How the date came about: stated in the contract, computed from a rule, or recurring. */
export function RuleTypeBadge({
  ruleType,
  recurrence,
  dark = false,
}: {
  ruleType: DueRuleType | null | undefined;
  recurrence?: Recurrence | null;
  dark?: boolean;
}) {
  if (!ruleType || ruleType === "none") return null;
  const neutral = dark ? "bg-slate-800 text-slate-300 border-slate-700" : "bg-slate-100 text-slate-700 border-slate-200";
  const blue = dark ? "bg-blue-950/60 text-blue-300 border-blue-800" : "bg-blue-50 text-blue-800 border-blue-200";
  const purple = dark ? "bg-purple-950/60 text-purple-300 border-purple-800" : "bg-purple-50 text-purple-800 border-purple-200";

  if (ruleType === "fixed") {
    return (
      <span title="This exact date is written in the contract." className={`${base} ${neutral}`}>
        Stated date
      </span>
    );
  }
  if (ruleType === "relative") {
    return (
      <span title="Worked out from a rule in the contract (for example, 30 days after another date). See the basis." className={`${base} ${blue}`}>
        <CalendarClock className="w-3 h-3 shrink-0" aria-hidden="true" />
        Computed
      </span>
    );
  }
  const rec = recurrenceLabel(recurrence);
  return (
    <span title="This obligation repeats. Each occurrence is calculated from the recurrence rule." className={`${base} ${purple}`}>
      <Repeat className="w-3 h-3 shrink-0" aria-hidden="true" />
      {rec ? `Recurring · ${rec}` : "Recurring"}
    </span>
  );
}

const TYPE_LABEL: Partial<Record<DeadlineType, string>> = {
  EXPIRY: "Contract expiry",
  RENEWAL_NOTICE: "Renewal notice deadline",
  PAYMENT: "Payment",
  OBLIGATION: "Obligation",
  OTHER: "Other",
};

export function deadlineTypeLabel(type: DeadlineType | string): string {
  return TYPE_LABEL[type as DeadlineType] ?? type.replace(/_/g, " ").toLowerCase();
}

/** Renewal notice deadlines are visually distinct: missing one can trigger an automatic renewal. */
export function DeadlineTypeBadge({ type }: { type: DeadlineType | string }) {
  const renewal = type === "RENEWAL_NOTICE";
  const expiry = type === "EXPIRY";
  const cls = renewal
    ? "bg-indigo-100 text-indigo-900 border-indigo-300"
    : expiry
      ? "bg-slate-200 text-slate-800 border-slate-300"
      : "bg-slate-100 text-slate-700 border-slate-200";
  return (
    <span className={`${base} ${cls}`}>
      {renewal && <BellRing className="w-3 h-3 shrink-0" aria-hidden="true" />}
      {deadlineTypeLabel(type)}
    </span>
  );
}
