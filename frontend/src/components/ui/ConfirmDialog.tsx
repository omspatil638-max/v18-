"use client";

import { useEffect, useId, useRef, useState } from "react";
import { AlertTriangle } from "lucide-react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface ConfirmDialogProps {
  title: string;
  /** Explains what will happen. */
  children: React.ReactNode;
  confirmLabel: string;
  /** Shown on the confirm button while `busy`. */
  busyLabel?: string;
  cancelLabel?: string;
  /** Red styling for the confirm button. Defaults to true. */
  destructive?: boolean;
  /** True while the action runs: buttons are disabled and Escape / backdrop do not close the dialog. */
  busy?: boolean;
  /** Inline error from the last attempt (announced to assistive tech). */
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
  /** An optional second, more severe choice (for example "Delete permanently"). */
  secondary?: {
    label: string;
    busyLabel?: string;
    hint?: string;
    onClick: () => void;
  };
}

/**
 * Accessible modal confirmation. Focus starts on the safe (Cancel) button, Tab is kept inside,
 * Escape and a click on the backdrop cancel, and focus returns to the trigger on close.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  busyLabel,
  cancelLabel = "Cancel",
  destructive = true,
  busy = false,
  error = null,
  onConfirm,
  onCancel,
  secondary,
}: ConfirmDialogProps) {
  const uid = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const [clicked, setClicked] = useState<"primary" | "secondary">("primary");

  // Focus the safe button on open; give focus back to the trigger on close (if it still exists).
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    return () => {
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, []);

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      if (!busy) onCancel();
      return;
    }
    if (e.key !== "Tab") return;
    const nodes = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (nodes.length === 0) {
      e.preventDefault();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === dialogRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  const titleId = `${uid}-title`;
  const descId = `${uid}-desc`;
  const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        aria-busy={busy}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-md max-h-[90vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-slate-200 focus:outline-none"
      >
        <div className="flex items-start gap-3 p-5">
          {destructive && (
            <div className="w-9 h-9 rounded-full bg-red-100 text-red-700 flex items-center justify-center shrink-0">
              <AlertTriangle className="w-5 h-5" aria-hidden="true" />
            </div>
          )}
          <div className="min-w-0 space-y-1">
            <h2 id={titleId} className="text-base font-bold text-slate-900 break-words">
              {title}
            </h2>
            <div id={descId} className="text-sm text-slate-600 break-words">
              {children}
            </div>
          </div>
        </div>

        {error && (
          <p role="alert" className="mx-5 mb-4 text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </p>
        )}

        <div className="flex flex-col-reverse sm:flex-row sm:justify-end gap-2 px-5 pb-5">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            disabled={busy}
            className={`px-4 py-2 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50 ${focusRing}`}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={() => {
              setClicked("primary");
              onConfirm();
            }}
            disabled={busy}
            className={`px-4 py-2 text-xs font-bold rounded-xl text-white disabled:opacity-60 ${focusRing} ${
              destructive ? "bg-red-600 hover:bg-red-700" : "bg-blue-600 hover:bg-blue-700"
            }`}
          >
            {busy && clicked === "primary" && busyLabel ? busyLabel : confirmLabel}
          </button>
        </div>

        {secondary && (
          <div className="px-5 py-4 border-t border-slate-100 bg-slate-50/60 rounded-b-2xl space-y-2">
            {secondary.hint && <p className="text-xs text-slate-600">{secondary.hint}</p>}
            <button
              type="button"
              onClick={() => {
                setClicked("secondary");
                secondary.onClick();
              }}
              disabled={busy}
              className={`px-3 py-1.5 text-xs font-bold rounded-lg bg-white border border-red-300 text-red-800 hover:bg-red-50 disabled:opacity-60 ${focusRing}`}
            >
              {busy && clicked === "secondary" && secondary.busyLabel ? secondary.busyLabel : secondary.label}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
