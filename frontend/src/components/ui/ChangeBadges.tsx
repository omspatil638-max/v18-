import { AlertTriangle, Check, Minus, PenLine, Plus } from "lucide-react";
import type { ChangeType } from "@/lib/types";

const base = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap";

const CONFIG: Record<ChangeType, { label: string; className: string; Icon: typeof Plus }> = {
  ADDED: { label: "Added", className: "bg-green-100 text-green-900 border-green-300", Icon: Plus },
  REMOVED: { label: "Removed", className: "bg-red-100 text-red-900 border-red-300", Icon: Minus },
  MODIFIED: { label: "Changed", className: "bg-amber-100 text-amber-900 border-amber-300", Icon: PenLine },
  UNCHANGED: { label: "Unchanged", className: "bg-slate-100 text-slate-700 border-slate-200", Icon: Check },
};

/** Text plus icon, so the change type never relies on colour alone. */
export function ChangeTypeBadge({ type }: { type: ChangeType }) {
  const cfg = CONFIG[type] ?? CONFIG.MODIFIED;
  return (
    <span className={`${base} ${cfg.className}`}>
      <cfg.Icon className="w-3 h-3 shrink-0" aria-hidden="true" />
      {cfg.label}
    </span>
  );
}

export function MaterialBadge() {
  return (
    <span
      title="Flagged as material: a change to a term that usually matters commercially or legally. Check it against the sources."
      className={`${base} bg-orange-100 text-orange-900 border-orange-300`}
    >
      <AlertTriangle className="w-3 h-3 shrink-0" aria-hidden="true" />
      Material
    </span>
  );
}
