"use client";

import { use, useEffect, useMemo, useState } from "react";
import { Copy, Check, Download, HelpCircle } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ContractTabs } from "@/components/ui/Badges";
import { SourceRef } from "@/components/ui/SourceRef";
import {
  CompletedBadge,
  DeadlineTypeBadge,
  OverdueBadge,
  RuleTypeBadge,
  UnverifiedBadge,
  isUnverified,
} from "@/components/ui/DateBadges";
import { calendarUrl, errorMessage, fetchContractById, fetchSchedule } from "@/lib/api";
import { emptyStateText } from "@/lib/extraction";
import { daysFromToday, formatDate, monthLabel, recurrenceLabel } from "@/lib/format";
import { isProcessing } from "@/lib/types";
import type { ContractDetail, ScheduleEntry, ScheduleResponse, UndatedObligation } from "@/lib/types";

type View = "party" | "date";

const VISIBLE_REPEATS = 3;
const CONTRACT_SECTION = "Contract";

/** One obligation (or contract date) plus any repeats of it, so repeats do not flood the view. */
interface Item {
  main: ScheduleEntry;
  repeats: ScheduleEntry[];
}

function byDate(a: ScheduleEntry, b: ScheduleEntry): number {
  return a.due_on.localeCompare(b.due_on) || a.label.localeCompare(b.label);
}

/** Fold "occurrence" entries under their obligation. */
function buildItems(entries: ScheduleEntry[]): Item[] {
  const occurrences = new Map<string, ScheduleEntry[]>();
  const others: ScheduleEntry[] = [];
  for (const e of entries) {
    if (e.kind === "occurrence" && e.obligation_id) {
      const list = occurrences.get(e.obligation_id) ?? [];
      list.push(e);
      occurrences.set(e.obligation_id, list);
    } else {
      others.push(e);
    }
  }

  const items: Item[] = [];
  const claimed = new Set<string>();
  for (const main of others) {
    let repeats: ScheduleEntry[] = [];
    if (main.obligation_id && occurrences.has(main.obligation_id)) {
      claimed.add(main.obligation_id);
      repeats = (occurrences.get(main.obligation_id) ?? [])
        .filter((r) => r.id !== main.id && r.due_on !== main.due_on)
        .sort(byDate);
    }
    items.push({ main, repeats });
  }
  // Occurrences whose obligation has no entry of its own: the earliest one stands in as the main row.
  for (const [obligationId, list] of occurrences) {
    if (claimed.has(obligationId)) continue;
    const sorted = [...list].sort(byDate);
    items.push({ main: sorted[0], repeats: sorted.slice(1) });
  }
  return items.sort((a, b) => byDate(a.main, b.main));
}

function groupByParty(items: Item[]): { name: string; items: Item[] }[] {
  const groups = new Map<string, Item[]>();
  for (const it of items) {
    const key = it.main.party ?? CONTRACT_SECTION;
    const list = groups.get(key) ?? [];
    list.push(it);
    groups.set(key, list);
  }
  // Items are already in date order, so insertion order = order of each party's earliest date.
  const all = Array.from(groups, ([name, list]) => ({ name, items: list }));
  return [...all.filter((g) => g.name === CONTRACT_SECTION), ...all.filter((g) => g.name !== CONTRACT_SECTION)];
}

function groupByMonth(items: Item[]): { label: string; items: Item[] }[] {
  const groups: { label: string; items: Item[] }[] = [];
  for (const it of items) {
    const label = monthLabel(it.main.due_on) ?? it.main.due_on;
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(it);
    else groups.push({ label, items: [it] });
  }
  return groups;
}

function overdueText(e: ScheduleEntry): string | null {
  if (!e.is_overdue || e.status === "COMPLETED") return null;
  const days = daysFromToday(e.due_on);
  if (days == null || days >= 0) return "Overdue";
  const n = Math.abs(days);
  return `Overdue by ${n} ${n === 1 ? "day" : "days"}`;
}

