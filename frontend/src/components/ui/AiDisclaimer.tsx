import { Info } from "lucide-react";

export function AiDisclaimer({ className = "" }: { className?: string }) {
  return (
    <div
      role="note"
      className={`flex items-start gap-2 px-4 py-2 bg-slate-100 border-slate-200 text-slate-600 text-[11px] leading-snug ${className}`}
    >
      <Info className="w-3.5 h-3.5 shrink-0 mt-px text-slate-500" aria-hidden="true" />
      <p>
        <span className="font-semibold text-slate-700">AI-assisted, not legal advice.</span> ContractLens reduces
        manual review effort and does not replace a qualified legal professional.
      </p>
    </div>
  );
}
