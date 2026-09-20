import { Download, FileText, ScanLine } from "lucide-react";
import { contractFileUrl } from "@/lib/api";
import type { ExtractionMeta } from "@/lib/types";

const MAX_LISTED = 20;

function pageList(pages: number[]): string {
  const sorted = [...pages].sort((a, b) => a - b);
  const shown = sorted.slice(0, MAX_LISTED).join(", ");
  const more = sorted.length - MAX_LISTED;
  const text = more > 0 ? `${shown} and ${more} more` : shown;
  return `${sorted.length === 1 ? "page" : "pages"} ${text}`;
}

const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";

/**
 * Explains how the text was obtained when it did not come straight from a text PDF:
 * scanned pages read with OCR (a warning) or a Word document converted to PDF (calm info).
 * Not dismissible on purpose: the caveat applies for as long as the values are shown.
 */
export function ExtractionSourceNotice({
  contractId,
  versionId,
  meta,
}: {
  contractId: string;
  versionId?: string | null;
  meta: ExtractionMeta | null;
}) {
  const source = meta?.source;
  if (!source) return null;

  if (source.kind === "ocr") {
    const skipped = source.skipped_pages ?? [];
    const empty = source.empty_pages ?? [];
    const low = source.low_confidence_pages ?? [];
    return (
      <section
        role="note"
        aria-label="Scanned document notice"
        className="p-4 rounded-2xl border border-amber-300 bg-amber-50 text-amber-950 text-sm space-y-2"
      >
        <p className="flex items-start gap-2 font-semibold">
          <ScanLine className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
          <span>This contract was read from scanned pages using OCR ({pageList(source.pages ?? [])}).</span>
        </p>
        <p className="text-xs">
          OCR can misread digits and names, so values from these pages are marked &lsquo;needs review&rsquo; until you
          check them against the page image. Use Review on a value to confirm it.
        </p>
        {(low.length > 0 || skipped.length > 0 || empty.length > 0) && (
          <ul className="text-xs list-disc pl-5 space-y-0.5">
            {low.length > 0 && (
              <li>
                Low reading confidence on {pageList(low)}.
              </li>
            )}
            {skipped.length > 0 && (
              <li className="font-semibold">
                {skipped.length} {skipped.length === 1 ? "page was" : "pages were"} not read because of the page limit (
                {pageList(skipped)}); the analysis is incomplete.
              </li>
            )}
            {empty.length > 0 && <li>No text was found on {pageList(empty)}.</li>}
          </ul>
        )}
      </section>
    );
  }

  const notes = source.notes ?? [];
  return (
    <section
      role="note"
      aria-label="Converted document notice"
      className="p-4 rounded-2xl border border-blue-200 bg-blue-50 text-blue-950 text-sm space-y-2"
    >
      <p className="flex items-start gap-2 font-semibold">
        <FileText className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
        <span>Converted from a Word document. Page numbers refer to the converted copy.</span>
      </p>
      {notes.length > 0 && (
        <ul className="text-xs list-disc pl-5 space-y-0.5">
          {notes.map((n, i) => (
            <li key={i}>{n}</li>
          ))}
        </ul>
      )}
      <a
        href={contractFileUrl(contractId, versionId, true)}
        className={`inline-flex items-center gap-1.5 text-xs font-bold text-blue-800 hover:text-blue-950 underline ${focusRing}`}
      >
        <Download className="w-3.5 h-3.5" aria-hidden="true" />
        Download the original Word file
      </a>
    </section>
  );
}

/** Small marker for a value that was read from a scanned page. */
export function OcrFieldChip() {
  return (
    <p className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-900 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
      <ScanLine className="w-3 h-3 shrink-0" aria-hidden="true" />
      Read by OCR: check the page image
    </p>
  );
}
