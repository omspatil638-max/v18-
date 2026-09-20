"use client";

import { Suspense, use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Users,
  Activity,
  MessageSquare,
  Trash2,
  Calendar,
  AlertTriangle,
  RefreshCw,
  ClipboardCheck,
  CheckCircle2,
  Download,
  BellRing,
  GitCompare,
  Eye,
  Upload,
  FileText,
  Pencil,
  UserCheck,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ContractTabs, ContractStatusBadge, ClauseTypeBadge } from "@/components/ui/Badges";
import { StatusChip } from "@/components/ui/StatusChip";
import { ConfidenceBadge } from "@/components/ui/ConfidenceBadge";
import { SourceRef } from "@/components/ui/SourceRef";
import { ProcessingBanner } from "@/components/ui/ProcessingBanner";
import { ReviewFlagCard } from "@/components/ReviewFlagCard";
import { UploadVersionDialog } from "@/components/UploadVersionDialog";
import { ExtractionSourceNotice, OcrFieldChip } from "@/components/ExtractionSourceNotice";
import { FieldReviewDialog, ReviewedByYouChip } from "@/components/FieldReviewDialog";
import { ContractDetailsDialog } from "@/components/ContractDetailsDialog";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { SEVERITY_ORDER } from "@/components/ui/FlagBadge";
import { calendarUrl, deleteContract, errorMessage, fetchContractById, fetchContractFlags, reprocessContract } from "@/lib/api";
import { emptyStateText } from "@/lib/extraction";
import { daysFromToday, dueInText, formatDate, locationLabel, sourceHref, versionName, versionOptionLabel } from "@/lib/format";
import { isProcessing } from "@/lib/types";
import type { ContractDetail, Deadline, ExtractedField, FieldKey, ReviewFlag } from "@/lib/types";

const FIELD_LABELS: { key: FieldKey; label: string; isDate?: boolean }[] = [
  { key: "effective_date", label: "Effective Date", isDate: true },
  { key: "expiration_date", label: "Expiry Date", isDate: true },
  { key: "renewal_terms", label: "Renewal Terms" },
  { key: "auto_renew", label: "Auto-Renewal" },
  { key: "renewal_notice_period", label: "Renewal Notice Period" },
  { key: "payment_terms", label: "Payment Terms" },
  { key: "termination_conditions", label: "Termination Conditions" },
  { key: "termination_notice_period", label: "Termination Notice Period" },
];

/** The text to show for one extracted field. Never invents a value. */
function fieldText(field: ExtractedField, isDate: boolean): string {
  if (field.status === "not_found") return "Not found in this document";
  if (field.status === "extraction_unavailable") return "Extraction unavailable";
  if (isDate) {
    const iso = field.value && typeof field.value.date === "string" ? field.value.date : null;
    const formatted = formatDate(iso, "long");
    if (formatted) return formatted;
  }
  return field.display_value ?? "Value missing";
}

