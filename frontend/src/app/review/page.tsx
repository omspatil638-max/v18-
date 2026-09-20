"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { ClipboardCheck, Info } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ReviewFlagCard } from "@/components/ReviewFlagCard";
import { SEVERITY_ORDER, severityLabel } from "@/components/ui/FlagBadge";
import { FLAGS_CHANGED_EVENT, errorMessage, fetchContracts, fetchFlagQueue, fetchFlagSummary } from "@/lib/api";
import type {
  ContractSummary,
  FlagSeverity,
  FlagStatus,
  FlagSummary,
  QueueFlag,
  ReviewFlag,
} from "@/lib/types";

type StatusFilter = FlagStatus | "ALL";

const STATUS_TABS: { value: StatusFilter; label: string }[] = [
  { value: "OPEN", label: "Open" },
  { value: "RESOLVED", label: "Resolved" },
  { value: "DISMISSED", label: "Dismissed" },
  { value: "ALL", label: "All" },
];

function parseStatus(v: string | null): StatusFilter {
  return v === "RESOLVED" || v === "DISMISSED" || v === "ALL" ? v : "OPEN";
}

function parseSeverity(v: string | null): FlagSeverity | "" {
  return v === "HIGH" || v === "MEDIUM" || v === "LOW" ? v : "";
}

const selectClass =
  "block w-full sm:w-auto max-w-full px-3 py-2 rounded-xl border border-slate-300 bg-white text-sm text-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";

function SummaryChip({ label, value, tone }: { label: string; value: number | null; tone: string }) {
  return (
    <div className={`px-4 py-3 rounded-2xl border ${tone}`}>
      <p className="text-[11px] font-bold uppercase tracking-wider opacity-80">{label}</p>
      <p className="text-2xl font-bold leading-tight">{value ?? "–"}</p>
    </div>
  );
}

function ReviewContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const status = parseStatus(searchParams.get("status"));
  const severity = parseSeverity(searchParams.get("severity"));
  const contractId = searchParams.get("contract") ?? "";

  const [flags, setFlags] = useState<QueueFlag[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<FlagSummary | null>(null);
  const [contracts, setContracts] = useState<ContractSummary[] | null>(null);
  // Contract options accumulate across fetches so the select keeps every contract seen so far.
  const [contractNames, setContractNames] = useState<Record<string, string>>({});
  const [reloadKey, setReloadKey] = useState(0);

  const setParam = useCallback(
    (key: "status" | "severity" | "contract", value: string) => {
      const params = new URLSearchParams(searchParams.toString());
      if (!value || (key === "status" && value === "OPEN")) params.delete(key);
      else params.set(key, value);
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams]
  );

  // The queue is fetched by status and severity; the contract filter is applied below so the
  // contract select can always list every contract that has flags under the current filters.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchFlagQueue({ status, severity: severity || undefined })
      .then((data) => {
        if (cancelled) return;
        setFlags(data);
        setError(null);
        setContractNames((prev) => {
          const next = { ...prev };
          for (const f of data) next[f.contract_id] = f.contract_title;
          return next;
        });
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorMessage(e, "Could not load review flags."));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [status, severity, reloadKey]);

  // Summary counts and contract states (used to word the empty state honestly).
  const loadSummary = useCallback(() => {
    fetchFlagSummary()
      .then(setSummary)
      .catch(() => setSummary(null));
  }, []);

  useEffect(() => {
    loadSummary();
    fetchContracts()
      .then(setContracts)
      .catch(() => setContracts(null));
    window.addEventListener(FLAGS_CHANGED_EVENT, loadSummary);
    return () => window.removeEventListener(FLAGS_CHANGED_EVENT, loadSummary);
  }, [loadSummary]);

  function handleUpdated(updated: ReviewFlag) {
    setFlags((prev) => prev.map((f) => (f.id === updated.id ? { ...f, ...updated } : f)));
  }

  const visible = useMemo(
    () => (contractId ? flags.filter((f) => f.contract_id === contractId) : flags),
    [flags, contractId]
  );

  const contractOptions = useMemo(
    () =>
      Object.entries(contractNames)
        .map(([id, title]) => ({ id, title }))
        .sort((a, b) => a.title.localeCompare(b.title)),
    [contractNames]
  );
  const selectedKnown = !contractId || contractId in contractNames;

  const grouped = SEVERITY_ORDER.map((sev) => ({
    severity: sev,
    items: visible.filter((f) => f.severity === sev),
  })).filter((g) => g.items.length > 0);

  // "Nothing needs review" is only claimed when every contract finished processing cleanly.
  const analysisIncomplete = contracts?.some(
    (c) =>
      c.status === "PENDING" ||
      c.status === "PROCESSING" ||
      c.status === "FAILED" ||
      c.extraction_status === "partial" ||
      c.extraction_status === "unavailable"
  );
  const filtered = status !== "OPEN" || severity !== "" || contractId !== "";

  function emptyState() {
    if (filtered) {
      return {
        title: "No flags match these filters.",
        body: "Try a different status, severity or contract.",
      };
    }
    if (contracts && contracts.length === 0) {
      return {
        title: "No contracts yet.",
        body: "Upload a contract and any items that need a human look will appear here.",
      };
    }
    if (analysisIncomplete === false) {
      return {
        title: "Nothing needs review.",
        body: "Flags are automatic checks and do not guarantee that nothing is wrong. Read the contracts yourself before relying on them.",
      };
    }
    return {
      title: "No open review flags right now.",
      body:
        analysisIncomplete === true
          ? "Some contracts are still processing or were only partly analysed, so review flags may be incomplete."
          : "Flags are automatic checks and may be incomplete.",
    };
  }

  return (
    <div>
      <PageHeader
        title="Needs review"
        subtitle="Reasons a person should look at a contract, across all your contracts"
        breadcrumbs={[{ label: "Dashboard", href: "/" }, { label: "Needs review" }]}
      />

      <div className="p-4 sm:p-8 space-y-6 max-w-5xl mx-auto">
        <div role="note" className="flex items-start gap-2 text-xs text-slate-600 bg-slate-100 border border-slate-200 rounded-xl px-3 py-2">
          <Info className="w-4 h-4 shrink-0 mt-px text-slate-500" aria-hidden="true" />
          <p>
            <span className="font-semibold text-slate-700">These are prompts for a human to check, not legal conclusions.</span>{" "}
            ContractLens flags items automatically, so a contract with no flags can still contain problems.
          </p>
        </div>

        {/* Summary chips (open flags across all contracts, regardless of filters) */}
        <section aria-label="Open review flags summary" className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <SummaryChip label="Open" value={summary?.open ?? null} tone="bg-white border-slate-200 text-slate-900" />
          <SummaryChip label="High" value={summary?.high ?? null} tone="bg-red-50 border-red-200 text-red-900" />
          <SummaryChip label="Medium" value={summary?.medium ?? null} tone="bg-amber-50 border-amber-200 text-amber-900" />
          <SummaryChip label="Low" value={summary?.low ?? null} tone="bg-slate-50 border-slate-200 text-slate-800" />
        </section>

        {/* Filters */}
        <section aria-label="Filters" className="flex flex-col lg:flex-row lg:items-end gap-4">
          <div role="group" aria-label="Status" className="flex flex-wrap gap-1 p-1 bg-slate-100 rounded-xl self-start">
            {STATUS_TABS.map((tab) => {
              const active = tab.value === status;
              return (
                <button
                  key={tab.value}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setParam("status", tab.value)}
                  className={`px-3 py-1.5 rounded-lg text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 ${
                    active ? "bg-white text-blue-700 shadow-sm" : "text-slate-600 hover:text-slate-900"
                  }`}
                >
                  {tab.label}
                </button>
              );
            })}
          </div>

          <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600">
            Severity
            <select
              value={severity}
              onChange={(e) => setParam("severity", e.target.value)}
              className={selectClass}
            >
              <option value="">All severities</option>
              {SEVERITY_ORDER.map((s) => (
                <option key={s} value={s}>
                  {severityLabel(s)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs font-semibold text-slate-600 min-w-0">
            Contract
            <select
              value={contractId}
              onChange={(e) => setParam("contract", e.target.value)}
              className={selectClass}
            >
              <option value="">All contracts</option>
              {!selectedKnown && <option value={contractId}>Selected contract</option>}
              {contractOptions.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
          </label>
        </section>

        {/* Results */}
        <div aria-busy={loading}>
          {loading ? (
            <p role="status" className="p-8 text-center text-sm text-slate-500">Loading review flags...</p>
          ) : error ? (
            <div role="alert" className="p-4 rounded-2xl bg-red-50 border border-red-200 text-sm text-red-900 space-y-2">
              <p className="font-semibold">{error}</p>
              <button
                type="button"
                onClick={() => setReloadKey((k) => k + 1)}
                className="px-3 py-1.5 rounded-xl bg-white border border-red-300 text-xs font-bold text-red-800 hover:bg-red-100"
              >
                Try again
              </button>
            </div>
          ) : visible.length === 0 ? (
            <div className="p-8 text-center bg-white border border-slate-200 rounded-2xl space-y-1">
              <ClipboardCheck className="w-8 h-8 mx-auto text-slate-400" aria-hidden="true" />
              <p className="text-sm font-semibold text-slate-800">{emptyState().title}</p>
              <p className="text-xs text-slate-500 max-w-md mx-auto">{emptyState().body}</p>
            </div>
          ) : (
            <div className="space-y-8">
              {grouped.map((group) => (
                <section key={group.severity} aria-labelledby={`sev-${group.severity}`} className="space-y-3">
                  <h2
                    id={`sev-${group.severity}`}
                    className="text-xs font-bold text-slate-500 uppercase tracking-wider"
                  >
                    {severityLabel(group.severity)} priority ({group.items.length})
                  </h2>
                  <div className="space-y-3">
                    {group.items.map((f) => (
                      <ReviewFlagCard
                        key={f.id}
                        flag={f}
                        contractTitle={f.contract_title}
                        versionLabel={f.version_label}
                        onUpdated={handleUpdated}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<p className="p-8 text-center text-sm text-slate-500">Loading review flags...</p>}>
      <ReviewContent />
    </Suspense>
  );
}
