"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertTriangle, BellRing, CalendarClock, Download, Info, RefreshCw } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { TypeAndTagChips } from "@/components/ContractChips";
import { StatusChip } from "@/components/ui/StatusChip";
import { calendarUrl, errorMessage, fetchRenewals } from "@/lib/api";
import { formatDate, locationLabel, relativeDays, sourceHref } from "@/lib/format";
import type { RenewalBucket, RenewalItem, RenewalSource, RenewalsResponse } from "@/lib/types";

const WINDOWS = [30, 60, 90, 180, 365] as const;

const GROUPS: { id: string; heading: string; buckets: RenewalBucket[]; tone: "red" | "normal" }[] = [
  { id: "missed", heading: "Notice deadline missed", buckets: ["notice_missed", "overdue"], tone: "red" },
  { id: "d30", heading: "Next 30 days", buckets: ["30"], tone: "normal" },
  { id: "d60", heading: "31 to 60 days", buckets: ["60"], tone: "normal" },
  { id: "d90", heading: "61 to 90 days", buckets: ["90"], tone: "normal" },
  { id: "later", heading: "Later", buckets: ["later"], tone: "normal" },
  { id: "ended", heading: "Recently ended", buckets: ["expired"], tone: "normal" },
];

const FIELD_WORDS: Record<string, string> = {
  expiration_date: "expiry date",
  effective_date: "start date",
  auto_renew: "auto-renewal",
  renewal_notice_period: "notice period",
  termination_notice_period: "termination notice period",
  renewal_terms: "renewal terms",
  payment_terms: "payment terms",
};

function fieldWord(key: string): string {
  return FIELD_WORDS[key] ?? key.replace(/_/g, " ");
}

function actionLine(item: RenewalItem): string {
  const date = formatDate(item.action_date, "long") ?? item.action_date;
  switch (item.kind) {
    case "give_notice_by":
      return `Give notice by ${date}`;
    case "expires":
      return `Expires ${date}`;
    case "notice_missed":
      return `Notice deadline was ${date}`;
    case "expired":
      return `Ended ${date}`;
  }
}

const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";

function SummaryChip({ label, value, tone }: { label: string; value: number | null; tone: string }) {
  return (
    <div className={`px-4 py-3 rounded-2xl border ${tone}`}>
      <p className="text-[11px] font-bold uppercase tracking-wider opacity-80">{label}</p>
      <p className="text-2xl font-bold leading-tight">{value ?? "–"}</p>
    </div>
  );
}

function SourceLine({
  contractId,
  label,
  source,
}: {
  contractId: string;
  label: string;
  source: RenewalSource | null;
}) {
  if (!source) return null;
  const location = locationLabel(source.page, source.section);
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
      <span className="text-slate-600">{label}:</span>
      {source.page != null && location ? (
        <Link
          href={sourceHref(contractId, source.page, source.quote)}
          className={`font-semibold text-blue-700 hover:text-blue-900 ${focusRing}`}
        >
          {location}
        </Link>
      ) : (
        <span className="text-amber-700 font-semibold">Source location not verified</span>
      )}
      <StatusChip status={source.status} />
    </li>
  );
}

