"use client";

import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

interface ToastProps {
  message: string;
  /** Label of the optional action button (for example "Undo"). */
  actionLabel?: string;
  onAction?: () => void;
  onDismiss: () => void;
  /** Auto-dismiss delay. The timer pauses while the pointer or keyboard focus is on the toast. */
  durationMs?: number;
}

/** A small bottom-centre notice with an optional action, announced politely to screen readers. */
export function Toast({ message, actionLabel, onAction, onDismiss, durationMs = 10000 }: ToastProps) {
  const [paused, setPaused] = useState(false);
  const dismissRef = useRef(onDismiss);
  dismissRef.current = onDismiss;

  useEffect(() => {
    if (paused) return;
    const timer = window.setTimeout(() => dismissRef.current(), durationMs);
    return () => window.clearTimeout(timer);
  }, [paused, durationMs, message]);

  return (
    <div className="fixed inset-x-0 bottom-4 z-50 flex justify-center px-4 pointer-events-none" data-print-hide>
      <div
        role="status"
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
        onFocus={() => setPaused(true)}
        onBlur={() => setPaused(false)}
        className="pointer-events-auto max-w-lg w-full sm:w-auto flex items-center gap-3 pl-4 pr-2 py-2.5 rounded-2xl bg-slate-900 text-white text-sm shadow-2xl border border-slate-700"
      >
        <span className="min-w-0 flex-1 break-words">{message}</span>
        {actionLabel && onAction && (
          <button
            type="button"
            onClick={onAction}
            className="shrink-0 px-3 py-1.5 rounded-lg text-xs font-bold text-blue-300 hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
          >
            {actionLabel}
          </button>
        )}
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
        >
          <X className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
