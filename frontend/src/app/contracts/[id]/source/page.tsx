"use client";

import { Suspense, use, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import {
  FileText,
  Search,
  Download,
  AlertTriangle,
  Copy,
  Check,
  ChevronDown,
  ChevronRight,
  FileImage,
  Type,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { PdfPageViewer } from "@/components/PdfPageViewer";
import { ContractTabs } from "@/components/ui/Badges";
import { contractFileUrl, errorMessage, fetchContractById, fetchContractText } from "@/lib/api";
import { versionName } from "@/lib/format";
import { isProcessing } from "@/lib/types";
import type { ContractDetail, ContractText } from "@/lib/types";

const QUOTE_HIT_ID = "quote-hit";
const VIEW_STORAGE_KEY = "contractlens:source-view";

type ViewMode = "pdf" | "text";

function parseView(v: string | null): ViewMode | null {
  return v === "pdf" || v === "text" ? v : null;
}

function readStoredView(): ViewMode | null {
  try {
    return parseView(window.localStorage.getItem(VIEW_STORAGE_KEY));
  } catch {
    return null;
  }
}

function storeView(view: ViewMode): void {
  try {
    window.localStorage.setItem(VIEW_STORAGE_KEY, view);
  } catch {
    // Storage can be unavailable (private mode, blocked); the URL still carries the choice.
  }
}

type RangeKind = "quote" | "search";
interface Range {
  start: number;
  end: number;
  kind: RangeKind;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Whitespace- and case-insensitive regex for a phrase: its words joined by \s+. Null for empty input. */
function phraseRegex(phrase: string): RegExp | null {
  const words = phrase.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  return new RegExp(words.map(escapeRegExp).join("\\s+"), "gi");
}

function findRanges(text: string, re: RegExp | null, kind: RangeKind): Range[] {
  if (!re) return [];
  const out: Range[] = [];
  re.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    if (m[0].length === 0) {
      re.lastIndex++;
      continue;
    }
    out.push({ start: m.index, end: m.index + m[0].length, kind });
  }
  return out;
}

/** Non-overlapping ranges in order; quote matches win over search matches. */
function mergeRanges(quote: Range[], search: Range[]): Range[] {
  const all = [...quote, ...search].sort((a, b) => a.start - b.start || (a.kind === "quote" ? -1 : 1));
  const out: Range[] = [];
  let cursor = 0;
  for (const r of all) {
    if (r.start < cursor) continue;
    out.push(r);
    cursor = r.end;
  }
  return out;
}

function PageText({
  text,
  quoteRe,
  searchRe,
  markFirstQuote,
}: {
  text: string;
  quoteRe: RegExp | null;
  searchRe: RegExp | null;
  markFirstQuote: boolean;
}) {
  const ranges = mergeRanges(findRanges(text, quoteRe, "quote"), findRanges(text, searchRe, "search"));
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  let quoteSeen = false;
  ranges.forEach((r, i) => {
    if (r.start > cursor) nodes.push(text.slice(cursor, r.start));
    const isFirstQuote = r.kind === "quote" && markFirstQuote && !quoteSeen;
    if (r.kind === "quote") quoteSeen = true;
    nodes.push(
      <mark
        key={i}
        id={isFirstQuote ? QUOTE_HIT_ID : undefined}
        className={
          r.kind === "quote"
            ? "bg-amber-300 text-amber-950 font-semibold rounded px-0.5"
            : "bg-yellow-200 text-slate-900 rounded px-0.5"
        }
      >
        {text.slice(r.start, r.end)}
      </mark>
    );
    cursor = r.end;
  });
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return <>{nodes}</>;
}

function SourceViewer({ id }: { id: string }) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const pageParam = searchParams.get("page");
  const quote = searchParams.get("q");
  const versionParam = searchParams.get("version");
  const viewParam = parseView(searchParams.get("view"));
  const parsedPage = pageParam ? parseInt(pageParam, 10) : NaN;
  const targetPage = Number.isFinite(parsedPage) ? parsedPage : null;

  const [contract, setContract] = useState<ContractDetail | null>(null);
  const [contractError, setContractError] = useState<string | null>(null);
  const [text, setText] = useState<ContractText | null>(null);
  const [textLoading, setTextLoading] = useState(false);
  const [textError, setTextError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  // undefined = not read yet; null = nothing stored.
  const [storedView, setStoredView] = useState<ViewMode | null | undefined>(undefined);
  const [pdfFailed, setPdfFailed] = useState(false);
  const [quoteOpen, setQuoteOpen] = useState(true);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setStoredView(readStoredView());
  }, []);

  // The view: URL first, then the remembered choice, then the original PDF.
  const preferencesReady = viewParam !== null || storedView !== undefined;
  const noPagesKnown = contract !== null && !(contract.versions?.find((v) => v.id === (versionParam ?? contract.current_version_id))?.page_count || contract.page_count);
  const requestedView: ViewMode = viewParam ?? storedView ?? (noPagesKnown ? "text" : "pdf");
  const view: ViewMode = pdfFailed ? "text" : requestedView;

  // The page the quote was cited on: the quote is highlighted there, not on pages browsed to afterwards.
  const [cited, setCited] = useState<{ quote: string | null; page: number | null }>({ quote, page: targetPage });
  if (cited.quote !== quote) setCited({ quote, page: targetPage });

  useEffect(() => {
    let cancelled = false;
    setContractError(null);
    fetchContractById(id, versionParam)
      .then((c) => {
        if (!cancelled) setContract(c);
      })
      .catch((err: unknown) => {
        if (!cancelled) setContractError(errorMessage(err, "Could not load the contract."));
      });
    return () => {
      cancelled = true;
    };
  }, [id, versionParam]);

  // The extracted text is only needed for the text view.
  useEffect(() => {
    if (view !== "text") return;
    let cancelled = false;
    setTextLoading(true);
    setTextError(null);
    fetchContractText(id, versionParam)
      .then((t) => {
        if (!cancelled) setText(t);
      })
      .catch((err: unknown) => {
        if (!cancelled) setTextError(errorMessage(err, "Could not load the document text."));
      })
      .finally(() => {
        if (!cancelled) setTextLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, versionParam, view]);

  // A different version may have a working PDF even if this one did not.
  useEffect(() => {
    setPdfFailed(false);
  }, [id, versionParam]);

  // The version being read: the requested one, else the current one. Named only when there is more than one.
  const shownVersion =
    contract && contract.versions && contract.versions.length > 1
      ? (contract.versions.find((v) => v.id === (versionParam ?? contract.current_version_id)) ?? null)
      : null;
  const versionSuffix = shownVersion ? ` (${versionName(shownVersion)})` : "";

  const readVersion = contract?.versions?.find((v) => v.id === (versionParam ?? contract.current_version_id)) ?? null;
  const pdfPageCount = readVersion?.page_count || contract?.page_count || null;

  const quoteRe = useMemo(() => (quote ? phraseRegex(quote) : null), [quote]);
  const searchRe = useMemo(() => phraseRegex(searchQuery), [searchQuery]);

  const pages = text?.pages ?? [];
  const targetPageText = targetPage != null ? pages.find((p) => p.page === targetPage) : undefined;

  // Where do we expect the quote to be? On the requested page, or anywhere if no page was given.
  const quotePages = quoteRe
    ? pages.filter((p) => (targetPage == null || p.page === targetPage) && findRanges(p.text, quoteRe, "quote").length > 0)
    : [];
  const quoteFound = quotePages.length > 0;
  const firstQuotePage = quotePages[0]?.page ?? null;

  const searchCount = searchRe ? pages.reduce((n, p) => n + findRanges(p.text, searchRe, "search").length, 0) : 0;

  // Scroll to the quote (or page) once the text is on screen (text view only).
  useEffect(() => {
    if (view !== "text" || !text) return;
    const hit = document.getElementById(QUOTE_HIT_ID);
    if (hit) {
      hit.scrollIntoView({ block: "center" });
      return;
    }
    if (targetPage != null) {
      document.getElementById(`page-${targetPage}`)?.scrollIntoView({ block: "start" });
    }
  }, [view, text, targetPage, quote]);

  function jumpToPage(n: number) {
    document.getElementById(`page-${n}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /** Updates the query string in place: no new history entry, no scroll to top, no reload. */
  function updateParams(patch: Record<string, string | null>) {
    const params = new URLSearchParams(searchParams.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) params.delete(k);
      else params.set(k, v);
    }
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  function chooseView(next: ViewMode) {
    storeView(next);
    setStoredView(next);
    setPdfFailed(false);
    updateParams({ view: next });
  }

  async function copyQuote() {
    if (!quote) return;
    try {
      await navigator.clipboard.writeText(quote);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }

  const maxPage = pdfPageCount ?? Number.MAX_SAFE_INTEGER;
  const pdfPage = Math.min(Math.max(targetPage ?? 1, 1), maxPage);
  const viewerQuote = cited.page == null || cited.page === pdfPage ? quote : null;
  const textViewParams = new URLSearchParams(searchParams.toString());
  textViewParams.set("view", "text");
  const textViewHref = `${pathname}?${textViewParams.toString()}`;

  const toggleBtn = (active: boolean) =>
    `inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 ${
      active ? "bg-blue-600 text-white" : "text-slate-700 hover:bg-slate-100"
    }`;

  return (
    <div>
      <PageHeader
        title={contract ? `${contract.title}${versionSuffix} — Evidence` : "Contract evidence"}
        subtitle={
          view === "pdf"
            ? "The original PDF, with the cited text highlighted"
            : "Extracted text layer of the document, page by page"
        }
        breadcrumbs={[
          { label: "Dashboard", href: "/" },
          {
            label: contract?.title || "Contract",
            href: versionParam ? `/contracts/${id}?version=${encodeURIComponent(versionParam)}` : `/contracts/${id}`,
          },
          { label: "Evidence" },
        ]}
        actions={
          <a
            href={contractFileUrl(id, versionParam)}
            download
            className="inline-flex items-center gap-1.5 px-3 py-2 bg-white border border-slate-300 hover:bg-slate-50 text-slate-800 text-xs font-bold rounded-xl transition-colors"
          >
            <Download className="w-4 h-4" aria-hidden="true" />
            Download original PDF
          </a>
        }
      />
      <ContractTabs contractId={id} active="source" />

      <div className="p-3 sm:p-8 space-y-4 sm:space-y-6">
        {/* View toggle */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div role="group" aria-label="Document view" className="inline-flex p-1 bg-white border border-slate-200 rounded-xl">
            <button type="button" aria-pressed={view === "pdf"} onClick={() => chooseView("pdf")} className={toggleBtn(view === "pdf")}>
              <FileImage className="w-4 h-4" aria-hidden="true" />
              Original PDF
            </button>
            <button type="button" aria-pressed={view === "text"} onClick={() => chooseView("text")} className={toggleBtn(view === "text")}>
              <Type className="w-4 h-4" aria-hidden="true" />
              Text view
            </button>
          </div>
          <div className="text-xs text-slate-500 font-semibold min-w-0 break-words">
            {contract
              ? `${shownVersion ? `${versionName(shownVersion)} · ` : ""}${contract.filename} · ${text?.page_count ?? pdfPageCount ?? contract.page_count} pages`
              : ""}
          </div>
        </div>

        {pdfFailed && (
          <div role="status" className="p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900 flex flex-wrap items-start gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-px" aria-hidden="true" />
            <span>The original PDF page could not be displayed (the file may be missing), so the extracted text is shown instead.</span>
            <button
              type="button"
              onClick={() => chooseView("pdf")}
              className="font-bold underline underline-offset-2 hover:text-amber-950 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              Try the PDF again
            </button>
          </div>
        )}

        {contractError && (
          <div role="alert" className="p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-700">
            {contractError}
          </div>
        )}

        {view === "pdf" ? (
          !preferencesReady ? (
            <div className="p-12 bg-white rounded-2xl border border-slate-200 text-center text-sm text-slate-400">
              Loading the original PDF...
            </div>
          ) : (
            <div className="space-y-4">
              {quote && (
                <section aria-label="Quoted text" className="bg-white rounded-2xl border border-slate-200">
                  <button
                    type="button"
                    onClick={() => setQuoteOpen((o) => !o)}
                    aria-expanded={quoteOpen}
                    aria-controls="source-quote-panel"
                    className="w-full flex items-center gap-2 px-4 py-3 text-left text-xs font-bold uppercase tracking-wider text-slate-600 rounded-2xl focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                  >
                    {quoteOpen ? (
                      <ChevronDown className="w-4 h-4" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="w-4 h-4" aria-hidden="true" />
                    )}
                    Quote{cited.page != null ? ` from page ${cited.page}` : ""}
                  </button>
                  {quoteOpen && (
                    <div id="source-quote-panel" className="px-4 pb-4 space-y-3">
                      <blockquote className="border-l-4 border-amber-300 pl-3 text-sm italic text-slate-700 whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                        {quote}
                      </blockquote>
                      <div className="flex flex-wrap items-center gap-2">
                        <button
                          type="button"
                          onClick={copyQuote}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-800 text-xs font-bold rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                        >
                          {copied ? (
                            <Check className="w-3.5 h-3.5 text-green-600" aria-hidden="true" />
                          ) : (
                            <Copy className="w-3.5 h-3.5" aria-hidden="true" />
                          )}
                          {copied ? "Copied" : "Copy quote"}
                        </button>
                        <a
                          href={contractFileUrl(id, versionParam)}
                          download
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-50 text-slate-800 text-xs font-bold rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                        >
                          <Download className="w-3.5 h-3.5" aria-hidden="true" />
                          Download original PDF
                        </a>
                        {cited.page != null && cited.page !== pdfPage && (
                          <button
                            type="button"
                            onClick={() => updateParams({ page: String(cited.page) })}
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-blue-50 border border-blue-200 hover:bg-blue-100 text-blue-800 text-xs font-bold rounded-lg focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                          >
                            Back to page {cited.page}
                          </button>
                        )}
                      </div>
                      <span className="sr-only" role="status" aria-live="polite">
                        {copied ? "Quote copied to the clipboard" : ""}
                      </span>
                    </div>
                  )}
                </section>
              )}
              <PdfPageViewer
                contractId={id}
                versionId={versionParam}
                page={pdfPage}
                quote={viewerQuote}
                pageCount={pdfPageCount}
                onPageChange={(n) => updateParams({ page: String(n) })}
                onImageError={() => setPdfFailed(true)}
                textViewHref={textViewHref}
              />
            </div>
          )
        ) : (
          <>
            {/* Search + legend */}
            <div className="bg-slate-900 text-white p-5 rounded-2xl shadow-lg flex flex-wrap items-center justify-between gap-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="relative w-full sm:w-auto">
                  <label htmlFor="source-search" className="sr-only">
                    Search contract text
                  </label>
                  <Search className="w-4 h-4 text-slate-400 absolute left-3 top-2.5" aria-hidden="true" />
                  <input
                    id="source-search"
                    type="search"
                    placeholder="Search contract text..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="pl-9 pr-4 py-1.5 text-xs bg-slate-800 border border-slate-700 rounded-xl text-white placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 w-full sm:w-64"
                  />
                </div>
                {searchRe && (
                  <span className="text-xs text-slate-300" role="status">
                    {searchCount} {searchCount === 1 ? "match" : "matches"}
                  </span>
                )}
                <div className="flex items-center gap-2 text-xs">
                  <span className="px-2 py-0.5 rounded bg-amber-300 text-amber-950 font-semibold">Cited quote</span>
                  <span className="px-2 py-0.5 rounded bg-yellow-200 text-slate-900">Search match</span>
                </div>
              </div>
            </div>

            {/* Quote / page notices */}
            {quote && text && !quoteFound && (
              <div role="status" className="p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-px" aria-hidden="true" />
                <span>
                  {targetPage != null
                    ? `Quote text not found on page ${targetPage}. The reference may be inaccurate; check the page text below.`
                    : "Quote text not found in this document. The reference may be inaccurate."}
                  {targetPage != null && !targetPageText && ` Page ${targetPage} is not in the extracted text.`}
                </span>
              </div>
            )}
            {!quote && targetPage != null && text && !targetPageText && (
              <div role="status" className="p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-900">
                Page {targetPage} is not in the extracted text.
              </div>
            )}

            {textLoading && !text ? (
              <div className="p-12 bg-white rounded-2xl border border-slate-200 text-center text-sm text-slate-400">
                Loading document text...
              </div>
            ) : textError ? (
              <div role="alert" className="p-12 bg-white rounded-2xl border border-red-200 text-center text-sm text-red-600">
                {textError}
              </div>
            ) : pages.length === 0 ? (
              <div className="p-12 bg-white rounded-2xl border border-slate-200 text-center text-sm text-slate-500">
                {contract && isProcessing(contract.status)
                  ? "This document is still being processed. Its text will appear here when processing finishes."
                  : "No text layer is available for this document. It may be a scanned or password-protected PDF."}
              </div>
            ) : (
              <div className="space-y-4">
                <nav aria-label="Jump to page" className="flex flex-wrap items-center gap-1.5">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-slate-500 mr-1">Jump to page</span>
                  {pages.map((p) => (
                    <button
                      key={p.page}
                      type="button"
                      onClick={() => jumpToPage(p.page)}
                      className={`px-2 py-0.5 rounded-md text-xs font-semibold border transition-colors ${
                        p.page === (firstQuotePage ?? targetPage)
                          ? "bg-blue-600 text-white border-blue-600"
                          : "bg-white text-slate-600 border-slate-200 hover:bg-slate-50"
                      }`}
                    >
                      {p.page}
                    </button>
                  ))}
                </nav>

                {pages.map((p) => (
                  <section
                    key={p.page}
                    id={`page-${p.page}`}
                    aria-label={`Page ${p.page}`}
                    className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden scroll-mt-4"
                  >
                    <div className="bg-slate-100 border-b border-slate-200 px-6 py-2.5 text-xs font-bold text-slate-700 flex items-center gap-2">
                      <FileText className="w-4 h-4 text-blue-600" aria-hidden="true" />
                      Page {p.page}
                    </div>
                    <pre className="p-6 whitespace-pre-wrap break-words text-xs leading-relaxed font-mono text-slate-800">
                      <PageText
                        text={p.text}
                        quoteRe={targetPage == null || p.page === targetPage ? quoteRe : null}
                        searchRe={searchRe}
                        markFirstQuote={p.page === firstQuotePage}
                      />
                    </pre>
                  </section>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

export default function SourceViewerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <Suspense fallback={<div className="p-12 text-center text-sm text-slate-400">Loading the document...</div>}>
      <SourceViewer id={id} />
    </Suspense>
  );
}