function RenewalCard({ item }: { item: RenewalItem }) {
  const missed = item.kind === "notice_missed" || item.bucket === "overdue";
  const ended = item.kind === "expired";
  const hasSources = item.sources.expiry || item.sources.auto_renew || item.sources.notice;
  return (
    <li
      className={`p-4 sm:p-5 rounded-2xl border space-y-3 ${
        missed ? "bg-red-50 border-red-300 border-l-4" : ended ? "bg-slate-50 border-slate-200" : "bg-white border-slate-200 shadow-sm"
      }`}
    >
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/contracts/${item.contract_id}`}
            className={`text-sm font-bold text-slate-900 hover:text-blue-600 break-words ${focusRing}`}
          >
            {item.title}
          </Link>
          <TypeAndTagChips contractType={item.contract_type} tags={item.tags} />
        </div>
        {item.counterparty && <p className="text-xs text-slate-600 break-words">{item.counterparty}</p>}
      </div>

      <p className={`flex flex-wrap items-baseline gap-x-3 gap-y-0.5 ${missed ? "text-red-900" : "text-slate-900"}`}>
        {missed && <AlertTriangle className="w-4 h-4 self-center shrink-0" aria-hidden="true" />}
        <span className="text-lg font-bold">{actionLine(item)}</span>
        <span className="text-sm font-semibold">{relativeDays(item.days_to_action)}</span>
      </p>

      <p className="text-sm text-slate-800 break-words">{item.what_happens}</p>

      {item.notice_basis && <p className="text-xs text-slate-500 break-words">{item.notice_basis}</p>}

      {item.amounts.length > 0 && (
        <p className="text-xs text-slate-700 break-words">
          <span className="font-semibold">Amounts written in the payment terms:</span> {item.amounts.join(", ")}{" "}
          <span className="text-slate-500">(as written; not totals)</span>
        </p>
      )}

      {item.unverified.length > 0 && (
        <p>
          <span
            title="These values could not be fully checked against the document. Check the source before relying on this date."
            className="inline-flex items-start gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold border bg-amber-100 text-amber-900 border-amber-300"
          >
            <AlertTriangle className="w-3 h-3 shrink-0 mt-0.5" aria-hidden="true" />
            <span>Unverified: {item.unverified.map(fieldWord).join(", ")}</span>
          </span>
        </p>
      )}

      {hasSources && (
        <ul className="space-y-1" aria-label="Sources">
          <SourceLine contractId={item.contract_id} label="Expiry" source={item.sources.expiry} />
          <SourceLine contractId={item.contract_id} label="Auto-renewal" source={item.sources.auto_renew} />
          <SourceLine contractId={item.contract_id} label="Notice" source={item.sources.notice} />
        </ul>
      )}

      {item.open_flags > 0 && (
        <p className="text-xs">
          <Link
            href={`/review?contract=${encodeURIComponent(item.contract_id)}`}
            className={`inline-flex items-center gap-1 font-semibold text-amber-800 hover:text-amber-950 underline ${focusRing}`}
          >
            <BellRing className="w-3 h-3" aria-hidden="true" />
            {item.open_flags} open review {item.open_flags === 1 ? "flag" : "flags"}
          </Link>
        </p>
      )}
    </li>
  );
}

export default function RenewalsPage() {
  const [windowDays, setWindowDays] = useState<number>(90);
  const [data, setData] = useState<RenewalsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchRenewals(windowDays)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorMessage(e, "Could not load renewals."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [windowDays, reloadKey]);

  const retry = useCallback(() => setReloadKey((k) => k + 1), []);

  const notTracked = data?.not_tracked ?? [];

  return (
    <div>
      <PageHeader
        title="Renewals"
        subtitle="Notice deadlines and expiries across your contracts"
        breadcrumbs={[{ label: "Dashboard", href: "/" }, { label: "Renewals" }]}
        actions={
          <a
            href={calendarUrl()}
            download
            className={`inline-flex items-center gap-1.5 px-3 py-2 bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 text-xs font-bold rounded-xl shadow-sm ${focusRing}`}
          >
            <Download className="w-4 h-4" aria-hidden="true" />
            Add all to calendar (.ics)
          </a>
        }
      />

      <div className="p-4 sm:p-8 space-y-6 max-w-5xl mx-auto">
        <div role="note" className="flex items-start gap-2 text-xs text-slate-600 bg-slate-100 border border-slate-200 rounded-xl px-3 py-2">
          <Info className="w-4 h-4 shrink-0 mt-px text-slate-500" aria-hidden="true" />
          <p>
            <span className="font-semibold text-slate-700">AI-assisted, not legal advice.</span> Dates are worked out from
            what the AI read in each contract. Check the source link before you act on a date, especially anything marked
            unverified.
          </p>
        </div>

        <div className="flex flex-wrap items-end justify-between gap-4">
          <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
            Look ahead
            <select
              value={windowDays}
              onChange={(e) => setWindowDays(Number(e.target.value))}
              className={`block px-3 py-2 rounded-xl border border-slate-300 bg-white text-sm text-slate-800 ${focusRing}`}
            >
              {WINDOWS.map((w) => (
                <option key={w} value={w}>
                  {w} days
                </option>
              ))}
            </select>
          </label>
        </div>

        {data && (
          <section aria-label="Renewal summary" className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <SummaryChip label="Within 30 days" value={data.summary.due_30} tone="bg-white border-slate-200 text-slate-900" />
            <SummaryChip label="Within 60 days" value={data.summary.due_60} tone="bg-white border-slate-200 text-slate-900" />
            <SummaryChip label="Within 90 days" value={data.summary.due_90} tone="bg-white border-slate-200 text-slate-900" />
            <SummaryChip label="Auto-renewing" value={data.summary.auto_renewing} tone="bg-indigo-50 border-indigo-200 text-indigo-900" />
            <SummaryChip label="Notice missed" value={data.summary.notice_missed} tone="bg-red-50 border-red-200 text-red-900" />
            <SummaryChip label="Recently ended" value={data.summary.expired_recently} tone="bg-slate-50 border-slate-200 text-slate-800" />
          </section>
        )}

        <div aria-busy={loading}>
          {loading && !data ? (
            <p role="status" className="p-8 text-center text-sm text-slate-500">
              Loading renewals...
            </p>
          ) : error && !data ? (
            <div role="alert" className="p-4 rounded-2xl bg-red-50 border border-red-200 text-sm text-red-900 space-y-2">
              <p className="font-semibold">{error}</p>
              <button
                type="button"
                onClick={retry}
                className={`px-3 py-1.5 rounded-xl bg-white border border-red-300 text-xs font-bold text-red-800 hover:bg-red-100 ${focusRing}`}
              >
                Try again
              </button>
            </div>
          ) : data ? (
            <div className="space-y-8">
              {error && (
                <p role="alert" className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                  {error}{" "}
                  <button type="button" onClick={retry} className={`font-bold underline ${focusRing}`}>
                    Try again
                  </button>
                </p>
              )}
              {loading && (
                <p role="status" className="text-xs text-slate-500">
                  Updating...
                </p>
              )}

              {data.items.length === 0 ? (
                <div className="p-8 text-center bg-white border border-slate-200 rounded-2xl space-y-1">
                  <CalendarClock className="w-8 h-8 mx-auto text-slate-400" aria-hidden="true" />
                  <p className="text-sm font-semibold text-slate-800">
                    No renewals or expiries in the next {data.window_days} days
                  </p>
                  <p className="text-xs text-slate-500 max-w-md mx-auto">
                    {notTracked.length > 0
                      ? `${notTracked.length} ${notTracked.length === 1 ? "contract is" : "contracts are"} not tracked because ${
                          notTracked.length === 1 ? "its" : "their"
                        } dates are missing or unclear (listed below), so this may not be everything.`
                      : "Contracts still processing, or whose dates could not be read, are not included."}
                  </p>
                </div>
              ) : (
                GROUPS.map((group) => {
                  const items = data.items.filter((i) => group.buckets.includes(i.bucket));
                  if (items.length === 0) return null;
                  return (
                    <section key={group.id} aria-labelledby={`grp-${group.id}`} className="space-y-3">
                      <h2
                        id={`grp-${group.id}`}
                        className={`flex items-center gap-2 text-xs font-bold uppercase tracking-wider ${
                          group.tone === "red" ? "text-red-800" : "text-slate-500"
                        }`}
                      >
                        {group.tone === "red" && <AlertTriangle className="w-4 h-4" aria-hidden="true" />}
                        {group.heading} ({items.length})
                      </h2>
                      <ul className="space-y-3">
                        {items.map((item) => (
                          <RenewalCard key={`${item.contract_id}-${item.kind}-${item.action_date}`} item={item} />
                        ))}
                      </ul>
                    </section>
                  );
                })
              )}

              {notTracked.length > 0 && (
                <section
                  aria-labelledby="not-tracked-heading"
                  className="bg-white rounded-2xl border border-slate-200 p-4 sm:p-5 space-y-3"
                >
                  <div className="space-y-1">
                    <h2 id="not-tracked-heading" className="flex items-center gap-2 text-xs font-bold text-slate-500 uppercase tracking-wider">
                      <RefreshCw className="w-4 h-4" aria-hidden="true" />
                      Not tracked ({notTracked.length})
                    </h2>
                    <p className="text-xs text-slate-500">
                      These contracts have no usable expiry or notice date, so they cannot appear above.
                    </p>
                  </div>
                  <ul className="space-y-2">
                    {notTracked.map((n) => (
                      <li key={n.contract_id} className="text-sm break-words">
                        <Link
                          href={`/contracts/${n.contract_id}`}
                          className={`font-semibold text-blue-700 hover:text-blue-900 ${focusRing}`}
                        >
                          {n.title}
                        </Link>
                        <span className="text-slate-500"> — </span>
                        <span className="text-slate-700">{n.reason}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