function FieldRow({
  contractId,
  versionId,
  field,
  label,
  isDate,
  onReviewed,
}: {
  contractId: string;
  versionId?: string | null;
  field: ExtractedField;
  label: string;
  isDate: boolean;
  onReviewed: () => void;
}) {
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewNote, setReviewNote] = useState("");
  const userReview = field.notes?.user_review ?? null;
  const hasValue = field.status === "verified" || field.status === "needs_review";
  const valueClass =
    field.status === "needs_review"
      ? "bg-amber-50 border-amber-200 text-amber-950"
      : hasValue
        ? "bg-slate-50 border-slate-100 text-slate-900"
        : "bg-slate-50 border-slate-100 text-slate-500 italic font-normal";
  const conflicts = field.notes?.conflicts ?? [];

  return (
    <div className="py-3 border-b border-slate-100 last:border-0 grid grid-cols-1 sm:grid-cols-5 gap-2 sm:gap-4">
      <dt className="sm:col-span-2 text-xs font-bold uppercase tracking-wider text-slate-500 pt-2">{label}</dt>
      <dd className="sm:col-span-3 space-y-2 min-w-0 break-words">
        <div className={`text-sm font-semibold p-2 rounded-lg border ${valueClass}`}>{fieldText(field, isDate)}</div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusChip status={field.status} />
          {hasValue && <ConfidenceBadge confidence={field.confidence} />}
          {userReview && <ReviewedByYouChip action={userReview.action} />}
          {field.notes?.ocr && hasValue && <OcrFieldChip />}
          <button
            type="button"
            onClick={() => setReviewOpen(true)}
            aria-haspopup="dialog"
            aria-label={`Review ${label}`}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border bg-white border-slate-300 text-slate-800 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            <UserCheck className="w-3 h-3 shrink-0" aria-hidden="true" />
            Review
          </button>
        </div>
        {hasValue && (
          <SourceRef
            contractId={contractId}
            versionId={versionId}
            page={field.page}
            section={field.section}
            quote={field.source_quote}
          />
        )}
        <div role="status" className="sr-only">
          {reviewNote}
        </div>
        {reviewOpen && (
          <FieldReviewDialog
            contractId={contractId}
            versionId={versionId}
            field={field}
            label={label}
            onClose={() => setReviewOpen(false)}
            onSaved={(message) => {
              setReviewOpen(false);
              setReviewNote(message);
              onReviewed();
            }}
          />
        )}
        {field.notes?.validation && hasValue && (
          <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1">
            {field.notes.validation}
          </p>
        )}
        {field.notes?.approximate && hasValue && (
          <p className="text-[11px] text-slate-500">{field.notes.approximate}</p>
        )}
        {conflicts.length > 0 && (
          <div className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5 space-y-1">
            <p className="font-semibold">Conflicting values found elsewhere in the document:</p>
            <ul className="list-disc pl-4 space-y-0.5">
              {conflicts.map((c, i) => (
                <li key={i}>
                  {c.value ?? "Value not stated"}
                  {locationLabel(c.page, c.section) && ` (${locationLabel(c.page, c.section)})`}
                </li>
              ))}
            </ul>
          </div>
        )}
      </dd>
    </div>
  );
}

/** Summary-banner value: honest about missing / unavailable, never a default. */
function summaryValue(contract: ContractDetail, key: FieldKey, legacyFlat: string | null): string {
  const field = contract.fields.find((f) => f.field_key === key);
  if (field) {
    if (field.status === "not_found") return "Not found";
    if (field.status === "extraction_unavailable") return "Unavailable";
    return fieldText(field, true);
  }
  if (isProcessing(contract.status)) return "Pending";
  if (contract.extraction_status === "legacy" && legacyFlat) return `${legacyFlat} (unverified)`;
  return "Not extracted";
}

function Banner({ tone, title, children }: { tone: "amber" | "red" | "slate"; title: string; children?: React.ReactNode }) {
  const styles = {
    amber: "bg-amber-50 border-amber-200 text-amber-900",
    red: "bg-red-50 border-red-200 text-red-900",
    slate: "bg-slate-50 border-slate-200 text-slate-800",
  }[tone];
  return (
    <div role="alert" className={`p-4 rounded-2xl border text-sm flex items-start gap-3 ${styles}`}>
      <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
      <div className="space-y-2 min-w-0">
        <p className="font-semibold">{title}</p>
        {children}
      </div>
    </div>
  );
}

/** The renewal notice deadline that matters most: the next upcoming one, else the most recent past one. */
function pickRenewalDeadline(deadlines: Deadline[]): Deadline | null {
  const notices = deadlines
    .filter((d) => d.deadline_type === "RENEWAL_NOTICE")
    .sort((a, b) => a.deadline_date.localeCompare(b.deadline_date));
  if (notices.length === 0) return null;
  return notices.find((d) => (daysFromToday(d.deadline_date) ?? 0) >= 0) ?? notices[notices.length - 1];
}

