"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { BellRing, CheckCircle2, Loader2, Scale } from "lucide-react";
import { AiDisclaimer } from "@/components/ui/AiDisclaimer";
import { errorMessage, fetchAckToken, submitAckToken } from "@/lib/api";

type Phase =
  | { kind: "loading" }
  | { kind: "invalid" }
  | { kind: "error"; message: string }
  | { kind: "pending"; count: number }
  | { kind: "none" }
  | { kind: "done" };

const linkClass =
  "font-semibold text-blue-700 hover:text-blue-900 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";

/**
 * Public page opened from a reminder email. It needs no sign-in: it only calls the two token endpoints.
 * Reading (GET) never changes anything; only the button sends the POST.
 */
export default function AckPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAckToken(token)
      .then((info) => {
        if (cancelled) return;
        if (!info.valid) setPhase({ kind: "invalid" });
        else if (info.pending > 0) setPhase({ kind: "pending", count: info.pending });
        else setPhase({ kind: "none" });
      })
      .catch((e: unknown) => {
        if (!cancelled) setPhase({ kind: "error", message: errorMessage(e, "Could not check this link.") });
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function handleConfirm() {
    setSubmitting(true);
    setSubmitError(null);
    try {
      await submitAckToken(token);
      setPhase({ kind: "done" });
    } catch (e: unknown) {
      setSubmitError(errorMessage(e, "Could not mark the alerts as read. Please try again."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-full flex flex-col bg-slate-50">
      <AiDisclaimer className="border-b" />
      <div className="flex-1 flex items-center justify-center px-4 py-8">
        <div className="w-full max-w-md bg-white rounded-3xl border border-slate-200 shadow-sm p-6 space-y-5">
          <div className="flex items-center gap-2">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-500 shrink-0">
              <Scale className="w-4 h-4 text-white" aria-hidden="true" />
            </div>
            <span className="font-bold text-slate-900 tracking-tight">ContractLens</span>
          </div>

          <h1 className="text-xl font-bold text-slate-900">Deadline reminders</h1>

          {phase.kind === "loading" && (
            <p role="status" className="flex items-center gap-2 text-sm text-slate-600">
              <Loader2 className="w-4 h-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              Checking this link...
            </p>
          )}

          {phase.kind === "error" && (
            <div className="space-y-3">
              <p role="alert" className="text-sm text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                {phase.message}
              </p>
              <p className="text-sm text-slate-600">
                Reload this page to try again, or{" "}
                <Link href="/" className={linkClass}>
                  open the dashboard
                </Link>
                .
              </p>
            </div>
          )}

          {phase.kind === "invalid" && (
            <div role="status" className="space-y-3">
              <p className="text-sm text-slate-800">This link is no longer valid.</p>
              <p className="text-sm text-slate-600">
                <Link href="/" className={linkClass}>
                  Open the dashboard
                </Link>{" "}
                to see and mark your alerts as read.
              </p>
            </div>
          )}

          {phase.kind === "none" && (
            <div role="status" className="space-y-3">
              <p className="text-sm text-slate-800">Nothing to mark: everything is already read.</p>
              <p className="text-sm text-slate-600">
                <Link href="/" className={linkClass}>
                  Open the dashboard
                </Link>
              </p>
            </div>
          )}

          {phase.kind === "pending" && (
            <div className="space-y-4">
              <p className="flex items-start gap-2 text-sm text-slate-800">
                <BellRing className="w-4 h-4 shrink-0 mt-0.5 text-purple-600" aria-hidden="true" />
                <span>
                  {phase.count} deadline {phase.count === 1 ? "alert" : "alerts"} waiting.
                </span>
              </p>
              <p className="text-xs text-slate-500">
                Marking them as read stops further reminder emails and notifications for them.
              </p>
              <button
                type="button"
                onClick={handleConfirm}
                disabled={submitting}
                className="w-full inline-flex items-center justify-center gap-2 px-4 py-3 text-sm font-bold rounded-xl bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
              >
                {submitting ? "Marking..." : "Mark as read"}
              </button>
              {submitError && (
                <p role="alert" className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                  {submitError}
                </p>
              )}
            </div>
          )}

          {phase.kind === "done" && (
            <div role="status" className="space-y-3">
              <p className="flex items-start gap-2 text-sm text-slate-800">
                <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5 text-green-700" aria-hidden="true" />
                <span>Done. You won&apos;t get more reminders for these.</span>
              </p>
              <p className="text-sm text-slate-600">
                <Link href="/" className={linkClass}>
                  Open the dashboard
                </Link>
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
