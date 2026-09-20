"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ClipboardCheck, FileText, Pencil, Search } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ContractDetailsDialog } from "@/components/ContractDetailsDialog";
import { UploadContractPanel } from "@/components/UploadContractPanel";
import { TypeAndTagChips } from "@/components/ContractChips";
import { ContractStatusBadge, ExtractionStatusChip } from "@/components/ui/Badges";
import { errorMessage, fetchContractTypes, fetchContracts, fetchTags } from "@/lib/api";
import { daysFromToday, formatDate } from "@/lib/format";
import type { ContractSummary, NameCount } from "@/lib/types";

type SortKey = "newest" | "title" | "expiry";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "newest", label: "Newest" },
  { value: "title", label: "Title A-Z" },
  { value: "expiry", label: "Expiring soonest" },
];

const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";
const selectClass = `block w-full sm:w-auto max-w-full px-3 py-2 rounded-xl border border-slate-300 bg-white text-sm text-slate-800 ${focusRing}`;

function matchesQuery(c: ContractSummary, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack = [c.title, c.counterparty ?? "", c.contract_type ?? "", ...(c.tags ?? []), ...c.parties.map((p) => p.name)];
  return haystack.some((t) => t.toLowerCase().includes(q));
}

/** Upcoming expiries (soonest first), then past ones (most recent first), then contracts with no expiry date. */
function expiryRank(c: ContractSummary): { group: number; days: number } {
  const days = daysFromToday(c.expiry_date);
  if (days === null) return { group: 2, days: 0 };
  return days >= 0 ? { group: 0, days } : { group: 1, days: -days };
}