function RenewalNoticeCallout({
  contractId,
  versionId,
  deadline,
}: {
  contractId: string;
  versionId?: string | null;
  deadline: Deadline;
}) {
  const days = daysFromToday(deadline.deadline_date);
  const overdue = days != null && days < 0;
  const location = locationLabel(deadline.source_page, deadline.source_section);
  return (
    <section
      aria-labelledby="renewal-notice-heading"
      className={`rounded-2xl border-2 p-5 space-y-2 ${
        overdue ? "bg-red-50 border-red-400" : "bg-indigo-50 border-indigo-300"
      }`}
    >
      <h2
        id="renewal-notice-heading"
        className={`text-xs font-bold uppercase tracking-wider flex items-center gap-2 ${
          overdue ? "text-red-900" : "text-indigo-900"
        }`}
      >
        <BellRing className="w-4 h-4" aria-hidden="true" />
        Renewal notice deadline
      </h2>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <p className="text-lg font-bold font-mono text-slate-900">
          {formatDate(deadline.deadline_date, "long") ?? deadline.deadline_date}
        </p>
        {days != null && (
          <p className={`text-sm font-semibold ${overdue ? "text-red-800" : "text-indigo-900"}`}>
            {overdue ? `Passed ${Math.abs(days)} ${Math.abs(days) === 1 ? "day" : "days"} ago` : dueInText(days)}
          </p>
        )}
      </div>
      {deadline.basis && <p className="text-xs text-slate-700 leading-relaxed">{deadline.basis}</p>}
      <p className="text-xs text-slate-600">
        The last day to give notice, worked out by AI from the contract&apos;s dates. Not legal advice; confirm it
        against the source.{" "}
        {deadline.source_page != null && location ? (
          <Link
            href={sourceHref(contractId, deadline.source_page, deadline.source_quote, versionId)}
            className="font-semibold text-blue-700 hover:text-blue-900"
          >
            Source: {location}
          </Link>
        ) : null}
      </p>
    </section>
  );
}

const FLAG_STATUS_MESSAGE = {
  OPEN: "Flag reopened.",
  RESOLVED: "Flag marked resolved.",
  DISMISSED: "Flag dismissed.",
} as const;

