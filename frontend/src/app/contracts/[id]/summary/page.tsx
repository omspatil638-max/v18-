"use client";

import { Suspense, use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { AlertTriangle, Download, Info, Loader2, Printer, RefreshCw, Sparkles } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ContractTabs } from "@/components/ui/Badges";
import { StatusChip } from "@/components/ui/StatusChip";
import { severityLabel } from "@/components/ui/FlagBadge";
import { errorMessage, fetchSummary, regenerateSummaryOverview, summaryMarkdownUrl } from "@/lib/api";
import { formatDate, sourceHref } from "@/lib/format";
import type { FlagSeverity, SummaryLine, SummaryResponse } from "@/lib/types";

const REWRITE_RELOAD_DELAY_MS = 3000;

const SEVERITY_DOT: Record<FlagSeverity, string> = {
  HIGH: "bg-red-600",
  MEDIUM: "bg-amber-500",
  LOW: "bg-slate-400",
};

const actionBtn =
  "inline-flex items-center gap-1.5 px-3 py-2 bg-white border border-slate-300 hover:bg-slate-50 text-slate-800 text-xs font-bold rounded-xl transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 disabled:opacity-50 disabled:cursor-not-allowed";

function SummaryLineItem({ line, contractId, versionId }: { line: SummaryLine; contractId: string; versionId: string }) {
  const quote = line.quote?.trim() || null;
  const sourceLabel = line.page != null ? `p.${line.page}${line.section ? ` - ${line.section}` : ""}` : null;
  return (
    <li className="py-2.5 flex flex-col gap-1 print:break-inside-avoid">
      <p className="text-sm text-slate-900 leading-relaxed break-words">{line.text}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
        {line.status !== "verified" && <StatusChip status={line.status} />}
        {line.severity && (
          <span className="inline-flex items-center gap-1.5 font-semibold text-slate-700">
            <span aria-hidden="true" className={`inline-block w-2 h-2 rounded-full ${SEVERITY_DOT[line.severity] ?? SEVERITY_DOT.LOW}`} />
            {severityLabel(line.severity)} priority
          </span>
        )}
        {sourceLabel && line.page != null ? (
          <Link
            href={sourceHref(contractId, line.page, quote, versionId)}
            title="Open this passage, highlighted, in the original PDF"
            className="font-semibold text-blue-700 hover:text-blue-900 underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 print:text-slate-700"
          >
            {sourceLabel}
          </Link>
        ) : (
          <span className="text-slate-500 italic">no verified source</span>
        )}
      </div>
    </li>
  );
}

