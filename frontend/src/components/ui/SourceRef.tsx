"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight } from "lucide-react";
import { locationLabel, sourceHref } from "@/lib/format";

interface SourceRefProps {
  contractId: string;
  page: number | null | undefined;
  section?: string | null;
  quote?: string | null;
  /** Link into this version's source viewer instead of the current version's. */
  versionId?: string | null;
  /** Use on dark backgrounds (timeline). */
  dark?: boolean;
}

const EXPAND_THRESHOLD = 140;

export function SourceRef({ contractId, page, section, quote, versionId, dark = false }: SourceRefProps) {
  const [expanded, setExpanded] = useState(false);
  const trimmedQuote = quote?.trim() || null;
  const location = locationLabel(page, section);
  const long = (trimmedQuote?.length ?? 0) > EXPAND_THRESHOLD;

  const quoteColor = dark ? "text-slate-400 border-slate-600" : "text-slate-500 border-blue-300";
  const linkColor = dark ? "text-blue-300 hover:text-blue-200" : "text-blue-700 hover:text-blue-900";

  return (
    <div className="space-y-1 text-xs">
      {page != null && location ? (
        <Link
          href={sourceHref(contractId, page, trimmedQuote, versionId)}
          className={`inline-flex items-center gap-1 font-semibold ${linkColor}`}
          title="Open this passage in the source viewer"
        >
          {location}
          <ArrowRight className="w-3 h-3" aria-hidden="true" />
        </Link>
      ) : (
        <span
          className={`inline-flex items-center gap-1 font-semibold ${dark ? "text-amber-300" : "text-amber-700"}`}
          title="The extraction could not tie this item to a page in the document"
        >
          <AlertTriangle className="w-3 h-3" aria-hidden="true" />
          Source location not verified
        </span>
      )}

      {trimmedQuote && (
        <div className={`border-l-2 pl-2.5 ${quoteColor}`}>
          {page == null && (
            <span className="block text-[10px] font-bold uppercase tracking-wider text-amber-600">
              Unverified quote
            </span>
          )}
          <p className={`italic leading-relaxed whitespace-pre-wrap break-words ${expanded ? "" : "line-clamp-2"}`}>
            &ldquo;{trimmedQuote}&rdquo;
          </p>
          {long && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className={`text-[11px] font-semibold ${linkColor}`}
            >
              {expanded ? "Show less" : "Show full quote"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
