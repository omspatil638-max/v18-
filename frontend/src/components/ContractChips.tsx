import { Tag } from "lucide-react";

const chipBase = "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border max-w-full";

/** The user's own contract type label (for example "NDA") and tags, as small chips. Renders nothing when both are empty. */
export function TypeAndTagChips({ contractType, tags }: { contractType: string | null | undefined; tags: string[] | undefined }) {
  const list = tags ?? [];
  if (!contractType && list.length === 0) return null;
  return (
    <>
      {contractType && (
        <span title="Contract type (set by you)" className={`${chipBase} bg-indigo-50 text-indigo-800 border-indigo-200`}>
          <span className="sr-only">Type: </span>
          <span className="break-all">{contractType}</span>
        </span>
      )}
      {list.map((t) => (
        <span key={t} className={`${chipBase} bg-slate-100 text-slate-700 border-slate-200`}>
          <Tag className="w-3 h-3 shrink-0" aria-hidden="true" />
          <span className="sr-only">Tag: </span>
          <span className="break-all">{t}</span>
        </span>
      ))}
    </>
  );
}