function SummaryView({ id }: { id: string }) {
  const searchParams = useSearchParams();
  const versionParam = searchParams.get("version");

  const [data, setData] = useState<SummaryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);
  const [rewriting, setRewriting] = useState(false);
  const [rewriteError, setRewriteError] = useState<string | null>(null);
  const rewriteTimer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchSummary(id, versionParam)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorMessage(e, "Could not load the summary."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, versionParam, reloadKey]);

  useEffect(
    () => () => {
      if (rewriteTimer.current !== null) window.clearTimeout(rewriteTimer.current);
    },
    [],
  );

  const rewrite = useCallback(async () => {
    setRewriting(true);
    setRewriteError(null);
    try {
      await regenerateSummaryOverview(id, versionParam);
      // The rewrite runs in the background; look again shortly.
      rewriteTimer.current = window.setTimeout(() => {
        setReloadKey((k) => k + 1);
        setRewriting(false);
      }, REWRITE_RELOAD_DELAY_MS);
    } catch (e: unknown) {
      setRewriteError(errorMessage(e, "Could not rewrite the overview."));
      setRewriting(false);
    }
  }, [id, versionParam]);

  const generated = data ? formatDate(data.generated_at, "long") : null;
  const versionId = data?.contract_version_id ?? versionParam ?? null;
  const unavailable = data?.status === "unavailable";

  return (
    <div>
      <div data-print-hide>
        <PageHeader
          title={data ? `${data.title} — Brief` : "Contract brief"}
          subtitle="A one-page overview where every line links to the original text"
          breadcrumbs={[
            { label: "Dashboard", href: "/" },
            { label: data?.title || "Contract", href: versionParam ? `/contracts/${id}?version=${encodeURIComponent(versionParam)}` : `/contracts/${id}` },
            { label: "Brief" },
          ]}
          actions={
            data && (
              <>
                <a href={summaryMarkdownUrl(id, versionParam)} download className={actionBtn}>
                  <Download className="w-4 h-4" aria-hidden="true" />
                  Download as Markdown
                </a>
                <button type="button" onClick={() => window.print()} className={actionBtn}>
                  <Printer className="w-4 h-4" aria-hidden="true" />
                  Print / Save as PDF
                </button>
                {!unavailable && (
                  <button type="button" onClick={rewrite} disabled={rewriting} className={actionBtn}>
                    {rewriting ? (
                      <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    ) : (
                      <Sparkles className="w-4 h-4" aria-hidden="true" />
                    )}
                    {rewriting ? "Rewriting..." : "Rewrite overview"}
                  </button>
                )}
              </>
            )
          }
        />
      </div>
      <ContractTabs contractId={id} active="summary" />

      <div className="p-3 sm:p-8">
        {rewriteError && (
          <div role="alert" data-print-hide className="max-w-4xl mx-auto mb-4 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-700">
            {rewriteError}
          </div>
        )}

        {loading && !data ? (
          <div role="status" aria-live="polite" className="max-w-4xl mx-auto bg-white rounded-2xl border border-slate-200 p-8 space-y-4">
            <span className="sr-only">Loading the summary...</span>
            <div className="h-6 w-2/3 rounded bg-slate-100 animate-pulse" />
            <div className="h-4 w-1/3 rounded bg-slate-100 animate-pulse" />
            <div className="h-24 rounded bg-slate-100 animate-pulse" />
            <div className="h-16 rounded bg-slate-100 animate-pulse" />
          </div>
        ) : error && !data ? (
          <div role="alert" className="max-w-4xl mx-auto bg-white rounded-2xl border border-red-200 p-8 text-center space-y-3">
            <p className="text-sm text-red-700">{error}</p>
            <button type="button" onClick={() => setReloadKey((k) => k + 1)} className={actionBtn}>
              <RefreshCw className="w-4 h-4" aria-hidden="true" />
              Retry
            </button>
          </div>
        ) : data ? (
          <article
            aria-label={`Summary of ${data.title}`}
            className="max-w-4xl mx-auto bg-white rounded-2xl border border-slate-200 shadow-sm p-5 sm:p-8 space-y-6 print:max-w-none print:border-0 print:shadow-none print:p-0"
          >
            <header className="space-y-1">
              <h1 className="text-2xl font-bold text-slate-900 break-words">{data.title}</h1>
              <p className="text-sm text-slate-500">
                {[
                  data.version_label,
                  generated ? `Generated ${generated}` : null,
                  data.status === "partial" ? "Partial summary" : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </p>
            </header>

            {error && (
              <div role="alert" data-print-hide className="p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-700">
                Could not refresh the summary: {error} The version below may be out of date.
              </div>
            )}

            {unavailable && (
              <div role="status" className="p-4 rounded-xl bg-red-50 border border-red-200 text-sm text-red-800 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                <p>
                  <span className="font-semibold">No summary could be produced.</span> Nothing was extracted from this
                  document, so there are no verified facts to summarise. Re-run extraction from the contract overview,
                  or read the original document.
                </p>
              </div>
            )}

            {data.notes.length > 0 && (
              <aside aria-label="Notes about this summary" className="p-4 rounded-xl bg-blue-50 border border-blue-100">
                <p className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-blue-800 mb-2">
                  <Info className="w-3.5 h-3.5" aria-hidden="true" />
                  Notes
                </p>
                <ul className="list-disc pl-5 space-y-1 text-sm text-blue-900">
                  {data.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              </aside>
            )}

            {data.overview_status === "ai" && data.overview ? (
              <section aria-labelledby="summary-overview" className="p-4 sm:p-5 rounded-xl bg-indigo-50 border border-indigo-100 print:break-inside-avoid">
                <h2 id="summary-overview" className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider text-indigo-800 mb-2">
                  <Sparkles className="w-3.5 h-3.5" aria-hidden="true" />
                  AI-written overview - checked against the facts below
                </h2>
                <p className="text-sm text-slate-800 leading-relaxed whitespace-pre-wrap">{data.overview}</p>
              </section>
            ) : (
              !unavailable && (
                <p data-print-hide className="text-xs text-slate-500 italic">
                  No AI-written overview is available (it is optional and needs an AI model to be configured and
                  reachable). The verified lines below do not depend on it.
                </p>
              )
            )}

            {data.sections.map((section) => (
              <section key={section.key} aria-labelledby={`summary-${section.key}`} className="space-y-1">
                <h2
                  id={`summary-${section.key}`}
                  className="text-sm font-bold uppercase tracking-wider text-slate-700 border-b border-slate-200 pb-1.5 print:break-after-avoid"
                >
                  {section.title}
                </h2>
                {section.lines.length === 0 ? (
                  <p className="py-2 text-sm text-slate-500 italic">Nothing to report in this section.</p>
                ) : (
                  <ul className="divide-y divide-slate-100">
                    {section.lines.map((line, i) => (
                      <SummaryLineItem key={`${line.ref_id ?? line.kind}-${i}`} line={line} contractId={id} versionId={versionId ?? ""} />
                    ))}
                  </ul>
                )}
              </section>
            ))}

            <footer className="pt-4 border-t border-slate-200 space-y-1">
              <p role="note" className="text-xs text-slate-600 leading-relaxed">
                {data.disclaimer || "AI-assisted, not legal advice. This summary does not replace a qualified legal professional."}
              </p>
              <p className="text-[11px] text-slate-400">{data.word_count} words</p>
            </footer>
          </article>
        ) : null}
      </div>
    </div>
  );
}

export default function SummaryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<div className="p-12 text-center text-sm text-slate-400">Loading the summary...</div>}>
      <SummaryView id={id} />
    </Suspense>
  );
}
