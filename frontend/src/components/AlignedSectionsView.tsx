"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { ChangeTypeBadge, MaterialBadge } from "@/components/ui/ChangeBadges";
import { sourceLocationText } from "@/components/VersionChangeCard";
import type { VersionRef } from "@/components/VersionChangeCard";
import { sourceHref } from "@/lib/format";
import type { AlignedSection, AlignedSide, DiffSegment } from "@/lib/types";

const CLAMP_THRESHOLD = 500;
const LINK_QUOTE_CHARS = 160;

/** Words of one side of the diff. Segments carry no surrounding spaces, so they are joined with one. */
function DiffText({ segments, side }: { segments: DiffSegment[]; side: "old" | "new" }) {
  return (
    <>
      {segments.map((seg, i) => {
        if (seg.op === "equal") return <span key={i}>{seg.text} </span>;
        if (seg.op === "delete") {
          if (side !== "old") return null;
          return (
            <del key={i} className="bg-red-100 text-red-900 line-through decoration-red-500 rounded-sm px-0.5">
              {seg.text}
              <span className="sr-only"> (removed)</span>{" "}
            </del>
          );
        }
        if (side !== "new") return null;
        return (
          <ins key={i} className="bg-green-100 text-green-900 font-semibold underline decoration-green-600 rounded-sm px-0.5">
            {seg.text}
            <span className="sr-only"> (added)</span>{" "}
          </ins>
        );
      })}
    </>
  );
}

function Column({
  contractId,
  version,
  side,
  which,
  section,
  absentText,
}: {
  contractId: string;
  version: VersionRef;
  side: AlignedSide | null;
  which: "old" | "new";
  section: AlignedSection;
  absentText: string;
}) {
  const [expanded, setExpanded] = useState(false);

  if (!side) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-slate-50/60 p-3 text-xs italic text-slate-500 min-h-[3rem]">
        {absentText}
      </div>
    );
  }

  const text = side.text ?? "";
  const long = text.length > CLAMP_THRESHOLD;
  const location = sourceLocationText(version, side.label, side.page);
  const useDiff = section.change_type === "MODIFIED" && section.diff.length > 0;

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3 space-y-2 min-w-0">
      {side.page != null ? (
        <Link
          href={sourceHref(contractId, side.page, text.trim().slice(0, LINK_QUOTE_CHARS), version.id)}
          title={`Open this passage in the source viewer for v${version.version_number}`}
          className="inline-flex items-center gap-1 text-xs font-semibold text-blue-700 hover:text-blue-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
        >
          {location}
          <ArrowRight className="w-3 h-3 shrink-0" aria-hidden="true" />
        </Link>
      ) : (
        <span className="text-xs font-semibold text-slate-700">{location}</span>
      )}
      <p
        className={`text-xs leading-relaxed text-slate-800 whitespace-pre-wrap break-words ${
          expanded || !long ? "" : "line-clamp-6"
        }`}
      >
        {useDiff ? <DiffText segments={section.diff} side={which} /> : text}
      </p>
      {long && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="text-[11px] font-semibold text-blue-700 hover:text-blue-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
        >
          {expanded ? "Show less" : "Show full text"}
        </button>
      )}
    </div>
  );
}

function SectionRow({
  contractId,
  fromVersion,
  toVersion,
  section,
}: {
  contractId: string;
  fromVersion: VersionRef;
  toVersion: VersionRef;
  section: AlignedSection;
}) {
  const title = section.new?.label ?? section.old?.label ?? "Untitled clause";
  return (
    <li
      className={`rounded-2xl border bg-slate-50/60 p-3 space-y-2 ${
        section.is_material ? "border-orange-300" : "border-slate-200"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-bold text-slate-900 mr-1 min-w-0 break-words">{title}</h4>
        <ChangeTypeBadge type={section.change_type} />
        {section.is_material && <MaterialBadge />}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Column
          contractId={contractId}
          version={fromVersion}
          side={section.old}
          which="old"
          section={section}
          absentText={`Not in v${fromVersion.version_number}`}
        />
        <Column
          contractId={contractId}
          version={toVersion}
          side={section.new}
          which="new"
          section={section}
          absentText={`Not in v${toVersion.version_number}`}
        />
      </div>
    </li>
  );
}

type Block =
  | { kind: "row"; section: AlignedSection; key: string }
  | { kind: "unchanged"; sections: AlignedSection[]; key: string };

/** Keeps document order; consecutive unchanged clauses fold into one disclosure. */
function toBlocks(sections: AlignedSection[]): Block[] {
  const blocks: Block[] = [];
  sections.forEach((section, i) => {
    if (section.change_type === "UNCHANGED") {
      const last = blocks[blocks.length - 1];
      if (last && last.kind === "unchanged") last.sections.push(section);
      else blocks.push({ kind: "unchanged", sections: [section], key: `u${i}` });
    } else {
      blocks.push({ kind: "row", section, key: `r${i}` });
    }
  });
  return blocks;
}

export function AlignedSectionsView({
  contractId,
  fromVersion,
  toVersion,
  sections,
}: {
  contractId: string;
  fromVersion: VersionRef;
  toVersion: VersionRef;
  sections: AlignedSection[];
}) {
  if (sections.length === 0) {
    return (
      <p className="text-sm text-slate-500 text-center py-8">
        No clauses could be aligned between these two versions.
      </p>
    );
  }
  const blocks = toBlocks(sections);
  return (
    <div className="space-y-3">
      <ul className="space-y-3" aria-label="Clauses in document order">
        {blocks.map((block) =>
          block.kind === "row" ? (
            <SectionRow
              key={block.key}
              contractId={contractId}
              fromVersion={fromVersion}
              toVersion={toVersion}
              section={block.section}
            />
          ) : (
            <li key={block.key}>
              <details className="rounded-2xl border border-slate-200 bg-white">
                <summary className="cursor-pointer select-none px-3 py-2 text-xs font-bold text-slate-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600">
                  {block.sections.length} unchanged {block.sections.length === 1 ? "clause" : "clauses"}
                </summary>
                <ul className="p-3 space-y-3 border-t border-slate-100">
                  {block.sections.map((section, i) => (
                    <SectionRow
                      key={`${block.key}-${i}`}
                      contractId={contractId}
                      fromVersion={fromVersion}
                      toVersion={toVersion}
                      section={section}
                    />
                  ))}
                </ul>
              </details>
            </li>
          ),
        )}
      </ul>
    </div>
  );
}
