"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { errorMessage, fetchContractStatus } from "@/lib/api";
import { isProcessing } from "@/lib/types";
import type { ContractStatus, ContractStatusInfo } from "@/lib/types";

const POLL_MS = 2000;
const POLL_ERROR_MS = 5000;

export function ProgressBar({ progress, label }: { progress: number; label: string }) {
  const pct = Math.max(0, Math.min(100, Math.round(progress)));
  return (
    <div
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={label}
      className="h-1.5 w-full rounded-full bg-slate-200 overflow-hidden"
    >
      <div className="h-full rounded-full bg-blue-600 transition-all duration-500" style={{ width: `${pct}%` }} />
    </div>
  );
}

interface ProcessingBannerProps {
  contractId: string;
  /** Poll this version's status instead of the current version's. */
  versionId?: string | null;
  status: ContractStatus;
  stage: string | null;
  progress: number;
  /** Called with every fresh status snapshot while polling. */
  onUpdate?: (info: ContractStatusInfo) => void;
  /** Called once when the contract leaves PENDING/PROCESSING. */
  onDone: (info: ContractStatusInfo) => void;
  /** Slim inline variant for list rows. */
  compact?: boolean;
}

/** Progress bar + stage text. Polls /status every 2s while the contract is PENDING/PROCESSING. */
export function ProcessingBanner({
  contractId,
  versionId,
  status,
  stage,
  progress,
  onUpdate,
  onDone,
  compact = false,
}: ProcessingBannerProps) {
  const [live, setLive] = useState<{ stage: string | null; progress: number }>({ stage, progress });
  const [pollError, setPollError] = useState<string | null>(null);
  const onDoneRef = useRef(onDone);
  const onUpdateRef = useRef(onUpdate);
  useEffect(() => {
    onDoneRef.current = onDone;
    onUpdateRef.current = onUpdate;
  }, [onDone, onUpdate]);

  const active = isProcessing(status);

  useEffect(() => {
    if (!active) return;
    // A fresh run (e.g. after re-run extraction) starts from the caller's values.
    setLive({ stage, progress });
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    async function poll() {
      try {
        const info = await fetchContractStatus(contractId, versionId);
        if (cancelled) return;
        setPollError(null);
        setLive({ stage: info.stage, progress: info.progress });
        onUpdateRef.current?.(info);
        if (!isProcessing(info.status)) {
          onDoneRef.current(info);
          return;
        }
        timer = setTimeout(poll, POLL_MS);
      } catch (e) {
        if (cancelled) return;
        setPollError(errorMessage(e, "Could not check progress."));
        timer = setTimeout(poll, POLL_ERROR_MS);
      }
    }

    timer = setTimeout(poll, POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
    // stage/progress only seed the display when polling (re)starts, so they are not dependencies.
  }, [contractId, versionId, active]);

  if (!active) return null;

  const stageText = live.stage || (status === "PENDING" ? "Queued" : "Processing");
  const label = `Processing progress: ${stageText}`;

  if (compact) {
    return (
      <div className="space-y-1 min-w-[10rem]" aria-live="polite">
        <ProgressBar progress={live.progress} label={label} />
        <p className="text-[11px] text-slate-500 flex items-center gap-1">
          <Loader2 className="w-3 h-3 animate-spin text-blue-500" aria-hidden="true" />
          {stageText} · {Math.round(live.progress)}%
        </p>
        {pollError && <p className="text-[11px] text-amber-700">Progress check failed: {pollError}</p>}
      </div>
    );
  }

  return (
    <div className="p-4 rounded-2xl bg-blue-50 border border-blue-200 space-y-2" aria-live="polite">
      <div className="flex items-center justify-between gap-4 text-sm">
        <span className="font-semibold text-blue-900 flex items-center gap-2">
          <Loader2 className="w-4 h-4 animate-spin text-blue-600" aria-hidden="true" />
          {stageText}
        </span>
        <span className="text-xs font-mono text-blue-700">{Math.round(live.progress)}%</span>
      </div>
      <ProgressBar progress={live.progress} label={label} />
      <p className="text-xs text-blue-800">
        The document is still being processed. Extracted data appears here when processing finishes.
      </p>
      {pollError && <p className="text-xs text-amber-700">Progress check failed: {pollError}. Retrying...</p>}
    </div>
  );
}