function EntryHeadline({ e, showParty }: { e: ScheduleEntry; showParty: boolean }) {
  const overdue = overdueText(e);
  const completed = e.status === "COMPLETED";
  return (
    <div className="space-y-2 font-sans min-w-0">
      <p className={`font-medium leading-relaxed break-words ${completed ? "text-slate-400" : "text-white"}`}>
        {showParty && e.party && (
          <span className="mr-2 text-xs font-bold text-blue-300 bg-blue-950/60 border border-blue-900 px-2 py-0.5 rounded">
            {e.party}
          </span>
        )}
        {e.label}
      </p>
      {e.action && e.action !== e.label && <p className="text-xs text-slate-300 break-words">{e.action}</p>}
      <div className="flex flex-wrap items-center gap-1.5">
        {(e.kind === "renewal_notice" || e.kind === "contract_date") && (
          <DeadlineTypeBadge type={e.kind === "renewal_notice" ? "RENEWAL_NOTICE" : "OTHER"} />
        )}
        <RuleTypeBadge dark ruleType={e.rule_type} recurrence={e.recurrence} />
        {completed && <CompletedBadge dark />}
        {overdue && <OverdueBadge dark label={overdue} />}
        {isUnverified(e.verification_status) && <UnverifiedBadge dark />}
      </div>
      {e.basis && (
        <p className="text-xs text-slate-400 leading-relaxed">
          <span className="font-semibold text-slate-300">How this date was worked out:</span> {e.basis}
        </p>
      )}
    </div>
  );
}

