"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ChevronLeft, ChevronRight, FileText, RefreshCw } from "lucide-react";
import { errorMessage, fetchHighlights, pageImageUrl } from "@/lib/api";
import type { HighlightResponse } from "@/lib/types";

type Zoom = "fit" | 75 | 100 | 125 | 150;

const ZOOMS: { value: Zoom; label: string }[] = [
  { value: 75, label: "75%" },
  { value: 100, label: "100%" },
  { value: 125, label: "125%" },
  { value: 150, label: "150%" },
  { value: "fit", label: "Fit width" },
];

/** 100% is this many CSS pixels wide (or the container, if narrower). "Fit width" is the whole container. */
const BASE_WIDTH_PX = 800;
/** Width of a US-letter page in PDF points, used to pick a sharp image scale before the real size is known. */
const ASSUMED_PAGE_POINTS = 612;

interface Measure {
  width: number;
  dpr: number;
}

type HighlightResult =
  | { key: string; kind: "ok"; data: HighlightResponse }
  | { key: string; kind: "error"; message: string };

type ImageResult = { key: string; status: "loaded" | "error" };

export interface PdfPageViewerProps {
  contractId: string;
  versionId?: string | null;
  page: number;
  /** Text to highlight on the page. Nothing is highlighted (and no status shown) without it. */
  quote?: string | null;
  onPageChange?: (page: number) => void;
  /** Total pages, when known. Otherwise learned from the highlight response. */
  pageCount?: number | null;
  /** Called when the page image cannot be loaded (for example the file is missing). */
  onImageError?: () => void;
  /** Where the "view the extracted text instead" link in the error state goes. */
  textViewHref?: string;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function PdfPageViewer({
  contractId,
  versionId,
  page,
  quote,
  onPageChange,
  pageCount,
  onImageError,
  textViewHref,
}: PdfPageViewerProps) {
  const [zoom, setZoom] = useState<Zoom>(100);
  const [measure, setMeasure] = useState<Measure | null>(null);
  const [retry, setRetry] = useState(0);
  const [imageResult, setImageResult] = useState<ImageResult | null>(null);
  const [highlightResult, setHighlightResult] = useState<HighlightResult | null>(null);
  const [draft, setDraft] = useState<string | null>(null);

  // The page shown right now. Follows the `page` prop, and also moves immediately when the user navigates.
  const [localPage, setLocalPage] = useState(page);
  const [seenPageProp, setSeenPageProp] = useState(page);
  if (seenPageProp !== page) {
    setSeenPageProp(page);
    setLocalPage(page);
    setDraft(null);
  }

  const scrollerRef = useRef<HTMLDivElement>(null);
  const firstRectRef = useRef<HTMLDivElement>(null);
  const scrolledForKey = useRef<string | null>(null);

  const trimmedQuote = quote?.trim() || null;
  const imageKey = `${contractId}|${versionId ?? ""}|${localPage}|${retry}`;
  const highlightKey = `${contractId}|${versionId ?? ""}|${localPage}|${trimmedQuote ?? ""}`;

  // Track the width available to the page so the image can be requested at a matching, sharp size.
  useEffect(() => {
    const el = scrollerRef.current;
    if (!el) return;
    const update = () => setMeasure({ width: el.clientWidth, dpr: window.devicePixelRatio || 1 });
    update();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Highlights for (page, quote). Stale responses are ignored; the request is aborted when inputs change.
  useEffect(() => {
    if (!trimmedQuote) return;
    const controller = new AbortController();
    let cancelled = false;
    fetchHighlights(contractId, localPage, trimmedQuote, versionId, controller.signal)
      .then((data) => {
        if (!cancelled) setHighlightResult({ key: highlightKey, kind: "ok", data });
      })
      .catch((e: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setHighlightResult({ key: highlightKey, kind: "error", message: errorMessage(e, "Could not locate the quote.") });
      });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [contractId, localPage, trimmedQuote, versionId, highlightKey]);

  const currentHighlight = trimmedQuote && highlightResult?.key === highlightKey ? highlightResult : null;
  const highlightData = currentHighlight?.kind === "ok" ? currentHighlight.data : null;
  const imageStatus: "loading" | "loaded" | "error" = imageResult?.key === imageKey ? imageResult.status : "loading";
  const total = pageCount ?? highlightData?.page_count ?? null;

  // Rects in reading order, so "first" means the earliest line of the cited text.
  const rects = useMemo(() => {
    if (!highlightData?.matched) return [];
    return [...highlightData.rects].sort((a, b) => a.y0 - b.y0 || a.x0 - b.x0);
  }, [highlightData]);
  const showRects = imageStatus === "loaded" && rects.length > 0;

  // Once the image is on screen and the rects are known, bring the first highlight into view (once per page/quote).
  useEffect(() => {
    if (!showRects) return;
    const scrollKey = `${imageKey}|${highlightKey}`;
    if (scrolledForKey.current === scrollKey) return;
    scrolledForKey.current = scrollKey;
    const raf = requestAnimationFrame(() => {
      firstRectRef.current?.scrollIntoView({
        block: "center",
        inline: "nearest",
        behavior: prefersReducedMotion() ? "auto" : "smooth",
      });
    });
    return () => cancelAnimationFrame(raf);
  }, [showRects, imageKey, highlightKey]);

  const goTo = useCallback(
    (n: number) => {
      if (!Number.isFinite(n)) return;
      let next = Math.max(1, Math.trunc(n));
      if (total != null) next = Math.min(next, total);
      setDraft(null);
      if (next === localPage) return;
      setLocalPage(next);
      onPageChange?.(next);
    },
    [localPage, onPageChange, total],
  );

  function commitDraft() {
    if (draft === null) return;
    const n = parseInt(draft, 10);
    if (Number.isNaN(n)) setDraft(null);
    else goTo(n);
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    const target = e.target as HTMLElement;
    // Keep arrows working for typing in the page box and for scrolling a zoomed-in page sideways.
    if (target.tagName === "INPUT" || target.tagName === "SELECT" || target.tagName === "TEXTAREA") return;
    const scroller = scrollerRef.current;
    if (scroller && target === scroller && scroller.scrollWidth > scroller.clientWidth + 1) return;
    e.preventDefault();
    goTo(localPage + (e.key === "ArrowRight" ? 1 : -1));
  }

  const cssWidth =
    measure && measure.width > 0 ? (zoom === "fit" ? measure.width : Math.min(measure.width, BASE_WIDTH_PX) * (zoom / 100)) : null;
  const scale =
    cssWidth != null && measure
      ? Math.min(3, Math.max(1, Math.ceil(((cssWidth * Math.min(measure.dpr, 2)) / ASSUMED_PAGE_POINTS) * 4) / 4))
      : null;
  const src = scale != null ? pageImageUrl(contractId, localPage, { scale, versionId }) : null;
  const pageWidthStyle =
    zoom === "fit" ? "100%" : `calc(min(100%, ${BASE_WIDTH_PX}px) * ${zoom / 100})`;

  const atFirst = localPage <= 1;
  const atLast = total != null && localPage >= total;

  let status: React.ReactNode = null;
  if (trimmedQuote) {
    if (!currentHighlight) status = "Locating the quote on this page...";
    else if (currentHighlight.kind === "error")
      status = `The quote position could not be checked (${currentHighlight.message}) - showing the page anyway.`;
    else if (currentHighlight.data.matched && currentHighlight.data.rects.length > 0)
      status = `Highlighted on page ${localPage}`;
    else status = "This quote could not be located on this page - showing the page anyway";
  }
  const statusTone =
    currentHighlight?.kind === "ok" && currentHighlight.data.matched && currentHighlight.data.rects.length > 0
      ? "text-emerald-800"
      : "text-amber-800";

  const btn =
    "inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg border border-slate-300 bg-white text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";

  return (
    <div onKeyDown={onKeyDown} className="space-y-3">
      {/* Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 bg-white border border-slate-200 rounded-2xl px-3 py-2.5">
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => goTo(localPage - 1)} disabled={atFirst} className={btn} aria-label="Previous page">
            <ChevronLeft className="w-4 h-4" aria-hidden="true" />
            <span className="hidden sm:inline">Previous</span>
          </button>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              commitDraft();
            }}
            className="flex items-center gap-1.5 text-xs text-slate-600"
          >
            <label htmlFor="pdf-page-input" className="font-semibold">
              Page
            </label>
            <input
              id="pdf-page-input"
              type="number"
              inputMode="numeric"
              min={1}
              max={total ?? undefined}
              value={draft ?? String(localPage)}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitDraft}
              className="w-14 px-2 py-1 rounded-lg border border-slate-300 text-xs text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            />
            <span aria-live="polite">{total != null ? `of ${total}` : ""}</span>
          </form>
          <button type="button" onClick={() => goTo(localPage + 1)} disabled={atLast} className={btn} aria-label="Next page">
            <span className="hidden sm:inline">Next</span>
            <ChevronRight className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div role="group" aria-label="Zoom" className="flex flex-wrap items-center gap-1">
          {ZOOMS.map((z) => (
            <button
              key={String(z.value)}
              type="button"
              aria-pressed={zoom === z.value}
              onClick={() => setZoom(z.value)}
              className={`px-2.5 py-1.5 rounded-lg border text-xs font-semibold focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 ${
                zoom === z.value
                  ? "bg-blue-600 border-blue-600 text-white"
                  : "bg-white border-slate-300 text-slate-700 hover:bg-slate-50"
              }`}
            >
              {z.label}
            </button>
          ))}
        </div>
      </div>

      {/* Status (announced to screen readers) */}
      <div role="status" aria-live="polite" className={`min-h-[1.25rem] text-xs font-semibold ${statusTone}`}>
        {status && (
          <span className="inline-flex items-start gap-1.5">
            {statusTone === "text-amber-800" && <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" aria-hidden="true" />}
            {status}
          </span>
        )}
      </div>

      {/* Page */}
      <div
        ref={scrollerRef}
        tabIndex={0}
        role="group"
        aria-label={`Page ${localPage} of the original PDF. Use the left and right arrow keys to change page.`}
        className="overflow-x-auto rounded-2xl border border-slate-200 bg-slate-200/60 p-2 sm:p-4 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
      >
        {imageStatus === "error" ? (
          <div role="alert" className="mx-auto max-w-md my-10 p-6 bg-white rounded-2xl border border-red-200 text-center space-y-3">
            <p className="text-sm font-semibold text-red-700">Page {localPage} could not be loaded.</p>
            <p className="text-xs text-slate-600">
              The original PDF may be missing or unreadable. You can retry, or read the extracted text instead.
            </p>
            <div className="flex flex-wrap items-center justify-center gap-2">
              <button
                type="button"
                onClick={() => setRetry((n) => n + 1)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-blue-600 text-white text-xs font-bold hover:bg-blue-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
              >
                <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
                Retry
              </button>
              {textViewHref && (
                <Link
                  href={textViewHref}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-300 bg-white text-xs font-bold text-slate-800 hover:bg-slate-50"
                >
                  <FileText className="w-3.5 h-3.5" aria-hidden="true" />
                  Open the text view
                </Link>
              )}
            </div>
          </div>
        ) : (
          <div className="relative mx-auto shadow-md bg-white" style={{ width: pageWidthStyle }}>
            {imageStatus === "loading" && (
              <div
                aria-hidden="true"
                className="absolute inset-0 min-h-[12rem] animate-pulse bg-slate-100"
              />
            )}
            {imageStatus === "loading" && <div aria-hidden="true" className="w-full" style={{ aspectRatio: "8.5 / 11" }} />}
            {src && (
              // eslint-disable-next-line @next/next/no-img-element -- server-rendered page image, sized by its own aspect ratio
              <img
                key={retry}
                src={src}
                alt={`Page ${localPage} of the original PDF`}
                onLoad={() => setImageResult({ key: imageKey, status: "loaded" })}
                onError={() => {
                  setImageResult({ key: imageKey, status: "error" });
                  onImageError?.();
                }}
                draggable={false}
                className={`block w-full h-auto select-none ${imageStatus === "loaded" ? "" : "absolute inset-0 opacity-0"}`}
              />
            )}
            {showRects &&
              rects.map((r, i) => (
                <div
                  key={i}
                  ref={i === 0 ? firstRectRef : undefined}
                  aria-hidden="true"
                  data-testid="pdf-highlight"
                  className="cl-highlight-pulse absolute pointer-events-none rounded-sm bg-amber-300/60 mix-blend-multiply"
                  style={{
                    left: `${r.x0 * 100}%`,
                    top: `${r.y0 * 100}%`,
                    width: `${Math.max(0, r.x1 - r.x0) * 100}%`,
                    height: `${Math.max(0, r.y1 - r.y0) * 100}%`,
                  }}
                />
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
