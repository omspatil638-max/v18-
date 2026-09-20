"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight, Sparkles } from "lucide-react";
import { ChangeTypeBadge, MaterialBadge } from "@/components/ui/ChangeBadges";
import { sourceHref } from "@/lib/format";
import type { ImpactStatus, VersionChange } from "@/lib/types";

export interface VersionRef {
  id: string;
  version_number: number;
}

const QUOTE_EXPAND_THRESHOLD = 160;

/** "v1 - Section 9.2, page 4"; never invents a location that was not given. */
export function sourceLocationText(version: VersionRef, section: string | null, page: number | null): string {
  const parts: string[] = [];
  if (section) parts.push(section);
  if (page != null) parts.push(`page ${page}`);
  return `v${version.version_number} - ${parts.length > 0 ? parts.join(", ") : "location not stated"}`;
}

/** One side (old or new) of a change: a link to the exact passage in that version, with the verbatim quote. */
function SourceSide({
  contractId,
  version,
  section,
  page,
  quote,
  absentText,
  present,
}: {
  contractId: string;
  version: VersionRef;
  section: string | null;
  page: number | null;
  quote: string | null;
  /** Shown when this side has no text (e.g. "Not in v1"). */
  absentText: string;
  present: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const trimmed = quote?.trim() || null;
  const long = (trimmed?.length ?? 0) > QUOTE_EXPAND_THRESHOLD;
  const location = sourceLocationText(version, section, page);

  if (!present && !trimmed && page == null && !section) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50/60 p-3 text-xs italic text-slate-500">
        {absentText}
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 space-y-1.5 text-xs min-w-0">
      {page != null ? (
        <Link
          href={sourceHref(contractId, page, trimmed, version.id)}
          title={`Open this passage in the source viewer for v${version.version_number}`}
          className="inline-flex items-center gap-1 font-semibold text-blue-700 hover:text-blue-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
        >
          {location}
          <ArrowRight className="w-3 h-3 shrink-0" aria-hidden="true" />
        </Link>
      ) : (
        <span className="font-semibold text-slate-700">{location}</span>
      )}
      {trimmed ? (
        <div className="border-l-2 border-blue-300 pl-2.5 text-slate-600">
          <p className={`italic leading-relaxed whitespace-pre-wrap break-words ${expanded ? "" : "line-clamp-3"}`}>
            &ldquo;{trimmed}&rdquo;
          </p>
          {long && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className="text-[11px] font-semibold text-blue-700 hover:text-blue-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              {expanded ? "Show less" : "Show full quote"}
            </button>
          )}
        </div>
      ) : (
        <p className="italic text-slate-500">No quote is available for this side.</p>
      )}
    </div>
  );
}

function ChangeValueLine({ change }: { change: VersionChange }) {
  const isKeyTerm = change.category !== "section";
  const hasValues = change.old_value != null || change.new_value != null;
  if (!isKeyTerm && !hasValues) return null;

  const oldText = change.old_value ?? "not stated";
  const newText = change.change_type === "REMOVED" ? "removed" : (change.new_value ?? "not stated");
  return (
    <p className="text-sm text-slate-900 flex flex-wrap items-baseline gap-x-2 gap-y-1">
      <span className="font-semibold">{change.label}:</span>
      <span
        className={`px-1.5 py-0.5 rounded bg-red-50 text-red-900 border border-red-100 ${
          change.old_value == null ? "italic text-slate-600 bg-slate-50 border-slate-200" : ""
        }`}
      >
        <span className="sr-only">Before: </span>
        {oldText}
      </span>
      <ArrowRight className="w-3.5 h-3.5 self-center text-slate-500 shrink-0" aria-hidden="true" />
      <span className="sr-only">changed to</span>
      <span
        className={`px-1.5 py-0.5 rounded border ${
          change.change_type === "REMOVED" || change.new_value == null
            ? "italic text-slate-600 bg-slate-50 border-slate-200"
            : "bg-green-50 text-green-900 border-green-100"
        }`}
      >
        <span className="sr-only">After: </span>
        {newText}
      </span>
    </p>
  );
}

function ImpactArea({ change, impactStatus }: { change: VersionChange; impactStatus: ImpactStatus }) {
  if (change.impact_text) {
    return (
      <div className="rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 space-y-1">
        <p className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-indigo-900">
          <Sparkles className="w-3 h-3" aria-hidden="true" />
          AI-explained impact (check against the sources)
        </p>
        <p className="text-sm text-slate-800 leading-relaxed whitespace-pre-wrap">{change.impact_text}</p>
      </div>
    );
  }
  if (!change.is_material) return null;
  return (
    <p className="text-xs text-slate-500 italic rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
      {impactStatus === "unavailable"
        ? "AI impact explanation unavailable (no AI is configured or it could not be verified)."
        : "No AI impact explanation is available for this change."}
    </p>
  );
}

export function VersionChangeCard({
  contractId,
  fromVersion,
  toVersion,
  change,
  impactStatus,
}: {
  contractId: string;
  fromVersion: VersionRef;
  toVersion: VersionRef;
  change: VersionChange;
  impactStatus: ImpactStatus;
}) {
  return (
    <article
      className={`rounded-2xl border bg-white p-4 shadow-sm space-y-3 ${
        change.is_material ? "border-orange-300" : "border-slate-200"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-bold text-slate-900 mr-1 break-words min-w-0">{change.label}</h4>
        <ChangeTypeBadge type={change.change_type} />
        {change.is_material && <MaterialBadge />}
      </div>

      <ChangeValueLine change={change} />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <SourceSide
          contractId={contractId}
          version={fromVersion}
          section={change.old_section}
          page={change.old_page}
          quote={change.old_quote}
          present={change.change_type !== "ADDED"}
          absentText={`Not in v${fromVersion.version_number}`}
        />
        <SourceSide
          contractId={contractId}
          version={toVersion}
          section={change.new_section}
          page={change.new_page}
          quote={change.new_quote}
          present={change.change_type !== "REMOVED"}
          absentText={`Not in v${toVersion.version_number}`}
        />
      </div>

      <ImpactArea change={change} impactStatus={impactStatus} />
    </article>
  );
}

/** Compact one-liner for unchanged rows. */
export function UnchangedRow({
  contractId,
  toVersion,
  change,
}: {
  contractId: string;
  toVersion: VersionRef;
  change: VersionChange;
}) {
  const page = change.new_page ?? change.old_page;
  const quote = change.new_quote ?? change.old_quote;
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs">
      <span className="font-semibold text-slate-800 min-w-0 break-words">{change.label}</span>
      <ChangeTypeBadge type="UNCHANGED" />
      {change.new_value && <span className="text-slate-600 min-w-0 break-words">{change.new_value}</span>}
      {page != null && (
        <Link
          href={sourceHref(contractId, page, quote, toVersion.id)}
          className="ml-auto inline-flex items-center gap-1 font-semibold text-blue-700 hover:text-blue-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
        >
          {sourceLocationText(toVersion, change.new_section ?? change.old_section, page)}
          <ArrowRight className="w-3 h-3" aria-hidden="true" />
        </Link>
      )}
    </li>
  );
}