function ItemCard({ contractId, item, showParty }: { contractId: string; item: Item; showParty: boolean }) {
  const [showAll, setShowAll] = useState(false);
  const { main, repeats } = item;
  const shown = showAll ? repeats : repeats.slice(0, VISIBLE_REPEATS);
  const hidden = repeats.length - shown.length;
  const overdue = overdueText(main) !== null;

  return (
    <div className="space-y-3">
      <div
        className={`grid grid-cols-1 sm:grid-cols-12 items-baseline gap-2 sm:gap-4 text-sm font-mono ${
          overdue ? "border-l-4 border-red-500 pl-3 -ml-3 bg-red-950/20 rounded-r-lg py-2" : ""
        }`}
      >
        <span className={`sm:col-span-4 font-medium tracking-wide ${overdue ? "text-red-200" : "text-slate-300"}`}>
          {formatDate(main.due_on) ?? main.due_on}
        </span>
        <div className="sm:col-span-8 space-y-2 min-w-0">
          <EntryHeadline e={main} showParty={showParty} />
          <SourceRef
            dark
            contractId={contractId}
            page={main.source_page}
            section={main.source_section}
            quote={main.source_quote}
          />
        </div>
      </div>

      {repeats.length > 0 && (
        <div className="sm:ml-[33.333%] ml-3 pl-3 border-l border-slate-700 space-y-2">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-purple-300">
            Repeats{main.recurrence ? ` · ${recurrenceLabel(main.recurrence)}` : ""} ({repeats.length} more)
          </p>
          <ul className="space-y-1.5">
            {shown.map((r) => {
              const od = overdueText(r);
              return (
                <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                  <span className={`font-mono ${od ? "text-red-300 font-semibold" : "text-slate-300"}`}>
                    {formatDate(r.due_on) ?? r.due_on}
                  </span>
                  {od && <OverdueBadge dark label={od} />}
                  {r.status === "COMPLETED" && <CompletedBadge dark />}
                  {isUnverified(r.verification_status) && <UnverifiedBadge dark />}
                </li>
              );
            })}
          </ul>
          {(hidden > 0 || showAll) && repeats.length > VISIBLE_REPEATS && (
            <button
              type="button"
              onClick={() => setShowAll((v) => !v)}
              aria-expanded={showAll}
              className="text-xs font-semibold text-blue-300 hover:text-blue-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 rounded"
            >
              {showAll ? "Show fewer" : `Show ${hidden} more`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ItemList({ contractId, items, showParty }: { contractId: string; items: Item[]; showParty: boolean }) {
  return (
    <div className="space-y-4">
      {items.map((item, idx) => (
        <div key={item.main.id} className="space-y-3">
          <ItemCard contractId={contractId} item={item} showParty={showParty} />
          {idx < items.length - 1 && (
            <div className="text-slate-500 font-mono text-xs pl-8 py-0.5" aria-hidden="true">
              ↓
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function UndatedPanel({ contractId, undated }: { contractId: string; undated: UndatedObligation[] }) {
  return (
    <section
      aria-labelledby="undated-heading"
      className="bg-[#18181b] rounded-3xl p-6 sm:p-8 border border-amber-900/60 shadow-2xl space-y-4"
    >
      <div className="space-y-1">
        <h2 id="undated-heading" className="text-sm font-bold text-amber-200 flex items-center gap-2">
          <HelpCircle className="w-4 h-4" aria-hidden="true" />
          Obligations without a calculable date ({undated.length})
        </h2>
        <p className="text-xs text-slate-400">
          ContractLens could not turn these into calendar dates, so none is guessed. The contract&apos;s own wording is shown
          instead. Work out the date yourself from the source.
        </p>
      </div>
      <ul className="space-y-4">
        {undated.map((u) => (
          <li key={u.obligation_id} className="space-y-2 border-t border-slate-800 pt-4 first:border-t-0 first:pt-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-bold text-blue-300 bg-blue-950/60 border border-blue-900 px-2 py-0.5 rounded">
                {u.party}
              </span>
              {isUnverified(u.verification_status) && <UnverifiedBadge dark />}
            </div>
            <p className="text-sm font-medium text-white break-words">{u.action}</p>
            <p className="text-xs text-slate-300 break-words">
              <span className="font-semibold text-slate-200">The contract says:</span>{" "}
              {u.due_rule ? <span className="italic">&ldquo;{u.due_rule}&rdquo;</span> : "No due rule stated."}
            </p>
            {u.reason && (
              <p className="text-xs text-amber-200/90 break-words">
                <span className="font-semibold">Why there is no date:</span> {u.reason}
              </p>
            )}
            <SourceRef
              dark
              contractId={contractId}
              page={u.source_page}
              section={u.source_section}
              quote={u.source_quote}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

function timelineAsText(schedule: ScheduleResponse): string {
  const lines: string[] = [];
  for (const it of buildItems(schedule.entries)) {
    const rows = [it.main, ...it.repeats];
    for (const e of rows) {
      const parts = [
        formatDate(e.due_on) ?? e.due_on,
        e.party ? `${e.label} (${e.party})` : e.label,
      ];
      const notes: string[] = [];
      if (e.is_overdue && e.status !== "COMPLETED") notes.push("OVERDUE");
      if (e.status === "COMPLETED") notes.push("completed");
      if (isUnverified(e.verification_status)) notes.push("unverified");
      if (e.basis) notes.push(e.basis);
      if (notes.length > 0) parts.push(notes.join("; "));
      lines.push(parts.join("\t"));
    }
  }
  if (schedule.undated.length > 0) {
    lines.push("", "Obligations without a calculable date:");
    for (const u of schedule.undated) {
      lines.push(`${u.party}: ${u.action}\tContract says: "${u.due_rule}"${u.reason ? `\t${u.reason}` : ""}`);
    }
  }
  return lines.join("\n");
}

export default function TimelinePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [contract, setContract] = useState<ContractDetail | null>(null);
  const [schedule, setSchedule] = useState<ScheduleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<View>("party");
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function loadData() {
      try {
        const [sData, cData] = await Promise.all([
          fetchSchedule(id),
          // The contract only adds context (title, extraction status); the timeline still works without it.
          fetchContractById(id).catch(() => null),
        ]);
        if (cancelled) return;
        setSchedule(sData);
        setContract(cData);
      } catch (err: unknown) {
        if (!cancelled) setLoadError(errorMessage(err, "Could not load the timeline."));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadData();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const items = useMemo(() => buildItems(schedule?.entries ?? []), [schedule]);
  const partyGroups = useMemo(() => groupByParty(items), [items]);
  const monthGroups = useMemo(() => groupByMonth(items), [items]);

  async function handleCopyTimeline() {
    if (!schedule) return;
    try {
      await navigator.clipboard.writeText(timelineAsText(schedule));
      setCopyError(null);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (e: unknown) {
      setCopyError(errorMessage(e, "Could not copy to the clipboard."));
    }
  }

  const ex = contract?.extraction_status;
  const incomplete =
    !!contract && (isProcessing(contract.status) || ex === "partial" || ex === "unavailable" || ex === "legacy" || ex === "unsupported");
  const hasEntries = (schedule?.entries.length ?? 0) > 0;
  const undated = schedule?.undated ?? [];
  const nothingAtAll = !!schedule && !hasEntries && undated.length === 0;

  const controlBtn =
    "inline-flex items-center gap-1.5 px-3 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400";

  return (
    <div className="min-h-full bg-slate-950 text-slate-100">
      <PageHeader
        title={contract ? `${contract.title} — Schedule` : "Contract Schedule"}
        subtitle="Renewal dates and commitment occurrences, with how each date was worked out"
        breadcrumbs={[
          { label: "Dashboard", href: "/" },
          { label: contract?.title || "Contract", href: `/contracts/${id}` },
          { label: "Schedule" },
        ]}
      />
      <ContractTabs contractId={id} active="timeline" />

      <div className="p-4 sm:p-8 max-w-4xl mx-auto space-y-6">
        {/* Title + controls */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-bold flex items-center gap-2 text-white">
            <span className="text-2xl" aria-hidden="true">🗓️</span> Schedule
          </h1>
          {schedule && (
            <div className="flex flex-wrap items-center gap-2">
              <div role="group" aria-label="Timeline view" className="flex items-center gap-1 bg-slate-900 rounded-xl p-1 border border-slate-800">
                {(["party", "date"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    onClick={() => setView(v)}
                    aria-pressed={view === v}
                    className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${
                      view === v ? "bg-blue-600 text-white" : "text-slate-300 hover:bg-slate-800"
                    }`}
                  >
                    {v === "party" ? "By party" : "By date"}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={handleCopyTimeline}
                className={controlBtn}
                title="Copy timeline to clipboard as plain text"
                aria-label="Copy timeline to clipboard"
              >
                {copied ? <Check className="w-4 h-4 text-emerald-400" aria-hidden="true" /> : <Copy className="w-4 h-4" aria-hidden="true" />}
                {copied ? "Copied" : "Copy timeline"}
              </button>
              <a href={calendarUrl(id)} download className={controlBtn}>
                <Download className="w-4 h-4" aria-hidden="true" />
                Download .ics
              </a>
            </div>
          )}
        </div>
        <div aria-live="polite" className="sr-only">{copied ? "Timeline copied to clipboard." : ""}</div>
        {copyError && <p role="alert" className="text-xs text-red-300">{copyError}</p>}

        {loading ? (
          <div role="status" className="p-12 bg-[#18181b] rounded-3xl border border-slate-800 text-center text-sm text-slate-400">
            Loading timeline...
          </div>
        ) : loadError ? (
          <div role="alert" className="p-12 bg-[#18181b] rounded-3xl border border-red-900 text-center text-sm text-red-300">
            {loadError}
          </div>
        ) : (
          <>
            <p className="text-xs text-slate-400">
              AI-assisted, not legal advice. Every date shows where it came from or how it was calculated. Check the source
              passage before relying on it.
            </p>

            {incomplete && (
              <p className="text-xs text-amber-300 bg-amber-950/40 border border-amber-800/60 rounded-xl px-3 py-2">
                {contract && isProcessing(contract.status)
                  ? "This contract is still being processed, so this timeline may be incomplete."
                  : "Extraction for this contract was unavailable, partial or not fully verified, so this timeline may be incomplete or missing dates. Check each source reference."}
              </p>
            )}

            {nothingAtAll ? (
              <div className="p-12 bg-[#18181b] rounded-3xl border border-slate-800 text-center text-sm text-slate-400">
                {contract
                  ? emptyStateText(contract, "dates or obligations", "No dated items or obligations were found in this contract.")
                  : "Nothing was returned for this timeline. If extraction did not complete, dates may be missing."}
              </div>
            ) : (
              <>
                {hasEntries ? (
                  <div className="bg-[#18181b] rounded-3xl p-6 sm:p-8 border border-slate-800 shadow-2xl space-y-8">
                    {view === "party"
                      ? partyGroups.map((g) => (
                          <section key={g.name} aria-label={`${g.name} dates`} className="space-y-4">
                            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-400 border-b border-slate-800 pb-2">
                              {g.name === CONTRACT_SECTION ? "Contract (dates and renewal notice)" : g.name}
                            </h2>
                            <ItemList contractId={id} items={g.items} showParty={false} />
                          </section>
                        ))
                      : monthGroups.map((g) => (
                          <section key={g.label} aria-label={g.label} className="space-y-4">
                            <h2 className="text-xs font-bold uppercase tracking-wider text-slate-400 border-b border-slate-800 pb-2">
                              {g.label}
                            </h2>
                            <ItemList contractId={id} items={g.items} showParty />
                          </section>
                        ))}
                  </div>
                ) : (
                  <div className="p-8 bg-[#18181b] rounded-3xl border border-slate-800 text-center text-sm text-slate-400">
                    No calendar dates could be calculated for this contract. The obligations below have no date of their own.
                  </div>
                )}

                {undated.length > 0 && <UndatedPanel contractId={id} undated={undated} />}
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
