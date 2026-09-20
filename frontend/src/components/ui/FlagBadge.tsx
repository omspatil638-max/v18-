import { AlertOctagon, AlertTriangle, Info } from "lucide-react";
import type { FlagCode, FlagSeverity } from "@/lib/types";

const severityConfig: Record<
  FlagSeverity,
  { label: string; className: string; Icon: typeof AlertTriangle }
> = {
  HIGH: { label: "High", className: "bg-red-100 text-red-800 border-red-200", Icon: AlertOctagon },
  MEDIUM: { label: "Medium", className: "bg-amber-100 text-amber-800 border-amber-200", Icon: AlertTriangle },
  LOW: { label: "Low", className: "bg-slate-100 text-slate-700 border-slate-200", Icon: Info },
};

export const SEVERITY_ORDER: FlagSeverity[] = ["HIGH", "MEDIUM", "LOW"];

export function severityLabel(severity: FlagSeverity): string {
  return (severityConfig[severity] ?? severityConfig.LOW).label;
}

/** Severity is shown with an icon and text, so colour is never the only signal. */
export function SeverityBadge({ severity }: { severity: FlagSeverity }) {
  const { label, className, Icon } = severityConfig[severity] ?? severityConfig.LOW;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap ${className}`}
    >
      <Icon className="w-3 h-3 shrink-0" aria-hidden="true" />
      {label} priority
    </span>
  );
}

const codeLabels: Record<FlagCode, string> = {
  LOW_CONFIDENCE: "Low confidence",
  QUOTE_NOT_FOUND: "Source not verified",
  MISSING_FIELD: "Missing information",
  DATE_CONFLICT: "Conflicting dates",
  VAGUE_WORDING: "Vague wording",
  AUTO_RENEWAL: "Auto-renewal",
  UNCAPPED_LIABILITY: "Liability",
  UNUSUAL_TERMINATION: "Termination terms",
  AMENDMENT_CHANGES_TERM: "Changed by amendment",
  EXTRACTION_INCOMPLETE: "Incomplete analysis",
  SCANNED_PAGES: "Scanned pages (OCR)",
  CONVERTED_DOCUMENT: "Converted from Word",
  POLICY_RULE: "Your rule",
};

/** Human label for a flag code. An unknown code degrades to a readable form instead of breaking. */
export function flagCodeLabel(code: string): string {
  if (code in codeLabels) return codeLabels[code as FlagCode];
  const words = code.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function FlagCodeBadge({ code }: { code: string }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold bg-slate-100 text-slate-700 border border-slate-200 whitespace-nowrap">
      {flagCodeLabel(code)}
    </span>
  );
}