/** "Needs human review": this contract's review flags for its current version. */
function ReviewSection({ contract, versionId: viewedVersionId }: { contract: ContractDetail; versionId?: string | null }) {
  const [flags, setFlags] = useState<ReviewFlag[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const sectionRef = useRef<HTMLElement | null>(null);
  const scrolled = useRef(false);

  const contractId = contract.id;
  const versionId = viewedVersionId ?? contract.current_version_id ?? undefined;
  const processing = isProcessing(contract.status);

  useEffect(() => {
    let cancelled = false;
    fetchContractFlags(contractId, versionId)
      .then((data) => {
        if (cancelled) return;
        setFlags(data);
        setError(null);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorMessage(e, "Could not load review flags."));
      });
    return () => {
      cancelled = true;
    };
    // Re-fetch when processing finishes: new flags are generated at the end of extraction.
  }, [contractId, versionId, contract.status, contract.extraction_status]);

  // Scroll to the section when arriving via /contracts/{id}#review (once the section has content).
  useEffect(() => {
    if (scrolled.current || (flags === null && !error)) return;
    if (window.location.hash === "#review") {
      scrolled.current = true;
      sectionRef.current?.scrollIntoView({ block: "start" });
    }
  }, [flags, error]);

  function handleUpdated(updated: ReviewFlag) {
    setFlags((prev) => (prev ? prev.map((f) => (f.id === updated.id ? { ...f, ...updated } : f)) : prev));
    setAnnounce(FLAG_STATUS_MESSAGE[updated.status]);
  }

  const bySeverity = (a: ReviewFlag, b: ReviewFlag) =>
    SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity) ||
    b.created_at.localeCompare(a.created_at);
  const openFlags = (flags ?? []).filter((f) => f.status === "OPEN").sort(bySeverity);
  const closedFlags = (flags ?? []).filter((f) => f.status !== "OPEN").sort(bySeverity);

  const ex = contract.extraction_status;
  const analysisComplete = contract.status === "READY" && ex === "complete";

  return (
    <section
      ref={sectionRef}
      id="review"
      aria-labelledby="review-heading"
      className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-4 scroll-mt-4"
    >
      <h2
        id="review-heading"
        className="text-xs font-bold text-slate-500 uppercase tracking-wider pb-2 border-b border-slate-100 flex items-center gap-2"
      >
        <ClipboardCheck className="w-4 h-4 text-blue-600" aria-hidden="true" />
        Needs human review{flags ? ` (${openFlags.length})` : ""}
      </h2>

      <p className="text-xs text-slate-500">
        These are prompts for a human to check, not legal conclusions.
      </p>

      {error ? (
        <p role="alert" className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          {error}
        </p>
      ) : flags === null ? (
        <p role="status" className="text-xs text-slate-500 text-center py-4">Loading review flags...</p>
      ) : (
        <>
          {openFlags.length > 0 ? (
            <div className="space-y-3">
              {openFlags.map((f) => (
                <ReviewFlagCard key={f.id} flag={f} onUpdated={handleUpdated} />
              ))}
            </div>
          ) : processing ? (
            <p className="text-xs text-slate-500">Review flags will appear once processing has finished.</p>
          ) : analysisComplete ? (
            <div className="flex items-start gap-2 text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2">
              <CheckCircle2 className="w-4 h-4 mt-0.5 shrink-0 text-green-600" aria-hidden="true" />
              <p>
                No open review flags.{" "}
                <span className="text-xs text-slate-500">
                  Flags are automatic checks and do not guarantee that nothing is wrong.
                </span>
              </p>
            </div>
          ) : (
            <p className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">
              No open review flags were returned, but this contract was not fully analysed, so review flags may be
              incomplete.
            </p>
          )}

          {closedFlags.length > 0 && (
            <details className="group rounded-xl border border-slate-200 bg-slate-50/60">
              <summary className="cursor-pointer select-none px-3 py-2 text-xs font-bold text-slate-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600">
                Resolved / dismissed ({closedFlags.length})
              </summary>
              <div className="p-3 space-y-3">
                {closedFlags.map((f) => (
                  <ReviewFlagCard key={f.id} flag={f} onUpdated={handleUpdated} />
                ))}
              </div>
            </details>
          )}
        </>
      )}

      <div aria-live="polite" className="sr-only">
        {announce}
      </div>
    </section>
  );
}

/** Header <select> to switch between versions. The current version has no ?version= in the URL. */
function VersionPicker({
  contract,
  viewedVersionId,
  onChange,
}: {
  contract: ContractDetail;
  viewedVersionId: string | null;
  onChange: (versionId: string) => void;
}) {
  const versions = [...(contract.versions ?? [])].sort((a, b) => b.version_number - a.version_number);
  if (versions.length === 0) return null;
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="version-picker" className="text-xs font-bold uppercase tracking-wider text-slate-500">
        Version
      </label>
      <select
        id="version-picker"
        value={viewedVersionId ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="max-w-[16rem] px-2 py-1.5 text-xs font-semibold rounded-lg border border-slate-300 bg-white text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
      >
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            {versionOptionLabel(v, contract.current_version_id)}
          </option>
        ))}
      </select>
    </div>
  );
}

