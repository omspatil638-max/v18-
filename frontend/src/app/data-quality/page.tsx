"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, CheckCircle2, FileText, Info, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { SeverityBadge } from "@/components/ui/FlagBadge";
import { errorMessage, fetchDataQuality } from "@/lib/api";
import type { DataQualityReport, FlagSeverity, QualityIssue, QualitySeverity } from "@/lib/types";

const SEVERITIES: { key: QualitySeverity; flag: FlagSeverity; heading: string }[] = [
  { key: "high", flag: "HIGH", heading: "High" },
  { key: "medium", flag: "MEDIUM", heading: "Medium" },
  { key: "low", flag: "LOW", heading: "Low" },
];

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

function CoverageRow({ label, have, total }: { label: string; have: number; total: number }) {
  const pct = total > 0 ? Math.round((have / total) * 100) : 0;
  return (
    <li className="space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="text-sm font-semibold text-slate-800">{label}</span>
        <span className="text-sm font-mono text-slate-700">
          {have} of {total}
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={`${label}: ${have} of ${total} contracts`}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={have}
        className="h-1.5 w-full rounded-full bg-slate-200 overflow-hidden"
      >
        <div className="h-full bg-blue-600" style={{ width: `${pct}%` }} />
      </div>
    </li>
  );
}

function IssueCard({ issue }: { issue: QualityIssue }) {
  return (
    <li className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 sm:p-5 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-bold text-slate-900 break-words min-w-0">{issue.title}</h3>
        <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-slate-100 text-slate-700 border border-slate-200 whitespace-nowrap">
          {plural(issue.count, "contract", "contracts")}
        </span>
      </div>
      <p className="text-xs text-slate-600 leading-relaxed break-words">{issue.why}</p>
      {issue.contracts.length > 0 && (
        <ul className="space-y-2">
          {issue.contracts.map((c, i) => (
            <li key={`${c.contract_id}-${c.field_key ?? i}`}>
              <Link
                href={`/contracts/${c.contract_id}`}
                className="flex items-start gap-2.5 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 hover:bg-blue-50 hover:border-blue-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
              >
                <FileText className="w-4 h-4 shrink-0 mt-0.5 text-slate-500" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-slate-900 break-words">{c.title}</span>
                  <span className="block text-xs text-slate-600 break-words">{c.detail}</span>
                  <span className="mt-0.5 inline-flex items-center gap-1 text-xs font-semibold text-blue-700">
                    {issue.action}
                    <ArrowRight className="w-3 h-3 shrink-0" aria-hidden="true" />
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export default function DataQualityPage() {
  const [report, setReport] = useState<DataQualityReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setReport(await fetchDataQuality());
      setError(null);
    } catch (e: unknown) {
      setError(errorMessage(e, "Could not load the data quality report."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div>
      <PageHeader
        title="Data quality"
        subtitle="What is missing or unchecked across your contracts"
        breadcrumbs={[{ label: "Dashboard", href: "/" }, { label: "Data quality" }]}
      />

      <div className="p-4 sm:p-8 space-y-6 max-w-5xl mx-auto">
        <div role="note" className="flex items-start gap-2 text-xs text-slate-600 bg-slate-100 border border-slate-200 rounded-xl px-3 py-2">
          <Info className="w-4 h-4 shrink-0 mt-px text-slate-500" aria-hidden="true" />
          <p>
            <span className="font-semibold text-slate-700">AI-assisted, not legal advice.</span> These are gaps found by
            automatic checks. A contract that is not listed here can still have wrong or missing details, so review the
            source before relying on any value.
          </p>
        </div>

        {loading && !report ? (
          <p role="status" className="p-8 text-center text-sm text-slate-500">
            Loading the data quality report...
          </p>
        ) : error && !report ? (
          <div role="alert" className="p-4 rounded-2xl bg-red-50 border border-red-200 text-sm text-red-900 space-y-2">
            <p className="font-semibold">{error}</p>
            <button
              type="button"
              onClick={() => void load()}
              className="px-3 py-1.5 rounded-xl bg-white border border-red-300 text-xs font-bold text-red-800 hover:bg-red-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              Try again
            </button>
          </div>
        ) : report && report.contracts_total === 0 ? (
          <div className="p-8 text-center bg-white border border-slate-200 rounded-2xl space-y-1">
            <ShieldCheck className="w-8 h-8 mx-auto text-slate-400" aria-hidden="true" />
            <p className="text-sm font-semibold text-slate-800">No contracts yet.</p>
            <p className="text-xs text-slate-500 max-w-md mx-auto">
              <Link href="/" className="font-semibold text-blue-700 hover:text-blue-900">
                Upload a contract
              </Link>{" "}
              and any missing or unchecked details will be listed here.
            </p>
          </div>
        ) : report ? (
          <>
            <p aria-live="polite" className="text-sm text-slate-800">
              <span className="font-bold">{plural(report.analysed, "contract", "contracts")} analysed</span>,{" "}
              <span className="font-bold">{plural(report.open_issues, "open item", "open items")}</span>
              {report.processing > 0 && (
                <span className="text-slate-600">
                  {" "}
                  ({report.processing} still processing, not counted yet)
                </span>
              )}
              .
            </p>

            {report.coverage.length > 0 && (
              <section aria-labelledby="coverage-heading" className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 sm:p-6 space-y-4">
                <div className="space-y-1">
                  <h2 id="coverage-heading" className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                    Coverage
                  </h2>
                  <p className="text-xs text-slate-500">
                    How many analysed contracts have a value for each key term. A value can still need checking.
                  </p>
                </div>
                <ul className="space-y-3">
                  {report.coverage.map((c) => (
                    <CoverageRow key={c.field_key} label={c.label} have={c.have} total={c.total} />
                  ))}
                </ul>
              </section>
            )}

            {report.open_issues === 0 ? (
              <div className="p-8 text-center bg-white border border-slate-200 rounded-2xl space-y-1">
                <CheckCircle2 className="w-8 h-8 mx-auto text-green-600" aria-hidden="true" />
                <p className="text-sm font-semibold text-slate-800">Nothing to fix</p>
                <p className="text-xs text-slate-500 max-w-md mx-auto">
                  The automatic checks found no gaps. They do not guarantee that every value is right.
                </p>
              </div>
            ) : (
              <div className="space-y-8">
                {SEVERITIES.map(({ key, flag, heading }) => {
                  const issues = report.issues.filter((i) => i.severity === key);
                  if (issues.length === 0) return null;
                  return (
                    <section key={key} aria-labelledby={`sev-${key}`} className="space-y-3">
                      <h2 id={`sev-${key}`} className="flex flex-wrap items-center gap-2 text-xs font-bold text-slate-500 uppercase tracking-wider">
                        <SeverityBadge severity={flag} />
                        <span>
                          {heading} ({issues.length})
                        </span>
                      </h2>
                      <ul className="space-y-3">
                        {issues.map((issue) => (
                          <IssueCard key={issue.code} issue={issue} />
                        ))}
                      </ul>
                    </section>
                  );
                })}
              </div>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}
