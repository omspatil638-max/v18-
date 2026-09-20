"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileText, Clock, Upload, Bell, Sparkles, Trash2, AlertTriangle, ClipboardCheck, Settings, GitCompare, Search } from "lucide-react";
import { ContractStatusBadge, ExtractionStatusChip } from "@/components/ui/Badges";
import { ProcessingBanner } from "@/components/ui/ProcessingBanner";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Toast } from "@/components/ui/Toast";
import { AlertSettingsDialog } from "@/components/AlertSettingsDialog";
import { AttentionNow, UpcomingDeadlines } from "@/components/DashboardDeadlines";
import type { DeadlineWindow } from "@/components/DashboardDeadlines";
import {
  errorMessage,
  fetchContracts,
  uploadContract,
  deleteContract,
  restoreContract,
  fetchFlagSummary,
  fetchGlobalDeadlines,
  fetchAlerts,
  fetchSystemStatus,
  acknowledgeAlert,
  acknowledgeAllAlerts,
  fetchAlertSettings,
} from "@/lib/api";
import { daysFromToday } from "@/lib/format";
import { isProcessing } from "@/lib/types";
import type { Alert, AlertSettings, ContractSummary, DeadlineWithContext, FlagSummary, SystemStatus } from "@/lib/types";

interface UploadMessage {
  tone: "info" | "error";
  text: string;
}

const UNDO_WINDOW_MS = 10_000;

type StatusFilter = "all" | "ready" | "processing" | "review" | "failed";
type SortKey = "newest" | "oldest" | "title" | "expiry";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "ready", label: "Ready" },
  { value: "processing", label: "Processing" },
  { value: "review", label: "Needs review" },
  { value: "failed", label: "Failed / unsupported" },
];

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "title", label: "Title A-Z" },
  { value: "expiry", label: "Expiring soonest" },
];

function matchesStatus(c: ContractSummary, filter: StatusFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "ready":
      return c.status === "READY";
    case "processing":
      return isProcessing(c.status);
    case "review":
      return (c.open_flag_count ?? 0) > 0;
    case "failed":
      return c.status === "FAILED" || c.status === "UNSUPPORTED";
  }
}

/** Matches the title, counterparty, party names and file name. */
function matchesQuery(c: ContractSummary, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const haystack = [c.title, c.counterparty ?? "", c.filename, ...c.parties.map((p) => p.name)];
  return haystack.some((text) => text.toLowerCase().includes(q));
}

/** Upcoming expiries first (soonest first), then already-expired (most recent first), then contracts with no expiry date. */
function expiryRank(c: ContractSummary): { group: number; days: number } {
  const days = daysFromToday(c.expiry_date);
  if (days === null) return { group: 2, days: 0 };
  return days >= 0 ? { group: 0, days } : { group: 1, days: -days };
}

function compareContracts(a: ContractSummary, b: ContractSummary, sort: SortKey): number {
  switch (sort) {
    case "newest":
      return b.created_at.localeCompare(a.created_at);
    case "oldest":
      return a.created_at.localeCompare(b.created_at);
    case "title":
      return a.title.localeCompare(b.title, undefined, { sensitivity: "base" });
    case "expiry": {
      const ra = expiryRank(a);
      const rb = expiryRank(b);
      return ra.group - rb.group || ra.days - rb.days || b.created_at.localeCompare(a.created_at);
    }
  }
}

