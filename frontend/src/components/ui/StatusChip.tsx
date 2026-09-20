import { AlertTriangle, CheckCircle2, MinusCircle, XCircle } from "lucide-react";
import type { VerificationStatus } from "@/lib/types";

const config: Record<
  VerificationStatus,
  { label: string; title: string; className: string; Icon: typeof CheckCircle2 }
> = {
  verified: {
    label: "Verified",
    title:
      "The quoted text was found in the document and the extracted value checks out against it. Still review it before relying on it.",
    className: "bg-green-100 text-green-800 border-green-200",
    Icon: CheckCircle2,
  },
  needs_review: {
    label: "Needs review",
    title:
      "The value could not be fully confirmed against the document (for example the quote was not located, validation failed, or sources conflict). Check it manually.",
    className: "bg-amber-100 text-amber-800 border-amber-200",
    Icon: AlertTriangle,
  },
  not_found: {
    label: "Not found in document",
    title: "The extraction ran, but this item was not found in the document text.",
    className: "bg-slate-100 text-slate-600 border-slate-200",
    Icon: MinusCircle,
  },
  extraction_unavailable: {
    label: "Extraction unavailable",
    title: "AI extraction did not run or failed for this contract, so this item could not be extracted.",
    className: "bg-red-100 text-red-800 border-red-200",
    Icon: XCircle,
  },
};

export function StatusChip({ status }: { status: VerificationStatus }) {
  const cfg = config[status] ?? config.needs_review;
  const { label, title, className, Icon } = cfg;
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap ${className}`}
    >
      <Icon className="w-3 h-3 shrink-0" aria-hidden="true" />
      {label}
    </span>
  );
}
