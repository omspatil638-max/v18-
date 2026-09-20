"use client";

import { useState } from "react";
import Link from "next/link";
import { CheckCircle2, RotateCcw, XCircle } from "lucide-react";
import { FlagCodeBadge, SeverityBadge } from "@/components/ui/FlagBadge";
import { SourceRef } from "@/components/ui/SourceRef";
import { errorMessage, updateFlagStatus } from "@/lib/api";
import { formatDate } from "@/lib/format";
import type { FlagStatus, ReviewFlag } from "@/lib/types";

interface ReviewFlagCardProps {
  flag: ReviewFlag;
  /** When given (queue view) the card shows the contract title as a link to the contract. */
  contractTitle?: string;
  versionLabel?: string;
  /** Called with the server's copy of the flag once a status change is saved. */
  onUpdated?: (flag: ReviewFlag) => void;
}

const STATUS_NOTE: Record<FlagStatus, string> = {
  OPEN: "Reopened",
  RESOLVED: "Marked resolved",
  DISMISSED: "Dismissed",
};

const buttonBase =
  "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-bold border transition-colors disabled:opacity-60 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";

export function ReviewFlagCard({ flag, contractTitle, versionLabel, onUpdated }: ReviewFlagCardProps) {
  // Optimistic status: shown immediately, rolled back if the save fails.
  const [optimistic, setOptimistic] = useState<FlagStatus | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const status = optimistic ?? flag.status;
  const open = status === "OPEN";

  async function change(next: FlagStatus) {
    if (saving) return;
    setSaving(true);
    setError(null);
    setNote(null);
    setOptimistic(next);
    try {
      const updated = await updateFlagStatus(flag.id, next);
      onUpdated?.(updated);
      setNote(`${STATUS_NOTE[next]}.`);
    } catch (e: unknown) {
      setError(errorMessage(e, "Could not update this flag. Nothing was changed."));
    } finally {
      setOptimistic(null);
      setSaving(false);
    }
  }

  const resolvedOn = !open ? formatDate(flag.resolved_at) : null;

  return (
    <article
      className={`rounded-2xl border p-4 space-y-3 ${
        open ? "bg-white border-slate-200 shadow-sm" : "bg-slate-50 border-slate-200 opacity-80"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <SeverityBadge severity={flag.severity} />
        <FlagCodeBadge code={flag.code} />
        {status === "RESOLVED" && (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-green-800">
            <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
            Resolved{resolvedOn ? ` on ${resolvedOn}` : ""}
          </span>
        )}
        {status === "DISMISSED" && (
          <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-slate-600">
            <XCircle className="w-3 h-3" aria-hidden="true" />
            Dismissed{resolvedOn ? ` on ${resolvedOn}` : ""}
          </span>
        )}
      </div>

      {contractTitle && (
        <p className="text-xs text-slate-500 min-w-0">
          Contract:{" "}
          <Link
            href={`/contracts/${flag.contract_id}`}
            className="font-semibold text-blue-700 hover:text-blue-900 break-words"
          >
            {contractTitle}
          </Link>
          {versionLabel && <span className="text-slate-400"> · {versionLabel}</span>}
        </p>
      )}

      <div className="space-y-1 min-w-0">
        <p className="text-sm text-slate-900 leading-relaxed break-words">{flag.reason}</p>
        {flag.target_label && (
          <p className="text-xs text-slate-500 break-words">
            About: <span className="font-semibold text-slate-700">{flag.target_label}</span>
          </p>
        )}
      </div>

      <SourceRef
        contractId={flag.contract_id}
        page={flag.source_page}
        section={flag.source_section}
        quote={flag.source_quote}
      />

      <div className="flex flex-wrap items-center gap-2 pt-1">
        {open ? (
          <>
            <button
              type="button"
              onClick={() => change("RESOLVED")}
              disabled={saving}
              aria-label={`Mark resolved: ${flag.reason}`}
              className={`${buttonBase} bg-green-600 border-green-600 text-white hover:bg-green-700`}
            >
              <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />
              Mark resolved
            </button>
            <button
              type="button"
              onClick={() => change("DISMISSED")}
              disabled={saving}
              aria-label={`Dismiss: ${flag.reason}`}
              className={`${buttonBase} bg-white border-slate-300 text-slate-700 hover:bg-slate-50`}
            >
              <XCircle className="w-3.5 h-3.5" aria-hidden="true" />
              Dismiss
            </button>
          </>
        ) : (
          <button
            type="button"
            onClick={() => change("OPEN")}
            disabled={saving}
            aria-label={`Reopen: ${flag.reason}`}
            className={`${buttonBase} bg-white border-slate-300 text-slate-700 hover:bg-slate-50`}
          >
            <RotateCcw className="w-3.5 h-3.5" aria-hidden="true" />
            Reopen
          </button>
        )}
      </div>

      {/* Announced to assistive tech when a status changes or a save fails. */}
      <div aria-live="polite" className={note ? "text-xs text-green-800" : "sr-only"}>
        {note}
      </div>
      {error && (
        <p role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-2 py-1">
          {error}
        </p>
      )}
    </article>
  );
}
