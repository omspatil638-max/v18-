export function ConfidenceBadge({ confidence }: { confidence: number | null | undefined }) {
  const known = typeof confidence === "number" && Number.isFinite(confidence);
  const text = known ? `${Math.round(confidence * 100)}%` : "—";
  return (
    <span
      title={
        known
          ? "Heuristic extraction confidence, not a calibrated probability"
          : "No confidence score is available for this item"
      }
      className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-mono font-semibold border bg-slate-50 text-slate-600 border-slate-200"
    >
      <span className="sr-only">Confidence: </span>
      {text}
    </span>
  );
}