function ContractOverview({ id }: { id: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const versionParam = searchParams.get("version");
  const [contract, setContract] = useState<ContractDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rerunning, setRerunning] = useState(false);
  const [rerunError, setRerunError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const load = useCallback(async () => {
    try {
      setContract(await fetchContractById(id, versionParam));
      setError(null);
    } catch (err: unknown) {
      setError(errorMessage(err, "Could not load contract details."));
    } finally {
      setLoading(false);
    }
  }, [id, versionParam]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  function switchVersion(versionId: string) {
    const isCurrent = versionId === contract?.current_version_id;
    router.push(isCurrent ? `/contracts/${id}` : `/contracts/${id}?version=${encodeURIComponent(versionId)}`);
  }

  function openDeleteDialog() {
    setDeleteError(null);
    setDeleteOpen(true);
  }

  function closeDeleteDialog() {
    if (deleting) return;
    setDeleteOpen(false);
    setDeleteError(null);
  }

  async function handleDelete(permanent: boolean) {
    if (!contract || deleting) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteContract(contract.id, permanent);
      router.push("/");
    } catch (e: unknown) {
      setDeleteError(errorMessage(e, "Failed to delete contract. Please try again."));
      setDeleting(false);
    }
  }

  async function handleRerun() {
    setRerunning(true);
    setRerunError(null);
    try {
      await reprocessContract(id, versionParam);
      await load(); // status is now PENDING, so the ProcessingBanner resumes polling
    } catch (e: unknown) {
      setRerunError(errorMessage(e, "Could not re-run extraction."));
    } finally {
      setRerunning(false);
    }
  }

  if (loading) {
    return (
      <div className="p-12 text-center text-slate-500 text-sm">
        Loading contract details...
      </div>
    );
  }

  if (error || !contract) {
    return (
      <div className="p-12 text-center text-red-500 text-sm">
        {error || "Contract not found"}
      </div>
    );
  }

  const processing = isProcessing(contract.status);
  const ex = contract.extraction_status;
  const viewedVersionId = versionParam ?? contract.current_version_id;
  const viewedVersion = contract.versions?.find((v) => v.id === viewedVersionId) ?? null;
  const viewingOld = Boolean(versionParam && versionParam !== contract.current_version_id);
  const rerunButton = (
    <div className="space-y-1">
      <button
        type="button"
        onClick={handleRerun}
        disabled={rerunning}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-slate-300 hover:bg-slate-50 disabled:opacity-60 text-slate-800 text-xs font-bold rounded-xl transition-colors"
      >
        <RefreshCw className={`w-3.5 h-3.5 ${rerunning ? "animate-spin" : ""}`} aria-hidden="true" />
        Re-run extraction
      </button>
      {rerunError && <p className="text-xs text-red-700">{rerunError}</p>}
    </div>
  );

  const renewalDeadline = pickRenewalDeadline(contract.deadlines ?? []);
  const hasFields = contract.fields.length > 0;
  const legacyFlat: { label: string; value: string | null }[] = [
    { label: "Effective Date", value: formatDate(contract.effective_date, "long") },
    { label: "Expiry Date", value: formatDate(contract.expiry_date, "long") },
    { label: "Renewal Terms", value: contract.renewal_terms },
    { label: "Payment Terms", value: contract.payment_terms },
    { label: "Termination Conditions", value: contract.termination_conditions },
  ];

  return (
    <div>
      <PageHeader
        title={contract.title}
        subtitle={contract.filename}
        breadcrumbs={[{ label: "Dashboard", href: "/" }, { label: contract.title }]}
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <VersionPicker contract={contract} viewedVersionId={viewedVersionId} onChange={switchVersion} />
            <button
              type="button"
              onClick={() => setUploadOpen(true)}
              aria-haspopup="dialog"
              className="flex items-center gap-1.5 px-3 py-2 bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 text-xs font-bold uppercase tracking-wider rounded-xl transition-colors shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              <Upload className="w-4 h-4" aria-hidden="true" />
              Upload new version
            </button>
            <button
              type="button"
              onClick={() => setDetailsOpen(true)}
              aria-haspopup="dialog"
              className="flex items-center gap-1.5 px-3 py-2 bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 text-xs font-bold uppercase tracking-wider rounded-xl transition-colors shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
            >
              <Pencil className="w-4 h-4" aria-hidden="true" />
              Edit details
            </button>
            <ContractStatusBadge status={contract.status} />
            <Link
              href={`/contracts/${contract.id}/chat`}
              className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-xs font-bold uppercase tracking-wider rounded-xl hover:from-blue-700 hover:to-indigo-700 transition-all shadow-md"
            >
              <MessageSquare className="w-4 h-4" />
              Ask AI Assistant
            </Link>
            <Link
              href={`/contracts/${contract.id}/summary${versionParam ? `?version=${encodeURIComponent(versionParam)}` : ""}`}
              className="flex items-center gap-1.5 px-3 py-2 bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 text-xs font-bold uppercase tracking-wider rounded-xl transition-colors shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
              title="A one-page, source-linked summary you can share or print"
            >
              <FileText className="w-4 h-4" aria-hidden="true" />
              Summary
            </Link>
            <a
              href={calendarUrl(contract.id)}
              download
              className="flex items-center gap-1.5 px-3 py-2 bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 text-xs font-bold uppercase tracking-wider rounded-xl transition-colors shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
              title="Download this contract's deadlines as a calendar file"
            >
              <Download className="w-4 h-4" aria-hidden="true" />
              Download .ics
            </a>
            <button
              type="button"
              onClick={openDeleteDialog}
              aria-haspopup="dialog"
              className="flex items-center gap-1.5 px-3 py-2 bg-red-50 border border-red-200 text-red-700 hover:bg-red-100 text-xs font-bold uppercase tracking-wider rounded-xl transition-colors shadow-sm"
              title="Delete Contract"
            >
              <Trash2 className="w-4 h-4" />
              Delete Contract
            </button>
          </div>
        }
      />
      <ContractTabs contractId={contract.id} active="overview" />

      <div className="p-4 sm:p-8 space-y-6">
        {viewingOld && (
          <div
            role="status"
            className="p-4 rounded-2xl border border-amber-300 bg-amber-50 text-amber-950 text-sm flex flex-wrap items-center justify-between gap-3"
          >
            <p className="flex items-start gap-2 font-semibold">
              <Eye className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
              <span>
                {viewedVersion ? `You are viewing ${versionName(viewedVersion)}` : "You are viewing an earlier version"}{" "}
                (not the current version)
              </span>
            </p>
            <div className="flex flex-wrap items-center gap-3 text-xs font-bold">
              <Link
                href={`/contracts/${contract.id}`}
                className="text-blue-700 hover:text-blue-900 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
              >
                Go to current
              </Link>
              <Link
                href={`/contracts/${contract.id}/compare`}
                className="inline-flex items-center gap-1 text-blue-700 hover:text-blue-900 underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
              >
                <GitCompare className="w-3.5 h-3.5" aria-hidden="true" />
                Compare versions
              </Link>
            </div>
          </div>
        )}

        {processing && (
          <ProcessingBanner
            contractId={contract.id}
            versionId={versionParam}
            status={contract.status}
            stage={contract.stage}
            progress={contract.progress}
            onDone={load}
          />
        )}

        {!processing && contract.status === "FAILED" && (
          <Banner tone="red" title="Processing failed">
            <p>{contract.extraction_error ?? "The document could not be processed."}</p>
            {rerunButton}
          </Banner>
        )}

        {!processing && ex === "unavailable" && (
          <Banner tone="red" title="AI extraction is unavailable for this contract">
            <p>
              {contract.extraction_error ?? "The extraction did not run."} The document text is stored and searchable,
              but fields, obligations and deadlines could not be extracted.
            </p>
            {rerunButton}
          </Banner>
        )}

        {!processing && ex === "partial" && (
          <Banner tone="amber" title="Extraction was only partial">
            <p>{contract.extraction_error ?? "Some parts of the document could not be analysed."} Items below may be missing.</p>
            {rerunButton}
          </Banner>
        )}

        {!processing && ex === "unsupported" && (
          <Banner tone="amber" title="This document has no readable text">
            <p>
              {contract.extraction_error ??
                "The PDF appears to be scanned (no text layer) or password-protected, so nothing could be extracted."}
            </p>
          </Banner>
        )}

        {!processing && ex === "legacy" && (
          <Banner tone="amber" title="Extracted by an older version, not verified">
            <p>
              The values below were produced before source verification existed. They have not been checked against
              the document. Re-run extraction to verify them.
            </p>
            {rerunButton}
          </Banner>
        )}

        <ExtractionSourceNotice contractId={contract.id} versionId={versionParam} meta={contract.extraction_meta} />

        {renewalDeadline && (
          <RenewalNoticeCallout contractId={contract.id} versionId={versionParam} deadline={renewalDeadline} />
        )}

        <ReviewSection contract={contract} versionId={versionParam} />

        {/* Top Summary Banner */}
        <div className="bg-slate-900 text-white rounded-2xl p-5 sm:p-6 shadow-xl flex flex-wrap items-center justify-between gap-6">
          <div className="space-y-1 min-w-0">
            <span className="text-[10px] font-bold text-blue-400 uppercase tracking-wider">
              Document Title
            </span>
            <h1 className="text-xl font-bold break-words">{contract.title}</h1>
            <p className="text-xs text-slate-300">
              {contract.filename}
              {contract.page_count > 0 && ` · ${contract.page_count} Pages`} · Status: {contract.status}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-x-6 gap-y-3 sm:border-l sm:border-slate-700 sm:pl-6">
            <div>
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">
                Effective Start
              </span>
              <span className="text-sm font-bold text-white">
                {summaryValue(contract, "effective_date", formatDate(contract.effective_date, "long"))}
              </span>
            </div>
            <div>
              <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">
                Expiry Date
              </span>
              <span className="text-sm font-bold text-amber-300">
                {summaryValue(contract, "expiration_date", formatDate(contract.expiry_date, "long"))}
              </span>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* ─ Contract Terms Panel ─ */}
          <section className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-4">
            <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider pb-2 border-b border-slate-100 flex items-center gap-2">
              <Calendar className="w-4 h-4 text-blue-600" />
              Extracted Contract Terms & Conditions
            </h2>
            {hasFields ? (
              <dl>
                {FIELD_LABELS.flatMap(({ key, label, isDate }) => {
                  const rows = contract.fields
                    .filter((f) => f.field_key === key)
                    .sort((a, b) => a.item_index - b.item_index);
                  if (rows.length === 0) {
                    return [
                      <div key={key} className="py-3 border-b border-slate-100 last:border-0 grid grid-cols-1 sm:grid-cols-5 gap-2 sm:gap-4">
                        <dt className="sm:col-span-2 text-xs font-bold uppercase tracking-wider text-slate-500">{label}</dt>
                        <dd className="sm:col-span-3 min-w-0 break-words text-sm italic text-slate-500">Not extracted</dd>
                      </div>,
                    ];
                  }
                  return rows.map((f) => (
                    <FieldRow
                      key={f.id}
                      contractId={contract.id}
                      versionId={versionParam}
                      field={f}
                      label={label}
                      isDate={Boolean(isDate)}
                      onReviewed={load}
                    />
                  ));
                })}
              </dl>
            ) : ex === "legacy" && legacyFlat.some((r) => r.value) ? (
              <div className="space-y-3">
                <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                  Unverified values from an older extraction. No source references are available.
                </p>
                <dl>
                  {legacyFlat
                    .filter((r) => r.value)
                    .map((r) => (
                      <div key={r.label} className="py-3 border-b border-slate-100 last:border-0 grid grid-cols-1 sm:grid-cols-5 gap-2 sm:gap-4">
                        <dt className="sm:col-span-2 text-xs font-bold uppercase tracking-wider text-slate-500">{r.label}</dt>
                        <dd className="sm:col-span-3 min-w-0 break-words text-sm p-2 rounded-lg border bg-amber-50 border-amber-200 text-amber-950 font-semibold">
                          {r.value}
                        </dd>
                      </div>
                    ))}
                </dl>
              </div>
            ) : (
              <p className="text-xs text-slate-500 text-center py-6">
                {emptyStateText(contract, "contract terms", "No contract terms were extracted from this document.")}
              </p>
            )}
          </section>

          {/* ─ Contract Parties Panel ─ */}
          <section className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-4">
            <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider pb-2 border-b border-slate-100 flex items-center gap-2">
              <Users className="w-4 h-4 text-blue-600" />
              Identified Contract Parties ({contract.parties.length})
            </h2>
            {contract.parties.length === 0 ? (
              <p className="text-xs text-slate-500 text-center py-6">
                {emptyStateText(contract, "parties", "No parties were extracted from this document.")}
              </p>
            ) : (
              <div className="space-y-3">
                {contract.parties.map((party) => (
                  <div
                    key={party.id}
                    className="p-4 bg-slate-50 rounded-xl border border-slate-200 flex items-start gap-3"
                  >
                    <div
                      className="w-8 h-8 rounded-full bg-blue-600 text-white flex items-center justify-center font-bold text-xs shrink-0 mt-0.5"
                      aria-hidden="true"
                    >
                      {party.role.charAt(0).toUpperCase() || "?"}
                    </div>
                    <div className="space-y-2 min-w-0">
                      <div>
                        <h3 className="text-sm font-bold text-slate-900 break-words">{party.name}</h3>
                        <p className="text-xs text-blue-700 font-semibold mt-0.5">Role: {party.role}</p>
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusChip status={party.status} />
                        <ConfidenceBadge confidence={party.confidence} />
                      </div>
                      <SourceRef
                        contractId={contract.id}
                        versionId={versionParam}
                        page={party.source_page}
                        section={party.source_section}
                        quote={party.source_quote}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        </div>

        {/* ─ Classified Clauses Panel ─ */}
        <section className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-4">
          <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider pb-2 border-b border-slate-100 flex items-center gap-2">
            <Activity className="w-4 h-4 text-blue-600" />
            Key Classified Clauses ({contract.clauses.length})
          </h2>
          {contract.clauses.length === 0 ? (
            <p className="text-xs text-slate-500 text-center py-6">
              {emptyStateText(contract, "clauses", "No clauses were extracted from this document.")}
            </p>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {contract.clauses.map((clause) => {
                const quote =
                  clause.source_quote && clause.source_quote.trim() !== clause.content.trim()
                    ? clause.source_quote
                    : null;
                return (
                  <div key={clause.id} className="bg-slate-50 rounded-xl border border-slate-200 p-4 space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-bold text-slate-900 min-w-0 break-words">{clause.title}</span>
                      <ClauseTypeBadge type={clause.clause_type} />
                    </div>
                    <p className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
                      Document text
                    </p>
                    <p className="text-xs text-slate-700 leading-relaxed font-sans whitespace-pre-wrap">
                      {clause.content}
                    </p>
                    <div className="flex flex-wrap items-center gap-2">
                      <StatusChip status={clause.status} />
                      <ConfidenceBadge confidence={clause.confidence} />
                    </div>
                    <div className="pt-1 border-t border-slate-200/60">
                      <SourceRef
                        contractId={contract.id}
                        versionId={versionParam}
                        page={clause.source_page}
                        section={clause.source_section}
                        quote={quote}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      </div>

      {uploadOpen && (
        <UploadVersionDialog contractId={contract.id} onClose={() => setUploadOpen(false)} onChanged={load} />
      )}

      {detailsOpen && (
        <ContractDetailsDialog
          contract={contract}
          onClose={() => setDetailsOpen(false)}
          onSaved={() => {
            setDetailsOpen(false);
            void load();
          }}
        />
      )}

      {deleteOpen && (
        <ConfirmDialog
          title={`Delete "${contract.title}"?`}
          confirmLabel="Delete"
          busyLabel="Deleting..."
          busy={deleting}
          error={deleteError}
          onConfirm={() => handleDelete(false)}
          onCancel={closeDeleteDialog}
          secondary={{
            label: "Delete permanently (cannot be undone)",
            busyLabel: "Deleting permanently...",
            hint: "Or erase the contract and its files for good. This cannot be recovered.",
            onClick: () => handleDelete(true),
          }}
        >
          <p>This contract and all of its versions will be removed from your contracts list.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}

export default function ContractOverviewPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return (
    <Suspense fallback={<div className="p-12 text-center text-slate-500 text-sm">Loading contract details...</div>}>
      <ContractOverview id={id} />
    </Suspense>
  );
}
