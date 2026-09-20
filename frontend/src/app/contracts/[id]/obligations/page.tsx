"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { CheckCircle } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ContractTabs, ObligationStatusBadge } from "@/components/ui/Badges";
import { StatusChip } from "@/components/ui/StatusChip";
import { ConfidenceBadge } from "@/components/ui/ConfidenceBadge";
import { SourceRef } from "@/components/ui/SourceRef";
import { OverdueBadge, RuleTypeBadge } from "@/components/ui/DateBadges";
import { errorMessage, fetchContractById, fetchObligations, updateObligationStatus } from "@/lib/api";
import { emptyStateText } from "@/lib/extraction";
import { daysFromToday, formatDate, recurrenceLabel } from "@/lib/format";
import type { ContractDetail, Obligation, ObligationStatus } from "@/lib/types";

const FILTERS = ["ALL", "PENDING", "COMPLETED", "OVERDUE"] as const;
type Filter = (typeof FILTERS)[number];

export default function ObligationsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [contract, setContract] = useState<ContractDetail | null>(null);
  const [obligations, setObligations] = useState<Obligation[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [filterStatus, setFilterStatus] = useState<Filter>("ALL");

  useEffect(() => {
    async function loadData() {
      try {
        const [cData, obData] = await Promise.all([fetchContractById(id), fetchObligations(id)]);
        setContract(cData);
        setObligations(obData);
      } catch (err: unknown) {
        setLoadError(errorMessage(err, "Could not load obligations."));
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [id]);

  async function handleStatusToggle(obId: string, currentStatus: ObligationStatus) {
    const nextStatus: ObligationStatus = currentStatus === "COMPLETED" ? "PENDING" : "COMPLETED";
    setActionError(null);

    // Optimistic UI update
    setObligations((prev) => prev.map((o) => (o.id === obId ? { ...o, status: nextStatus } : o)));

    try {
      await updateObligationStatus(obId, nextStatus);
    } catch (e: unknown) {
      setActionError(errorMessage(e, "Failed to update the obligation status."));
      // Revert on error
      setObligations((prev) => prev.map((o) => (o.id === obId ? { ...o, status: currentStatus } : o)));
    }
  }

  // "Overdue" uses the server's date-based is_overdue flag; completed items are never overdue.
  const filteredObligations = obligations.filter((o) => {
    if (filterStatus === "ALL") return true;
    if (filterStatus === "OVERDUE") return o.is_overdue && o.status !== "COMPLETED";
    return o.status === filterStatus;
  });

  return (
    <div>
      <PageHeader
        title={contract ? `${contract.title} — Commitments` : "Commitments"}
        subtitle="Who must do what, by when, and where the contract says it"
        breadcrumbs={[
          { label: "Dashboard", href: "/" },
          { label: contract?.title || "Contract", href: `/contracts/${id}` },
          { label: "Commitments" },
        ]}
        actions={
          <Link
            href={`/contracts/${id}/timeline`}
            className="inline-flex items-center px-3 py-2 rounded-xl border border-slate-300 bg-white text-xs font-bold text-slate-800 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            View schedule
          </Link>
        }
      />
      <ContractTabs contractId={id} active="obligations" />

      <div className="p-4 sm:p-8 space-y-6">
        <p role="note" className="rounded-xl border border-blue-100 bg-blue-50 px-3 py-2 text-xs text-blue-900">
          Use this view to complete or reopen commitments. For renewal dates and recurring occurrences, open the schedule.
          Every item links back to its source evidence.
        </p>
        {/* Filters bar */}
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter obligations by status">
            {FILTERS.map((st) => (
              <button
                key={st}
                type="button"
                onClick={() => setFilterStatus(st)}
                aria-pressed={filterStatus === st}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
                  filterStatus === st
                    ? "bg-blue-600 text-white shadow-sm"
                    : "bg-white border border-slate-200 text-slate-600 hover:bg-slate-50"
                }`}
              >
                {st}
              </button>
            ))}
          </div>
          <p className="text-xs text-slate-500 font-medium">
            Showing {filteredObligations.length} of {obligations.length} obligations
          </p>
        </div>

        {actionError && (
          <div role="alert" className="p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-800">
            {actionError}
          </div>
        )}

        {loading ? (
          <div className="p-8 bg-white rounded-xl border border-slate-200 text-center text-sm text-slate-400">
            Loading obligations...
          </div>
        ) : loadError ? (
          <div role="alert" className="p-8 bg-white rounded-xl border border-red-200 text-center text-sm text-red-600">
            {loadError}
          </div>
        ) : obligations.length === 0 ? (
          <div className="p-8 bg-white rounded-xl border border-slate-200 text-center text-sm text-slate-500">
            {contract
              ? emptyStateText(contract, "obligations", "No obligations were found in this contract.")
              : "No obligations found."}
          </div>
        ) : filteredObligations.length === 0 ? (
          <div className="p-8 bg-white rounded-xl border border-slate-200 text-center text-sm text-slate-500">
            No obligations match the current filter.
          </div>
        ) : (
          <div className="space-y-3">
            {filteredObligations.map((ob) => {
              const dueDate = formatDate(ob.due_date, "long");
              const overdue = ob.is_overdue && ob.status !== "COMPLETED";
              const overdueDays = overdue ? daysFromToday(ob.due_date) : null;
              const recurrence = recurrenceLabel(ob.recurrence);
              return (
                <div
                  key={ob.id}
                  className={`rounded-xl border p-4 sm:p-5 shadow-sm space-y-3 transition-all ${
                    overdue
                      ? "bg-red-50/60 border-red-400 border-l-4 hover:border-red-500"
                      : "bg-white border-slate-200 hover:border-blue-300"
                  }`}
                >
                  <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3 sm:gap-4">
                    <div className="space-y-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-xs font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded break-words">
                          {ob.responsible_party}
                        </span>
                        {!(overdue && ob.status === "OVERDUE") && <ObligationStatusBadge status={ob.status} />}
                        {overdue && <OverdueBadge />}
                        <StatusChip status={ob.verification_status} />
                        <ConfidenceBadge confidence={ob.confidence} />
                      </div>
                      <h3 className="text-sm font-semibold text-slate-900 leading-snug break-words">{ob.action}</h3>
                    </div>

                    {/* Quick status toggle button (tracks your own progress, not an extraction result) */}
                    <button
                      type="button"
                      onClick={() => handleStatusToggle(ob.id, ob.status)}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors border shrink-0 self-start ${
                        ob.status === "COMPLETED"
                          ? "bg-slate-50 text-slate-600 border-slate-200 hover:bg-slate-100"
                          : "bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100"
                      }`}
                    >
                      <CheckCircle className="w-3.5 h-3.5" />
                      {ob.status === "COMPLETED" ? "Mark Pending" : "Mark Complete"}
                    </button>
                  </div>

                  <div className="text-xs bg-slate-50 p-3 rounded-lg border border-slate-100 space-y-1.5">
                    <p className="font-semibold text-slate-500">Due date</p>
                    {dueDate ? (
                      <>
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={`text-sm font-mono font-bold ${overdue ? "text-red-800" : "text-slate-900"}`}>
                            {dueDate}
                          </span>
                          {ob.due_computed ? (
                            <RuleTypeBadge ruleType="relative" />
                          ) : ob.due_rule_type === "recurring" ? (
                            <RuleTypeBadge ruleType="recurring" recurrence={ob.recurrence} />
                          ) : (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold border bg-slate-100 text-slate-700 border-slate-200">
                              Stated in contract
                            </span>
                          )}
                          {overdue && (
                            <span className="text-xs font-semibold text-red-800">
                              {overdueDays != null && overdueDays < 0
                                ? `Overdue by ${Math.abs(overdueDays)} ${Math.abs(overdueDays) === 1 ? "day" : "days"}`
                                : "Overdue"}
                            </span>
                          )}
                        </div>
                        {ob.due_computed && (
                          <p className="text-slate-600 leading-relaxed">
                            {ob.due_basis ?? "Calculated from the rule below. The basis was not recorded."}
                          </p>
                        )}
                        {ob.due_rule_type === "recurring" && recurrence && !ob.due_computed && (
                          <p className="text-slate-600">Repeats {recurrence.toLowerCase()}.</p>
                        )}
                        <p className="text-slate-700">
                          <span className="font-semibold text-slate-500">Contract says:</span>{" "}
                          {ob.due_rule ? <span className="italic">&ldquo;{ob.due_rule}&rdquo;</span> : "no due rule stated"}
                        </p>
                      </>
                    ) : (
                      <>
                        <p className="text-slate-800 font-medium">
                          <span className="font-semibold text-slate-500">Contract says:</span>{" "}
                          {ob.due_rule ? <span className="italic">&ldquo;{ob.due_rule}&rdquo;</span> : "No due rule stated"}
                        </p>
                        <p className="text-amber-900">
                          No date could be calculated.{ob.due_reason ? ` ${ob.due_reason}` : ""}
                        </p>
                      </>
                    )}
                  </div>

                  <SourceRef
                    contractId={id}
                    page={ob.source_page}
                    section={ob.source_section}
                    quote={ob.source_quote}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
