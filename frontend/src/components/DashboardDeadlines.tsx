"use client";

import Link from "next/link";
import { BellRing, Check, CheckCheck, Download } from "lucide-react";
import { DeadlineTypeBadge, OverdueBadge, UnverifiedBadge, isUnverified } from "@/components/ui/DateBadges";
import { calendarUrl } from "@/lib/api";
import { dueInText, formatDate, locationLabel, sourceHref } from "@/lib/format";
import type { Alert, DeadlineWithContext } from "@/lib/types";

export const DEADLINE_WINDOWS = [30, 60, 90] as const;
export type DeadlineWindow = (typeof DEADLINE_WINDOWS)[number];

// ─── Attention now ────────────────────────────────────────────────────────────

interface AttentionProps {
  alerts: Alert[];
  loading: boolean;
  error: string | null;
  /** Shown when there are no alerts and nothing is dated in the loaded window. */
  nothingDated: boolean;
  ackError: string | null;
  announce: string;
  onAcknowledge: (id: string) => void;
  onAcknowledgeAll: () => void;
  ackAllBusy: boolean;
}

export function AttentionNow({
  alerts,
  loading,
  error,
  nothingDated,
  ackError,
  announce,
  onAcknowledge,
  onAcknowledgeAll,
  ackAllBusy,
}: AttentionProps) {
  return (
    <section aria-labelledby="attention-heading" className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 pb-3 border-b border-slate-100">
        <h2 id="attention-heading" className="text-xs font-bold text-slate-500 uppercase tracking-wider flex items-center gap-2">
          <BellRing className="w-4 h-4 text-purple-600" aria-hidden="true" />
          Attention now{!loading && !error ? ` (${alerts.length})` : ""}
        </h2>
        {!loading && !error && alerts.length > 1 && (
          <button
            type="button"
            onClick={onAcknowledgeAll}
            disabled={ackAllBusy}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-100 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            <CheckCheck className="w-3.5 h-3.5" aria-hidden="true" />
            {ackAllBusy ? "Marking..." : "Mark all as read"}
          </button>
        )}
      </div>

      {ackError && (
        <p role="alert" className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          {ackError}
        </p>
      )}

      {loading ? (
        <p role="status" className="p-4 text-center text-xs text-slate-400">Loading alerts...</p>
      ) : error ? (
        <p role="alert" className="p-4 text-center text-xs text-red-700">{error}</p>
      ) : alerts.length === 0 ? (
        <div className="space-y-1 p-2 text-center">
          <p className="text-sm text-slate-700">Nothing needs attention right now.</p>
          {nothingDated && (
            <p className="text-xs text-slate-500">
              No contract has a dated deadline in the upcoming window yet, so there is nothing to alert on. Alerts
              appear once a contract&apos;s dates have been extracted.
            </p>
          )}
        </div>
      ) : (
        <ul className="space-y-2">
          {alerts.map((a) => {
            const overdue = a.days_until < 0;
            return (
              <li
                key={a.id}
                className={`p-3 rounded-2xl border flex flex-wrap items-start justify-between gap-3 ${
                  overdue ? "bg-red-50 border-red-300" : "bg-slate-50 border-slate-200"
                }`}
              >
                <div className="min-w-0 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/contracts/${a.contract_id}`}
                      className="text-sm font-bold text-slate-900 hover:text-blue-600 break-words"
                    >
                      {a.contract_title}
                    </Link>
                    <DeadlineTypeBadge type={a.deadline_type} />
                    {overdue && <OverdueBadge />}
                  </div>
                  <p className="text-sm text-slate-800 break-words">{a.message}</p>
                  <p className={`text-xs font-semibold ${overdue ? "text-red-800" : "text-slate-600"}`}>
                    {dueInText(a.days_until)}
                    <span className="font-normal text-slate-500"> · {formatDate(a.deadline_date) ?? a.deadline_date}</span>
                  </p>
                  {a.email_count != null && a.email_count > 0 && (
                    <p className="text-xs text-slate-500">
                      Emailed {a.email_count}&times;
                      {a.emailed_at ? ` · last ${formatDate(a.emailed_at) ?? a.emailed_at}` : ""}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  onClick={() => onAcknowledge(a.id)}
                  aria-label={`Acknowledge alert for ${a.contract_title}`}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-100 shrink-0 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                >
                  <Check className="w-3.5 h-3.5" aria-hidden="true" />
                  Acknowledge
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div aria-live="polite" className="sr-only">{announce}</div>
    </section>
  );
}

// ─── Upcoming deadlines ───────────────────────────────────────────────────────

interface UpcomingProps {
  deadlines: DeadlineWithContext[];
  loading: boolean;
  error: string | null;
  window: DeadlineWindow;
  onWindowChange: (w: DeadlineWindow) => void;
}

function DeadlineRow({ d }: { d: DeadlineWithContext }) {
  const overdue = d.is_overdue;
  const location = locationLabel(d.source_page, d.source_section);
  const renewal = d.deadline_type === "RENEWAL_NOTICE";
  return (
    <li
      className={`p-4 rounded-2xl border space-y-2 ${
        overdue
          ? "bg-red-50 border-red-400 border-l-4"
          : renewal
            ? "bg-indigo-50/60 border-indigo-200"
            : "bg-slate-50 border-slate-200"
      }`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-sm font-mono font-bold text-slate-900">{formatDate(d.deadline_date) ?? d.deadline_date}</p>
        <p className={`text-xs font-semibold ${overdue ? "text-red-800" : "text-slate-600"}`}>
          {overdue ? `Overdue by ${Math.abs(d.days_until)} ${Math.abs(d.days_until) === 1 ? "day" : "days"}` : dueInText(d.days_until)}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <DeadlineTypeBadge type={d.deadline_type} />
        {overdue && <OverdueBadge />}
        {isUnverified(d.verification_status) && <UnverifiedBadge />}
      </div>

      <p className="text-sm text-slate-900 break-words">
        <Link href={`/contracts/${d.contract_id}`} className="font-bold hover:text-blue-600">
          {d.contract_title}
        </Link>
        <span className="text-slate-500"> — </span>
        {d.label}
        {d.responsible_party && <span className="text-slate-600"> ({d.responsible_party})</span>}
      </p>

      {d.basis && <p className="text-xs text-slate-600 leading-relaxed">{d.basis}</p>}

      {d.source_page != null && location ? (
        <Link
          href={sourceHref(d.contract_id, d.source_page, d.source_quote)}
          className="inline-block text-xs font-semibold text-blue-700 hover:text-blue-900"
        >
          Source: {location}
        </Link>
      ) : (
        <p className="text-xs text-slate-500">
          {d.deadline_type === "OBLIGATION" || d.responsible_party ? "Source location not verified." : "Source: derived from contract dates."}
        </p>
      )}
    </li>
  );
}

export function UpcomingDeadlines({ deadlines, loading, error, window: win, onWindowChange }: UpcomingProps) {
  return (
    <section aria-labelledby="deadlines-heading" className="bg-white rounded-3xl border border-slate-200 shadow-sm p-6 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-slate-100">
        <h2 id="deadlines-heading" className="text-xs font-bold text-slate-500 uppercase tracking-wider">
          Upcoming deadlines
        </h2>
        <div className="flex flex-wrap items-center gap-2">
          <div role="group" aria-label="Deadline window" className="flex flex-wrap items-center gap-1">
            {DEADLINE_WINDOWS.map((w) => (
              <button
                key={w}
                type="button"
                onClick={() => onWindowChange(w)}
                aria-pressed={win === w}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 ${
                  win === w
                    ? "bg-blue-600 text-white shadow-sm"
                    : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
                }`}
              >
                {w} days
              </button>
            ))}
          </div>
          <a
            href={calendarUrl()}
            download
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            <Download className="w-3.5 h-3.5" aria-hidden="true" />
            Download .ics
          </a>
        </div>
      </div>

      <p className="text-[11px] text-slate-500">
        Dates are worked out from your contracts by AI and are not legal advice. Each one shows how it was derived and where
        it came from; check the source before relying on it.
      </p>

      {loading ? (
        <p role="status" className="p-6 text-center text-xs text-slate-400">Loading deadlines...</p>
      ) : error ? (
        <p role="alert" className="p-6 text-center text-xs text-red-700">{error}</p>
      ) : deadlines.length === 0 ? (
        <p className="p-6 text-center text-xs text-slate-500">
          No deadlines in the next {win} days, and none overdue. Contracts still being processed, or whose dates
          could not be worked out, are not listed here.
        </p>
      ) : (
        <ul className="space-y-3">
          {[...deadlines]
            .sort((a, b) => a.deadline_date.localeCompare(b.deadline_date))
            .map((d) => (
              <DeadlineRow key={d.id} d={d} />
            ))}
        </ul>
      )}
    </section>
  );
}
