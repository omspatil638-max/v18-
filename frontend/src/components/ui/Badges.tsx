import Link from "next/link";
import type { ClauseType, ContractStatus, ExtractionStatus, ObligationStatus } from "@/lib/types";

// ─── Status Badge ──────────────────────────────────────────────────────────────

const obligationStatusConfig: Record<
  ObligationStatus,
  { label: string; className: string }
> = {
  PENDING: {
    label: "Pending",
    className: "bg-amber-100 text-amber-800 border-amber-200",
  },
  COMPLETED: {
    label: "Completed",
    className: "bg-green-100 text-green-800 border-green-200",
  },
  OVERDUE: {
    label: "Overdue",
    className: "bg-red-100 text-red-800 border-red-200",
  },
};

export function ObligationStatusBadge({ status }: { status: ObligationStatus }) {
  const { label, className } = obligationStatusConfig[status];
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${className}`}
    >
      {label}
    </span>
  );
}

// ─── Contract Status Badge ─────────────────────────────────────────────────────

const contractStatusConfig: Record<
  ContractStatus,
  { label: string; className: string }
> = {
  PENDING: { label: "Pending", className: "bg-slate-100 text-slate-700 border-slate-200" },
  PROCESSING: { label: "Processing", className: "bg-blue-100 text-blue-700 border-blue-200" },
  READY: { label: "Ready", className: "bg-green-100 text-green-800 border-green-200" },
  FAILED: { label: "Failed", className: "bg-red-100 text-red-800 border-red-200" },
  UNSUPPORTED: { label: "Unsupported", className: "bg-orange-100 text-orange-800 border-orange-200" },
};

export function ContractStatusBadge({ status }: { status: ContractStatus }) {
  const { label, className } = contractStatusConfig[status];
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${className}`}
    >
      {status === "PROCESSING" && (
        <span className="mr-1.5 inline-block w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
      )}
      {label}
    </span>
  );
}

// ─── Extraction Status Chip (dashboard rows) ───────────────────────────────────

const extractionChipConfig: Partial<Record<ExtractionStatus, { label: string; title: string; className: string }>> = {
  complete: {
    label: "Extracted",
    title: "AI extraction completed. Each item still carries its own verification status.",
    className: "bg-green-100 text-green-800 border-green-200",
  },
  partial: {
    label: "Partial",
    title: "Extraction only partly succeeded. Open the contract for details.",
    className: "bg-amber-100 text-amber-800 border-amber-200",
  },
  unavailable: {
    label: "AI extraction unavailable",
    title: "The AI extraction did not run or failed. The document text is still stored and searchable.",
    className: "bg-red-100 text-red-800 border-red-200",
  },
  legacy: {
    label: "Not verified (re-run)",
    title: "Extracted by an older version without verification. Re-run extraction to verify it.",
    className: "bg-amber-100 text-amber-800 border-amber-200",
  },
};

export function ExtractionStatusChip({ status }: { status: ExtractionStatus }) {
  const cfg = extractionChipConfig[status];
  if (!cfg) return null;
  return (
    <span
      title={cfg.title}
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium border ${cfg.className}`}
    >
      {cfg.label}
    </span>
  );
}

// ─── Clause Type Badge ─────────────────────────────────────────────────────────

const clauseTypeColors: Record<ClauseType, string> = {
  PAYMENT: "bg-green-100 text-green-800",
  RENEWAL: "bg-blue-100 text-blue-800",
  TERMINATION: "bg-red-100 text-red-800",
  CONFIDENTIALITY: "bg-purple-100 text-purple-800",
  INDEMNITY: "bg-orange-100 text-orange-800",
  FORCE_MAJEURE: "bg-yellow-100 text-yellow-800",
  DATA_PROTECTION: "bg-teal-100 text-teal-800",
  LIABILITY: "bg-rose-100 text-rose-800",
  INTELLECTUAL_PROPERTY: "bg-indigo-100 text-indigo-800",
  OTHER: "bg-slate-100 text-slate-700",
};

export function ClauseTypeBadge({ type }: { type: ClauseType }) {
  return (
    <span
      className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-semibold ${clauseTypeColors[type] ?? clauseTypeColors.OTHER}`}
    >
      {type.replace(/_/g, " ")}
    </span>
  );
}

// ─── Contract Nav Tabs ─────────────────────────────────────────────────────────

interface ContractTabsProps {
  contractId: string;
  active: "overview" | "summary" | "obligations" | "timeline" | "chat" | "source" | "compare";
}

const tabs = [
  { key: "overview", label: "Overview", href: "" },
  { key: "summary", label: "Brief", href: "/summary" },
  { key: "obligations", label: "Commitments", href: "/obligations" },
  { key: "timeline", label: "Schedule", href: "/timeline" },
  { key: "chat", label: "Ask", href: "/chat" },
  { key: "source", label: "Evidence", href: "/source" },
  { key: "compare", label: "Changes", href: "/compare" },
] as const;

export function ContractTabs({ contractId, active }: ContractTabsProps) {
  return (
    <div data-print-hide className="border-b border-slate-200 bg-white px-2 sm:px-8">
      <nav aria-label="Contract sections" className="flex gap-0 -mb-px overflow-x-auto">
        {tabs.map(({ key, label, href }) => {
          const isActive = key === active;
          return (
            <Link
              key={key}
              href={`/contracts/${contractId}${href}`}
              aria-current={isActive ? "page" : undefined}
              className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
                isActive
                  ? "border-blue-600 text-blue-600"
                  : "border-transparent text-slate-500 hover:text-slate-800 hover:border-slate-300"
              }`}
            >
              {label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