function ContractRow({
  contract: c,
  onDelete,
  onProcessed,
}: {
  contract: ContractSummary;
  onDelete: (id: string, title: string) => void;
  onProcessed: () => void;
}) {
  const processing = isProcessing(c.status);
  const openFlags = c.open_flag_count ?? 0;
  const highFlags = c.high_flag_count ?? 0;
  return (
    <div className="p-4 bg-slate-50 rounded-2xl border border-slate-200/80 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 sm:gap-4">
      <div className="flex items-center gap-3 min-w-0">
        <div className="w-9 h-9 rounded-xl bg-blue-100 text-blue-700 flex items-center justify-center shrink-0">
          <FileText className="w-5 h-5" />
        </div>
        <div className="min-w-0">
          <Link href={`/contracts/${c.id}`} className="text-sm font-bold text-slate-900 hover:text-blue-600 truncate block" title={c.title}>
            {c.title}
          </Link>
          <p className="text-[11px] text-slate-500 font-mono break-all">
            {c.filename}
            {c.page_count > 0 && ` · ${c.page_count} page${c.page_count === 1 ? "" : "s"}`}
          </p>
          {(c.status === "UNSUPPORTED" || c.status === "FAILED") && (
            <p className="text-[11px] text-orange-700 mt-1">
              {c.extraction_error ?? (c.status === "FAILED" ? "Processing failed." : "This PDF is not supported.")}
            </p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center sm:justify-end gap-3 sm:shrink-0">
        {processing && (
          <ProcessingBanner
            compact
            contractId={c.id}
            status={c.status}
            stage={c.stage}
            progress={c.progress}
            onDone={onProcessed}
          />
        )}
        <ContractStatusBadge status={c.status} />
        {c.status === "READY" && <ExtractionStatusChip status={c.extraction_status} />}
        {c.version_count > 1 && (
          <Link
            href={`/contracts/${c.id}/compare`}
            title="Compare the versions of this contract"
            className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold border whitespace-nowrap bg-slate-100 text-slate-700 border-slate-200 hover:bg-slate-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            <GitCompare className="w-3 h-3 shrink-0" aria-hidden="true" />
            {c.version_count} versions
          </Link>
        )}
        {openFlags > 0 && (
          <Link
            href={`/contracts/${c.id}#review`}
            title={highFlags > 0 ? `${highFlags} high priority` : "Open review flags"}
            className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-semibold border whitespace-nowrap ${
              highFlags > 0
                ? "bg-red-100 text-red-800 border-red-200 hover:bg-red-200"
                : "bg-amber-100 text-amber-800 border-amber-200 hover:bg-amber-200"
            }`}
          >
            <ClipboardCheck className="w-3 h-3 shrink-0" aria-hidden="true" />
            {openFlags} to review
          </Link>
        )}
        <Link
          href={`/contracts/${c.id}`}
          className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs rounded-xl transition-colors shadow-xs"
        >
          View Details
        </Link>
        <button
          type="button"
          onClick={() => onDelete(c.id, c.title)}
          className="p-1.5 rounded-lg text-slate-400 hover:text-red-600 hover:bg-red-50 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          title="Delete contract"
          aria-label={`Delete contract ${c.title}`}
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

/**
 * An empty alert list does not mean every contract was successfully analysed.
 * Keep incomplete/unsupported analysis visible alongside the action queue so a
 * user never mistakes missing extraction for a healthy contract.
 */
function AnalysisCoverage({ contracts }: { contracts: ContractSummary[] }) {
  if (contracts.length === 0) return null;

  const working = contracts.filter((c) => isProcessing(c.status));
  const needsAttention = contracts.filter(
    (c) =>
      c.status === "FAILED" ||
      c.status === "UNSUPPORTED" ||
      c.extraction_status === "partial" ||
      c.extraction_status === "unavailable" ||
      c.extraction_status === "legacy",
  );

  if (working.length === 0 && needsAttention.length === 0) {
    return (
      <section aria-label="Analysis coverage" className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3">
        <p className="text-sm font-semibold text-emerald-900">Analysis coverage is up to date.</p>
        <p className="mt-0.5 text-xs text-emerald-800">
          Every uploaded contract has completed its current analysis. Individual findings can still be marked “needs review.”
        </p>
      </section>
    );
  }

  return (
    <section aria-labelledby="coverage-heading" className="rounded-2xl border border-amber-300 bg-amber-50 px-4 py-4 space-y-3">
      <div>
        <h2 id="coverage-heading" className="text-sm font-bold text-amber-950">Analysis needs attention</h2>
        <p className="mt-0.5 text-xs text-amber-900">
          An empty review queue cannot confirm these contracts are clear: some are still processing or do not have complete verified extraction.
        </p>
      </div>
      <ul className="flex flex-wrap gap-2">
        {working.map((contract) => (
          <li key={contract.id}>
            <Link
              href={`/contracts/${contract.id}`}
              className="inline-flex rounded-lg border border-blue-200 bg-white px-2.5 py-1.5 text-xs font-semibold text-blue-800 hover:bg-blue-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              {contract.title} · processing
            </Link>
          </li>
        ))}
        {needsAttention.map((contract) => (
          <li key={contract.id}>
            <Link
              href={`/contracts/${contract.id}`}
              className="inline-flex rounded-lg border border-amber-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-amber-950 hover:bg-amber-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              {contract.title} · {contract.status === "FAILED" || contract.status === "UNSUPPORTED" ? contract.status.toLowerCase() : contract.extraction_status}
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** One honest line of what is switched on, built only from the saved reminder settings. */
function reminderStatusLine(s: AlertSettings): string {
  const email = !s.smtp_configured
    ? "not set up on server"
    : s.email_enabled && s.email_to
      ? `on → ${s.email_to}`
      : "off";
  let push: string;
  if (!s.push_available) push = "not set up on server";
  else if (s.push_enabled) push = `on (${s.push_subscriptions} ${s.push_subscriptions === 1 ? "browser" : "browsers"})`;
  else push = "off";
  const repeats = s.repeat_enabled ? `every ${s.repeat_hours} h` : "off";
  return `Email: ${email} · Push: ${push} · Repeats: ${repeats}`;
}

export default function DashboardPage() {
  const [contracts, setContracts] = useState<ContractSummary[]>([]);
  const [deadlines, setDeadlines] = useState<DeadlineWithContext[]>([]);
  const [deadlineWindow, setDeadlineWindow] = useState<DeadlineWindow>(90);
  const [deadlinesLoading, setDeadlinesLoading] = useState(true);
  const [deadlinesError, setDeadlinesError] = useState<string | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [alertsLoading, setAlertsLoading] = useState(true);
  const [alertsError, setAlertsError] = useState<string | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);
  const [ackAnnounce, setAckAnnounce] = useState("");
  const [ackAllBusy, setAckAllBusy] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [alertSettings, setAlertSettings] = useState<AlertSettings | null>(null);
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [flagSummary, setFlagSummary] = useState<FlagSummary | null>(null);
  const [loading, setLoading] = useState(true);

  const [uploadDragOver, setUploadDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<UploadMessage | null>(null);

  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [sort, setSort] = useState<SortKey>("newest");

  const [deleteTarget, setDeleteTarget] = useState<{ id: string; title: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [undo, setUndo] = useState<{ id: string; title: string } | null>(null);
  const listHeadingRef = useRef<HTMLHeadingElement | null>(null);

  const refreshContracts = useCallback(async () => {
    try {
      setContracts(await fetchContracts());
    } catch {
      // Keep the list we already have; the next reload will retry.
    }
  }, []);

  useEffect(() => {
    async function loadDashboardData() {
      const [contractsData, systemData, flagData] = await Promise.all([
        fetchContracts().catch(() => []),
        fetchSystemStatus().catch(() => null),
        fetchFlagSummary().catch(() => null),
      ]);
      setContracts(contractsData);
      setFlagSummary(flagData);
      setSystem(systemData);
      setLoading(false);
    }
    loadDashboardData();
  }, []);

  // Alerts whose lead time has been reached.
  useEffect(() => {
    let cancelled = false;
    fetchAlerts({ dueOnly: true })
      .then((data) => {
        if (cancelled) return;
        setAlerts(data.filter((a) => !a.acknowledged));
        setAlertsError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setAlertsError(errorMessage(e, "Could not load alerts."));
      })
      .finally(() => {
        if (!cancelled) setAlertsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Reminder settings, only for the status line beside the settings button. Failing quietly is fine.
  const loadAlertSettings = useCallback(() => {
    fetchAlertSettings()
      .then(setAlertSettings)
      .catch(() => setAlertSettings(null));
  }, []);

  useEffect(() => {
    loadAlertSettings();
  }, [loadAlertSettings]);

  // Upcoming deadlines for the selected window; overdue items are always included.
  useEffect(() => {
    let cancelled = false;
    setDeadlinesLoading(true);
    fetchGlobalDeadlines(deadlineWindow, true)
      .then((data) => {
        if (cancelled) return;
        setDeadlines(data);
        setDeadlinesError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setDeadlinesError(errorMessage(e, "Could not load deadlines."));
      })
      .finally(() => {
        if (!cancelled) setDeadlinesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [deadlineWindow]);

  async function handleAcknowledgeAlert(id: string) {
    const index = alerts.findIndex((a) => a.id === id);
    if (index === -1) return;
    const removed = alerts[index];
    setAckError(null);
    // Optimistic: take it off the list immediately, put it back if the server refuses.
    setAlerts((prev) => prev.filter((a) => a.id !== id));
    setAckAnnounce(`Alert for ${removed.contract_title} acknowledged.`);
    try {
      await acknowledgeAlert(id);
    } catch (e: unknown) {
      setAlerts((prev) => {
        const next = [...prev];
        next.splice(Math.min(index, next.length), 0, removed);
        return next;
      });
      setAckAnnounce("");
      setAckError(errorMessage(e, "Could not acknowledge the alert. It has been put back."));
    }
  }

  async function handleAcknowledgeAll() {
    setAckError(null);
    setAckAllBusy(true);
    try {
      const res = await acknowledgeAllAlerts();
      const count = res?.acknowledged ?? 0;
      setAckAnnounce(`${count} ${count === 1 ? "alert" : "alerts"} marked as read.`);
      const fresh = await fetchAlerts({ dueOnly: true });
      setAlerts(fresh.filter((a) => !a.acknowledged));
    } catch (e: unknown) {
      setAckAnnounce("");
      setAckError(errorMessage(e, "Could not mark the alerts as read."));
    } finally {
      setAckAllBusy(false);
    }
  }

  async function handleFileUpload(file: File) {
    if (!file.name.toLowerCase().endsWith(".pdf")) {
      setUploadMsg({ tone: "error", text: "Only PDF files (.pdf) are supported." });
      return;
    }
    if (file.size === 0) {
      setUploadMsg({ tone: "error", text: "That file is empty." });
      return;
    }
    if (system && file.size > system.max_upload_mb * 1024 * 1024) {
      const mb = (file.size / (1024 * 1024)).toFixed(1);
      setUploadMsg({
        tone: "error",
        text: `That file is ${mb} MB, which is over the ${system.max_upload_mb} MB upload limit.`,
      });
      return;
    }

    setUploading(true);
    setUploadMsg({ tone: "info", text: "Uploading..." });
    try {
      const created = await uploadContract(file);
      setContracts((prev) => [created, ...prev.filter((c) => c.id !== created.id)]);
      setUploadMsg({
        tone: "info",
        text: `"${created.title}" was uploaded. Processing has started; progress is shown in the list below.`,
      });
    } catch (e: unknown) {
      setUploadMsg({ tone: "error", text: errorMessage(e, "Upload failed.") });
    } finally {
      setUploading(false);
    }
  }

  function requestDelete(id: string, title: string) {
    setDeleteError(null);
    setDeleteTarget({ id, title });
  }

  function cancelDelete() {
    if (deleting) return;
    setDeleteTarget(null);
    setDeleteError(null);
  }

  async function confirmDelete(permanent: boolean) {
    if (!deleteTarget || deleting) return;
    const { id, title } = deleteTarget;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteContract(id, permanent);
      setContracts((prev) => prev.filter((c) => c.id !== id));
      setDeleteTarget(null);
      if (permanent) {
        setUndo(null);
        setUploadMsg({ tone: "info", text: `Permanently deleted "${title}".` });
      } else {
        setUploadMsg(null);
        setUndo({ id, title });
      }
      // The trigger button is gone with its row, so park keyboard focus on the list heading.
      window.requestAnimationFrame(() => listHeadingRef.current?.focus());
    } catch (err: unknown) {
      setDeleteError(errorMessage(err, "Failed to delete contract. Please try again."));
    } finally {
      setDeleting(false);
    }
  }

  async function handleUndo() {
    if (!undo) return;
    const { id, title } = undo;
    setUndo(null);
    try {
      const restored = await restoreContract(id);
      setContracts((prev) => (prev.some((c) => c.id === id) ? prev : [restored, ...prev]));
      setUploadMsg({ tone: "info", text: `Restored "${title}".` });
      // Pick up the review-flag counts, which the restore response may not carry.
      refreshContracts();
    } catch (err: unknown) {
      setUploadMsg({ tone: "error", text: errorMessage(err, `Could not restore "${title}".`) });
    }
  }

  const filtersActive = query.trim() !== "" || statusFilter !== "all";
  const visibleContracts = useMemo(
    () =>
      contracts
        .filter((c) => matchesStatus(c, statusFilter) && matchesQuery(c, query))
        .sort((a, b) => compareContracts(a, b, sort)),
    [contracts, statusFilter, query, sort],
  );

  function clearFilters() {
    setQuery("");
    setStatusFilter("all");
  }

  const statusCounts = useMemo(() => {
    const counts: Record<StatusFilter, number> = { all: 0, ready: 0, processing: 0, review: 0, failed: 0 };
    for (const c of contracts) {
      for (const f of STATUS_FILTERS) if (matchesStatus(c, f.value)) counts[f.value] += 1;
    }
    return counts;
  }, [contracts]);

  const controlClass =
    "w-full px-3 py-2 text-sm rounded-xl border border-slate-300 bg-white text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";

  return (
    <div className="space-y-8 p-4 sm:p-8 max-w-6xl mx-auto">
      {/* ─ Top Hero Banner ─ */}
      <div className="bg-gradient-to-r from-slate-900 via-indigo-950 to-blue-900 rounded-3xl p-5 sm:p-8 text-white shadow-2xl flex flex-col md:flex-row items-center justify-between gap-6">
        <div className="space-y-2">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-blue-500/20 text-blue-300 text-xs font-semibold border border-blue-400/30">
            <Sparkles className="w-3.5 h-3.5 text-blue-400" /> Grounded Contract Intelligence
          </div>
          <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-white">
            Know what needs attention.
          </h1>
          <p className="text-xs text-slate-300 leading-relaxed max-w-lg">
            Upload a contract, verify what matters in the source, and act on upcoming commitments, changes, and review items.
          </p>
        </div>

        {/* Primary Action Button */}
        <label className="flex items-center gap-2 px-6 py-3.5 bg-gradient-to-r from-blue-500 to-indigo-600 hover:from-blue-600 hover:to-indigo-700 text-white font-bold text-sm rounded-2xl shadow-lg cursor-pointer transition-all transform hover:-translate-y-0.5 shrink-0">
          <Upload className="w-4 h-4" />
          {uploading ? "Uploading..." : "Upload Contract PDF"}
          <input
            type="file"
            accept=".pdf,application/pdf"
            disabled={uploading}
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) handleFileUpload(file);
            }}
          />
        </label>
      </div>

      <AnalysisCoverage contracts={contracts} />

      {/* ─ Stat Overview Row ─ */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 md:gap-6">
        <div className="p-4 sm:p-6 bg-white rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
          <div>
            <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Active Contracts</p>
            <p className="text-2xl font-bold text-slate-900 mt-1">{contracts.length}</p>
          </div>
          <div className="w-10 h-10 rounded-xl bg-blue-50 text-blue-600 flex items-center justify-center">
            <FileText className="w-5 h-5" />
          </div>
        </div>

        <div className="p-4 sm:p-6 bg-white rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
          <div>
            <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">
              Deadlines · {deadlineWindow} days
            </p>
            <p className="text-2xl font-bold text-slate-900 mt-1">
              {deadlinesLoading || deadlinesError ? "–" : deadlines.length}
              {!deadlinesLoading && deadlines.some((d) => d.is_overdue) && (
                <span className="ml-2 text-xs font-semibold text-red-700">
                  {deadlines.filter((d) => d.is_overdue).length} overdue
                </span>
              )}
            </p>
          </div>
          <div className="w-10 h-10 rounded-xl bg-amber-50 text-amber-600 flex items-center justify-center">
            <Clock className="w-5 h-5" />
          </div>
        </div>

        <div className="p-4 sm:p-6 bg-white rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between">
          <div>
            <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Alerts due</p>
            <p className="text-2xl font-bold text-slate-900 mt-1">{alertsLoading || alertsError ? "–" : alerts.length}</p>
          </div>
          <div className="w-10 h-10 rounded-xl bg-purple-50 text-purple-600 flex items-center justify-center">
            <Bell className="w-5 h-5" />
          </div>
        </div>

        <Link
          href="/review"
          className="p-4 sm:p-6 bg-white rounded-2xl border border-slate-200 shadow-sm flex items-center justify-between hover:border-blue-300 hover:shadow-md transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        >
          <div>
            <p className="text-xs font-bold text-slate-400 uppercase tracking-wider">Needs review</p>
            <p className="text-2xl font-bold text-slate-900 mt-1">
              {flagSummary ? flagSummary.open : "–"}
              {flagSummary && flagSummary.high > 0 && (
                <span className="ml-2 text-xs font-semibold text-red-700">{flagSummary.high} high</span>
              )}
            </p>
          </div>
          <div className="w-10 h-10 rounded-xl bg-red-50 text-red-600 flex items-center justify-center shrink-0">
            <ClipboardCheck className="w-5 h-5" aria-hidden="true" />
          </div>
        </Link>
      </div>

      {/* ─ Attention now + upcoming deadlines ─ */}
      <div className="flex flex-wrap items-center justify-end gap-x-4 gap-y-2 -mb-4">
        {alertSettings && (
          <p className="text-xs text-slate-500 min-w-0 break-words">{reminderStatusLine(alertSettings)}</p>
        )}
        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          aria-haspopup="dialog"
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
        >
          <Settings className="w-3.5 h-3.5" aria-hidden="true" />
          Reminder settings
        </button>
      </div>

      <AttentionNow
        alerts={alerts}
        loading={alertsLoading}
        error={alertsError}
        nothingDated={!deadlinesLoading && !deadlinesError && deadlines.length === 0 && contracts.length > 0}
        ackError={ackError}
        announce={ackAnnounce}
        onAcknowledge={handleAcknowledgeAlert}
        onAcknowledgeAll={handleAcknowledgeAll}
        ackAllBusy={ackAllBusy}
      />

      <UpcomingDeadlines
        deadlines={deadlines}
        loading={deadlinesLoading}
        error={deadlinesError}
        window={deadlineWindow}
        onWindowChange={setDeadlineWindow}
      />

      {settingsOpen && (
        <AlertSettingsDialog
          onClose={() => {
            setSettingsOpen(false);
            loadAlertSettings();
          }}
        />
      )}

      {/* ─ AI not configured banner ─ */}
      {system && !system.llm_configured && (
        <div
          role="alert"
          className="p-4 rounded-2xl bg-amber-50 border border-amber-200 text-sm text-amber-900 flex items-start gap-3"
        >
          <AlertTriangle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" aria-hidden="true" />
          <div className="space-y-1">
            <p className="font-semibold">
              AI extraction is not configured. Documents are stored and searchable, but fields, obligations and
              deadlines will show &lsquo;Extraction unavailable&rsquo;.
            </p>
            {system.llm_reason && <p className="text-xs text-amber-800">{system.llm_reason}</p>}
          </div>
        </div>
      )}

      {/* ─ Upload Notification Alert ─ */}
      {uploadMsg && (
        <div
          role={uploadMsg.tone === "error" ? "alert" : "status"}
          className={`p-4 rounded-2xl border text-sm font-semibold flex items-center justify-between gap-3 shadow-sm ${
            uploadMsg.tone === "error"
              ? "bg-red-50 border-red-200 text-red-900"
              : "bg-blue-50 border-blue-200 text-blue-900"
          }`}
        >
          <span className="min-w-0 break-words">{uploadMsg.text}</span>
          <button
            type="button"
            onClick={() => setUploadMsg(null)}
            aria-label="Dismiss message"
            className="shrink-0 text-slate-500 hover:text-slate-800 font-bold ml-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            ✕
          </button>
        </div>
      )}

      {/* ─ Drag & Drop Upload Zone ─ */}
      <section>
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setUploadDragOver(true);
          }}
          onDragLeave={() => setUploadDragOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setUploadDragOver(false);
            const file = e.dataTransfer.files?.[0];
            if (file && !uploading) handleFileUpload(file);
          }}
          className={`border-2 border-dashed rounded-3xl p-5 sm:p-8 text-center transition-all ${
            uploadDragOver
              ? "border-blue-500 bg-blue-50/50 shadow-md"
              : "border-slate-300 bg-white hover:border-blue-400 hover:shadow-sm"
          }`}
        >
          <Upload className="w-10 h-10 text-blue-600 mx-auto mb-3 opacity-80" />
          <p className="text-sm font-bold text-slate-800">
            {uploading ? "Uploading contract..." : "Drag & drop your PDF contract here"}
          </p>
          <p className="text-xs text-slate-500 mt-1">
            or{" "}
            <label className="text-blue-600 font-bold cursor-pointer hover:underline">
              browse files
              <input
                type="file"
                accept=".pdf,application/pdf"
                disabled={uploading}
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) handleFileUpload(file);
                }}
              />
            </label>{" "}
            from your device
          </p>
          {system && (
            <p className="text-[11px] text-slate-400 mt-1">PDF only, up to {system.max_upload_mb} MB.</p>
          )}
        </div>
      </section>

      {/* ─ Contracts Table ─ */}
      <section
        aria-labelledby="contracts-heading"
        className="bg-white rounded-3xl border border-slate-200 shadow-sm p-4 sm:p-6 space-y-4"
      >
        <div className="flex items-center justify-between pb-3 border-b border-slate-100">
          <h2
            id="contracts-heading"
            ref={listHeadingRef}
            tabIndex={-1}
            className="text-xs font-bold text-slate-500 uppercase tracking-wider focus:outline-none"
          >
            Uploaded Contracts ({contracts.length})
          </h2>
        </div>

        {!loading && contracts.length > 0 && (
          <div className="space-y-2">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-[minmax(0,1fr)_13rem_13rem] gap-3">
              <div className="relative sm:col-span-2 lg:col-span-1">
                <label htmlFor="contract-search" className="sr-only">
                  Search contracts by title, party or file name
                </label>
                <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" aria-hidden="true" />
                <input
                  id="contract-search"
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search title, party or file name"
                  autoComplete="off"
                  className={`${controlClass} pl-9`}
                />
              </div>
              <div>
                <label htmlFor="contract-status-filter" className="sr-only">
                  Filter by status
                </label>
                <select
                  id="contract-status-filter"
                  value={statusFilter}
                  onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
                  className={controlClass}
                >
                  {STATUS_FILTERS.map((f) => (
                    <option key={f.value} value={f.value}>
                      {f.value === "all" ? "All statuses" : `${f.label} (${statusCounts[f.value]})`}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label htmlFor="contract-sort" className="sr-only">
                  Sort contracts
                </label>
                <select
                  id="contract-sort"
                  value={sort}
                  onChange={(e) => setSort(e.target.value as SortKey)}
                  className={controlClass}
                >
                  {SORT_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <p role="status" aria-live="polite" className="text-xs font-medium text-slate-500">
                {visibleContracts.length} of {contracts.length} {contracts.length === 1 ? "contract" : "contracts"}
              </p>
              {filtersActive && visibleContracts.length > 0 && (
                <button
                  type="button"
                  onClick={clearFilters}
                  className="text-xs font-semibold text-blue-700 hover:text-blue-900 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                >
                  Clear filters
                </button>
              )}
            </div>
          </div>
        )}

        {loading ? (
          <div className="p-8 text-center text-xs text-slate-400">Loading contracts...</div>
        ) : contracts.length === 0 ? (
          <div className="p-8 text-center text-xs text-slate-400">
            No contracts uploaded yet. Upload a PDF above to get started!
          </div>
        ) : visibleContracts.length === 0 ? (
          <div className="p-8 text-center space-y-3">
            <p className="text-sm text-slate-600">No contracts match your search or filters.</p>
            <button
              type="button"
              onClick={clearFilters}
              className="px-3 py-1.5 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              Clear filters
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            {visibleContracts.map((c) => (
              <ContractRow key={c.id} contract={c} onDelete={requestDelete} onProcessed={refreshContracts} />
            ))}
          </div>
        )}
      </section>

      {deleteTarget && (
        <ConfirmDialog
          title={`Delete "${deleteTarget.title}"?`}
          confirmLabel="Delete"
          busyLabel="Deleting..."
          busy={deleting}
          error={deleteError}
          onConfirm={() => confirmDelete(false)}
          onCancel={cancelDelete}
          secondary={{
            label: "Delete permanently (cannot be undone)",
            busyLabel: "Deleting permanently...",
            hint: "Or erase the contract and its files for good. This cannot be recovered.",
            onClick: () => confirmDelete(true),
          }}
        >
          <p>The contract is removed from your list. You will have a few seconds to undo it.</p>
        </ConfirmDialog>
      )}

      {undo && (
        <Toast
          message={`Deleted "${undo.title}".`}
          actionLabel="Undo"
          onAction={handleUndo}
          onDismiss={() => setUndo(null)}
          durationMs={UNDO_WINDOW_MS}
        />
      )}
    </div>
  );
}