function sortContracts(list: ContractSummary[], sort: SortKey): ContractSummary[] {
  const out = [...list];
  if (sort === "title") return out.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: "base" }));
  if (sort === "expiry") {
    return out.sort((a, b) => {
      const ra = expiryRank(a);
      const rb = expiryRank(b);
      return ra.group - rb.group || ra.days - rb.days || a.title.localeCompare(b.title);
    });
  }
  return out.sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function ContractRow({ c, onEdit }: { c: ContractSummary; onEdit: (c: ContractSummary) => void }) {
  const expiry = formatDate(c.expiry_date);
  const days = daysFromToday(c.expiry_date);
  const open = c.open_flag_count ?? 0;
  const high = c.high_flag_count ?? 0;
  return (
    <li className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 sm:p-5 flex flex-col sm:flex-row sm:items-start justify-between gap-3">
      <div className="min-w-0 space-y-2">
        <div className="flex items-start gap-2 min-w-0">
          <FileText className="w-4 h-4 shrink-0 mt-1 text-blue-600" aria-hidden="true" />
          <div className="min-w-0">
            <Link
              href={`/contracts/${c.id}`}
              className={`text-sm font-bold text-slate-900 hover:text-blue-600 break-words ${focusRing}`}
            >
              {c.title}
            </Link>
            {c.counterparty && <p className="text-xs text-slate-600 break-words">{c.counterparty}</p>}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <TypeAndTagChips contractType={c.contract_type} tags={c.tags} />
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <ContractStatusBadge status={c.status} />
          <ExtractionStatusChip status={c.extraction_status} />
          {open > 0 && (
            <Link
              href={`/review?contract=${encodeURIComponent(c.id)}`}
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap ${
                high > 0 ? "bg-red-100 text-red-800 border-red-200" : "bg-amber-100 text-amber-800 border-amber-200"
              } ${focusRing}`}
            >
              {high > 0 ? (
                <AlertTriangle className="w-3 h-3 shrink-0" aria-hidden="true" />
              ) : (
                <ClipboardCheck className="w-3 h-3 shrink-0" aria-hidden="true" />
              )}
              {open} to review{high > 0 ? `, ${high} high` : ""}
            </Link>
          )}
          <span className="text-slate-600">
            {expiry
              ? `Expires ${expiry}${days !== null && days < 0 ? " (past)" : ""}`
              : c.status === "READY"
                ? "No expiry date found"
                : "Expiry date pending"}
          </span>
        </div>
      </div>
      <button
        type="button"
        onClick={() => onEdit(c)}
        aria-haspopup="dialog"
        aria-label={`Edit details for ${c.title}`}
        className={`inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 shrink-0 ${focusRing}`}
      >
        <Pencil className="w-3.5 h-3.5" aria-hidden="true" />
        Edit details
      </button>
    </li>
  );
}

export default function ContractsLibraryPage() {
  const [contracts, setContracts] = useState<ContractSummary[] | null>(null);
  const [types, setTypes] = useState<NameCount[]>([]);
  const [tags, setTags] = useState<NameCount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [sort, setSort] = useState<SortKey>("newest");
  const [editing, setEditing] = useState<ContractSummary | null>(null);
  const [announce, setAnnounce] = useState("");

  const loadFacets = useCallback(() => {
    // The type and tag lists are conveniences: if they fail, the filters just have no options.
    fetchContractTypes().then(setTypes).catch(() => setTypes([]));
    fetchTags().then(setTags).catch(() => setTags([]));
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      setContracts(await fetchContracts());
    } catch (e: unknown) {
      setError(errorMessage(e, "Could not load your contracts."));
    }
  }, []);

  useEffect(() => {
    void load();
    loadFacets();
  }, [load, loadFacets]);

  const visible = useMemo(() => {
    if (!contracts) return [];
    const filtered = contracts.filter(
      (c) =>
        matchesQuery(c, query) &&
        (!typeFilter || (c.contract_type ?? "").toLowerCase() === typeFilter.toLowerCase()) &&
        (!tagFilter || (c.tags ?? []).some((t) => t.toLowerCase() === tagFilter.toLowerCase())),
    );
    return sortContracts(filtered, sort);
  }, [contracts, query, typeFilter, tagFilter, sort]);

  const filtersActive = query.trim() !== "" || typeFilter !== "" || tagFilter !== "";

  function clearFilters() {
    setQuery("");
    setTypeFilter("");
    setTagFilter("");
  }

  function handleUploaded(created: ContractSummary) {
    if (contracts) setContracts([created, ...contracts.filter((c) => c.id !== created.id)]);
    else void load();
  }

  function handleSaved(updated: ContractSummary) {
    setContracts((prev) => (prev ? prev.map((c) => (c.id === updated.id ? { ...c, ...updated } : c)) : prev));
    setEditing(null);
    setAnnounce(`Saved details for ${updated.title}.`);
    loadFacets();
  }

  return (
    <div>
      <PageHeader
        title="Contracts"
        subtitle="Every contract you have uploaded"
        breadcrumbs={[{ label: "Dashboard", href: "/" }, { label: "Contracts" }]}
      />

      <div className="p-4 sm:p-8 space-y-6 max-w-5xl mx-auto">
        <UploadContractPanel onUploaded={handleUploaded} />

        {error && !contracts ? (
          <div role="alert" className="p-4 rounded-2xl bg-red-50 border border-red-200 text-sm text-red-900 space-y-2">
            <p className="font-semibold">{error}</p>
            <button
              type="button"
              onClick={() => void load()}
              className={`px-3 py-1.5 rounded-xl bg-white border border-red-300 text-xs font-bold text-red-800 hover:bg-red-100 ${focusRing}`}
            >
              Try again
            </button>
          </div>
        ) : contracts === null ? (
          <p role="status" className="p-8 text-center text-sm text-slate-500">
            Loading contracts...
          </p>
        ) : contracts.length === 0 ? (
          <div className="p-8 text-center bg-white border border-slate-200 rounded-2xl space-y-1">
            <FileText className="w-8 h-8 mx-auto text-slate-400" aria-hidden="true" />
            <p className="text-sm font-semibold text-slate-800">No contracts yet.</p>
            <p className="text-xs text-slate-500">Upload a contract above to get started.</p>
          </div>
        ) : (
          <>
            <section aria-label="Find and filter contracts" className="flex flex-col lg:flex-row lg:items-end gap-4">
              <div className="flex-1 min-w-0">
                <label htmlFor="library-search" className="block text-xs font-semibold text-slate-600 mb-1">
                  Search
                </label>
                <div className="relative">
                  <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" aria-hidden="true" />
                  <input
                    id="library-search"
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder="Title, counterparty, type, tag or party"
                    className={`block w-full pl-9 pr-3 py-2 rounded-xl border border-slate-300 bg-white text-sm text-slate-900 ${focusRing}`}
                  />
                </div>
              </div>
              <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
                Type
                <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)} className={selectClass}>
                  <option value="">All types</option>
                  {types.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name} ({t.count})
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
                Tag
                <select value={tagFilter} onChange={(e) => setTagFilter(e.target.value)} className={selectClass}>
                  <option value="">All tags</option>
                  {tags.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name} ({t.count})
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
                Sort
                <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} className={selectClass}>
                  {SORT_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>
            </section>

            <div className="flex flex-wrap items-center gap-3">
              <p aria-live="polite" className="text-sm text-slate-700">
                {visible.length} of {contracts.length} {contracts.length === 1 ? "contract" : "contracts"}
              </p>
              {filtersActive && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className={`text-xs font-bold text-blue-700 hover:text-blue-900 underline ${focusRing}`}
                >
                  Clear filters
                </button>
              )}
            </div>

            {visible.length === 0 ? (
              <div className="p-8 text-center bg-white border border-slate-200 rounded-2xl space-y-2">
                <p className="text-sm font-semibold text-slate-800">No contracts match these filters.</p>
                <button
                  type="button"
                  onClick={clearFilters}
                  className={`px-3 py-1.5 rounded-xl bg-white border border-slate-300 text-xs font-bold text-slate-800 hover:bg-slate-50 ${focusRing}`}
                >
                  Clear filters
                </button>
              </div>
            ) : (
              <ul className="space-y-3">
                {visible.map((c) => (
                  <ContractRow key={c.id} c={c} onEdit={setEditing} />
                ))}
              </ul>
            )}
          </>
        )}

        <div role="status" className="sr-only">
          {announce}
        </div>
      </div>

      {editing && (
        <ContractDetailsDialog
          contract={editing}
          typeSuggestions={types.map((t) => t.name)}
          onClose={() => setEditing(null)}
          onSaved={handleSaved}
        />
      )}
    </div>
  );
}
