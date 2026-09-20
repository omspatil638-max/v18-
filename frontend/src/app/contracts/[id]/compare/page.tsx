"use client";

import { Suspense, use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import {
  ArrowLeftRight,
  GitCompare,
  Info,
  Loader2,
  RefreshCw,
  Upload,
  AlertTriangle,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ContractTabs } from "@/components/ui/Badges";
import { AiDisclaimer } from "@/components/ui/AiDisclaimer";
import { ProgressBar } from "@/components/ui/ProcessingBanner";
import { UploadVersionDialog } from "@/components/UploadVersionDialog";
import { AlignedSectionsView } from "@/components/AlignedSectionsView";
import { UnchangedRow, VersionChangeCard } from "@/components/VersionChangeCard";
import {
  ApiError,
  errorMessage,
  fetchAlignedSections,
  fetchComparison,
  fetchContractById,
  fetchVersions,
  runComparison,
} from "@/lib/api";
import { versionName, versionOptionLabel } from "@/lib/format";
import { isProcessing } from "@/lib/types";
import type { AlignedSections, ComparisonResponse, VersionChange, VersionSummary } from "@/lib/types";

const POLL_MS = 2000;

type Tab = "list" | "side";

interface Selection {
  from: string | null;
  to: string | null;
}

type Problem = { kind: "error" | "not_ready" | "no_earlier"; message: string };

/** Picks the pair to compare: URL params first, else the current version against the one before it. */
function pickDefaults(
  versions: VersionSummary[],
  currentId: string | null,
  fromParam: string | null,
  toParam: string | null,
): Selection {
  if (versions.length === 0) return { from: null, to: null };
  const sorted = [...versions].sort((a, b) => a.version_number - b.version_number);
  const byId = new Map(sorted.map((v) => [v.id, v] as const));
  const previousOf = (v: VersionSummary): VersionSummary | null =>
    [...sorted].reverse().find((x) => x.version_number < v.version_number) ?? null;

  let to = (toParam ? byId.get(toParam) : undefined) ?? (currentId ? byId.get(currentId) : undefined) ?? sorted[sorted.length - 1];
  let from = fromParam && fromParam !== to.id ? (byId.get(fromParam) ?? null) : null;
  if (!from) from = previousOf(to);
  // The current version can be the oldest one (a newer upload was not made current): compare the newest two instead.
  if (!from && !fromParam && !toParam && sorted.length > 1) {
    to = sorted[sorted.length - 1];
    from = previousOf(to);
  }
  return { from: from?.id ?? null, to: to.id };
}

function Chip({ children, tone = "slate" }: { children: React.ReactNode; tone?: "slate" | "green" | "red" | "amber" | "orange" | "blue" }) {
  const tones = {
    slate: "bg-slate-100 text-slate-700 border-slate-200",
    green: "bg-green-100 text-green-900 border-green-300",
    red: "bg-red-100 text-red-900 border-red-300",
    amber: "bg-amber-100 text-amber-900 border-amber-300",
    orange: "bg-orange-100 text-orange-900 border-orange-300",
    blue: "bg-blue-100 text-blue-900 border-blue-300",
  }[tone];
  return (
    <li className={`px-2.5 py-1 rounded-full text-xs font-semibold border whitespace-nowrap ${tones}`}>{children}</li>
  );
}

function Notice({
  tone,
  title,
  children,
  role,
}: {
  tone: "info" | "warn" | "error";
  title?: string;
  children?: React.ReactNode;
  role?: "alert" | "status";
}) {
  const styles = {
    info: "bg-blue-50 border-blue-200 text-blue-950",
    warn: "bg-amber-50 border-amber-200 text-amber-950",
    error: "bg-red-50 border-red-200 text-red-900",
  }[tone];
  const Icon = tone === "info" ? Info : AlertTriangle;
  return (
    <div role={role} className={`p-4 rounded-2xl border text-sm flex items-start gap-3 ${styles}`}>
      <Icon className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
      <div className="space-y-1 min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children}
      </div>
    </div>
  );
}

function Spinner({ text }: { text: string }) {
  return (
    <div role="status" className="p-10 bg-white rounded-2xl border border-slate-200 text-center text-sm text-slate-600 space-y-2">
      <Loader2 className="w-6 h-6 mx-auto animate-spin text-blue-600" aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}

function VersionSelect({
  id,
  label,
  value,
  versions,
  currentId,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  versions: VersionSummary[];
  currentId: string | null;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1 min-w-0">
      <label htmlFor={id} className="block text-[11px] font-bold uppercase tracking-wider text-slate-500">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full sm:w-64 max-w-full px-2 py-1.5 text-xs font-semibold rounded-lg border border-slate-300 bg-white text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
      >
        {versions.map((v) => (
          <option key={v.id} value={v.id}>
            {versionOptionLabel(v, currentId)}
          </option>
        ))}
      </select>
    </div>
  );
}

function NoEarlierVersion({ onUpload }: { onUpload: () => void }) {
  return (
    <div className="p-10 bg-white rounded-2xl border border-slate-200 text-center space-y-3">
      <GitCompare className="w-8 h-8 mx-auto text-blue-600" aria-hidden="true" />
      <h2 className="text-base font-bold text-slate-900">There is nothing to compare yet</h2>
      <p className="text-sm text-slate-600 max-w-md mx-auto">
        This contract has only one version. Upload a renegotiated version or an amendment, and ContractLens will show
        what changed between them.
      </p>
      <button
        type="button"
        onClick={onUpload}
        aria-haspopup="dialog"
        className="inline-flex items-center gap-1.5 px-4 py-2 text-xs font-bold rounded-xl bg-blue-600 hover:bg-blue-700 text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      >
        <Upload className="w-4 h-4" aria-hidden="true" />
        Upload new version
      </button>
    </div>
  );
}

function VersionProgress({ version }: { version: VersionSummary }) {
  const busy = isProcessing(version.status);
  return (
    <li className="rounded-xl border border-slate-200 bg-white p-3 space-y-1.5">
      <p className="text-sm font-semibold text-slate-900">{versionName(version)}</p>
      {busy ? (
        <>
          <ProgressBar progress={version.progress} label={`Processing ${versionName(version)}`} />
          <p className="text-xs text-slate-600">
            {version.stage || "Processing"} · {Math.round(version.progress)}%
          </p>
        </>
      ) : version.status === "READY" ? (
        <p className="text-xs text-green-800">Ready</p>
      ) : (
        <p className="text-xs text-red-800">
          {version.status === "UNSUPPORTED" ? "Unsupported PDF" : "Processing failed"}
          {version.extraction_error ? `: ${version.extraction_error}` : ""}
        </p>
      )}
    </li>
  );
}

function ChangeList({
  contractId,
  cmp,
  includeUnchanged,
  onIncludeUnchanged,
  refreshing,
}: {
  contractId: string;
  cmp: ComparisonResponse;
  includeUnchanged: boolean;
  onIncludeUnchanged: (v: boolean) => void;
  refreshing: boolean;
}) {
  const from = cmp.from_version;
  const to = cmp.to_version;
  const keyTerms = cmp.changes.filter((c) => c.category !== "section");
  const clauses = cmp.changes.filter((c) => c.category === "section");
  const anyChanged = cmp.changes.some((c) => c.change_type !== "UNCHANGED");

  function renderGroup(title: string, list: VersionChange[], emptyText: string) {
    const changed = list.filter((c) => c.change_type !== "UNCHANGED");
    const unchanged = list.filter((c) => c.change_type === "UNCHANGED");
    return (
      <section aria-label={title} className="space-y-3">
        <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider pb-2 border-b border-slate-200">
          {title} ({changed.length} changed)
        </h3>
        {changed.length === 0 && <p className="text-sm text-slate-500">{emptyText}</p>}
        {changed.map((c) => (
          <VersionChangeCard
            key={c.id}
            contractId={contractId}
            fromVersion={from}
            toVersion={to}
            change={c}
            impactStatus={cmp.impact_status}
          />
        ))}
        {unchanged.length > 0 && (
          <ul className="space-y-1.5" aria-label={`Unchanged ${title.toLowerCase()}`}>
            {unchanged.map((c) => (
              <UnchangedRow key={c.id} contractId={contractId} toVersion={to} change={c} />
            ))}
          </ul>
        )}
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <input
            id="include-unchanged"
            type="checkbox"
            checked={includeUnchanged}
            onChange={(e) => onIncludeUnchanged(e.target.checked)}
            className="h-4 w-4 rounded border-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          />
          <label htmlFor="include-unchanged" className="text-sm font-semibold text-slate-800">
            Show unchanged clauses
          </label>
          {refreshing && <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-500" aria-hidden="true" />}
        </div>
      </div>

      {cmp.impact_status === "unavailable" && (
        <Notice tone="info" title="AI impact explanations are unavailable">
          <p className="text-xs">
            The differences below were found by comparing the documents directly, not by AI. Only the plain-English
            explanation of what each change could mean is missing. Re-run the comparison once an AI key is configured.
          </p>
        </Notice>
      )}
      {cmp.impact_status === "partial" && (
        <Notice tone="info">
          <p className="text-xs">
            Some impact explanations could not be produced or verified, so those changes have none.
          </p>
        </Notice>
      )}

      {!anyChanged && (
        <p className="text-sm text-slate-700 bg-white border border-slate-200 rounded-2xl p-6 text-center">
          No differences were found between {versionName(from)} and {versionName(to)} in the extracted key terms and
          clauses.
        </p>
      )}

      {renderGroup("Key terms", keyTerms, "No key terms changed between these versions.")}
      {renderGroup("Clauses", clauses, "No clause wording changed between these versions.")}
    </div>
  );
}

function ComparePageBody({ id }: { id: string }) {
  const searchParams = useSearchParams();
  const fromParam = searchParams.get("from");
  const toParam = searchParams.get("to");

  const [title, setTitle] = useState<string | null>(null);
  const [currentId, setCurrentId] = useState<string | null>(null);
  const [versions, setVersions] = useState<VersionSummary[] | null>(null);
  const [infoError, setInfoError] = useState<string | null>(null);

  const [sel, setSel] = useState<Selection | null>(null);
  const [tab, setTab] = useState<Tab>("list");
  const [includeUnchanged, setIncludeUnchanged] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  const [data, setData] = useState<ComparisonResponse | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [fetching, setFetching] = useState(false);

  const [sections, setSections] = useState<AlignedSections | null>(null);
  const [sectionsError, setSectionsError] = useState<string | null>(null);
  const [sectionsLoading, setSectionsLoading] = useState(false);

  const [rerunning, setRerunning] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);

  const loadInfo = useCallback(async () => {
    try {
      const c = await fetchContractById(id);
      setTitle(c.title);
      setCurrentId(c.current_version_id);
      setVersions(c.versions ?? []);
      setInfoError(null);
    } catch (e: unknown) {
      setInfoError(errorMessage(e, "Could not load this contract."));
    }
  }, [id]);

  useEffect(() => {
    loadInfo();
  }, [loadInfo]);

  // Choose the pair once the versions are known, and again whenever the URL asks for a different pair.
  const paramsKey = `${fromParam ?? ""}|${toParam ?? ""}`;
  const appliedKey = useRef<string | null>(null);
  useEffect(() => {
    if (!versions) return;
    if (appliedKey.current === paramsKey && sel) return;
    appliedKey.current = paramsKey;
    setSel(pickDefaults(versions, currentId, fromParam, toParam));
  }, [versions, currentId, paramsKey, fromParam, toParam, sel]);

  const fromId = sel?.from ?? null;
  const toId = sel?.to ?? null;
  const canCompare = Boolean(fromId && toId && fromId !== toId);

  // The comparison for the selected pair; polls while it is being computed or a version is still processing.
  useEffect(() => {
    if (!canCompare) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setProblem(null);
    setFetching(true);

    async function run() {
      try {
        const res = await fetchComparison(id, { fromVersionId: fromId, toVersionId: toId, includeUnchanged });
        if (cancelled) return;
        setData(res);
        setProblem(null);
        setFetching(false);
        if (res.status === "running" || res.status === "versions_not_ready") {
          if (res.status === "versions_not_ready") {
            fetchVersions(id)
              .then((v) => {
                if (!cancelled) setVersions(v);
              })
              .catch(() => undefined);
          }
          timer = setTimeout(run, POLL_MS);
        }
      } catch (e: unknown) {
        if (cancelled) return;
        setFetching(false);
        if (e instanceof ApiError && e.status === 409) {
          setProblem({ kind: "not_ready", message: e.message });
          fetchVersions(id)
            .then((v) => {
              if (!cancelled) setVersions(v);
            })
            .catch(() => undefined);
          timer = setTimeout(run, POLL_MS);
          return;
        }
        if (e instanceof ApiError && e.status === 422 && /earlier version/i.test(e.message)) {
          setProblem({ kind: "no_earlier", message: e.message });
          return;
        }
        setProblem({ kind: "error", message: errorMessage(e, "Could not compare these versions.") });
      }
    }

    run();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [id, fromId, toId, includeUnchanged, reloadKey, canCompare]);

  // The comparison only counts if it is for the pair that is selected right now.
  const cmp = data && data.from_version.id === fromId && data.to_version.id === toId ? data : null;
  const cmpStatus = cmp?.status ?? null;

  // Side-by-side text, fetched only when that tab is open and the comparison is ready.
  useEffect(() => {
    if (tab !== "side" || !canCompare || cmpStatus !== "ready") return;
    let cancelled = false;
    setSectionsLoading(true);
    setSectionsError(null);
    fetchAlignedSections(id, { fromVersionId: fromId, toVersionId: toId })
      .then((res) => {
        if (!cancelled) setSections(res);
      })
      .catch((e: unknown) => {
        if (!cancelled) setSectionsError(errorMessage(e, "Could not load the side-by-side view."));
      })
      .finally(() => {
        if (!cancelled) setSectionsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, fromId, toId, tab, cmpStatus, canCompare, reloadKey]);

  const sectionsForPair =
    sections && sections.from_version_id === fromId && sections.to_version_id === toId ? sections : null;

  // Tell screen readers when the comparison state changes.
  useEffect(() => {
    if (cmpStatus === "running") setAnnounce("Comparing the versions...");
    else if (cmpStatus === "versions_not_ready") setAnnounce("Waiting for a version to finish processing.");
    else if (cmpStatus === "ready") setAnnounce("Comparison ready.");
  }, [cmpStatus]);

  async function handleRerun() {
    if (!canCompare) return;
    setRerunning(true);
    setActionError(null);
    try {
      await runComparison(id, { fromVersionId: fromId, toVersionId: toId });
      setData(null);
      setSections(null);
      setReloadKey((k) => k + 1);
      setAnnounce("Comparison re-run started.");
    } catch (e: unknown) {
      setActionError(errorMessage(e, "Could not re-run the comparison."));
    } finally {
      setRerunning(false);
    }
  }

  function swap() {
    setSel((s) => (s ? { from: s.to, to: s.from } : s));
  }

  const sortedVersions = useMemo(
    () => [...(versions ?? [])].sort((a, b) => b.version_number - a.version_number),
    [versions],
  );

  const btn =
    "inline-flex items-center gap-1.5 px-3 py-2 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";

  const uploadButton = (
    <button type="button" onClick={() => setUploadOpen(true)} aria-haspopup="dialog" className={btn}>
      <Upload className="w-4 h-4" aria-hidden="true" />
      Upload new version
    </button>
  );

  function body() {
    if (infoError) {
      return (
        <Notice tone="error" role="alert" title="Could not load this contract">
          <p>{infoError}</p>
          <button type="button" onClick={loadInfo} className={`${btn} mt-2`}>
            <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
            Try again
          </button>
        </Notice>
      );
    }
    if (!versions || !sel) return <Spinner text="Loading versions..." />;
    if (versions.length < 2 || !fromId || problem?.kind === "no_earlier") {
      return <NoEarlierVersion onUpload={() => setUploadOpen(true)} />;
    }

    const controls = (
      <div className="bg-white rounded-2xl border border-slate-200 p-4 shadow-sm flex flex-wrap items-end gap-3">
        <VersionSelect
          id="compare-from"
          label="From (older)"
          value={fromId ?? ""}
          versions={sortedVersions}
          currentId={currentId}
          onChange={(v) => setSel((s) => (s ? { ...s, from: v } : s))}
        />
        <button type="button" onClick={swap} className={btn} aria-label="Swap the two versions">
          <ArrowLeftRight className="w-4 h-4" aria-hidden="true" />
          Swap
        </button>
        <VersionSelect
          id="compare-to"
          label="To (newer)"
          value={toId ?? ""}
          versions={sortedVersions}
          currentId={currentId}
          onChange={(v) => setSel((s) => (s ? { ...s, to: v } : s))}
        />
        <button type="button" onClick={handleRerun} disabled={rerunning || !canCompare} className={`${btn} sm:ml-auto`}>
          <RefreshCw className={`w-3.5 h-3.5 ${rerunning ? "animate-spin" : ""}`} aria-hidden="true" />
          Re-run comparison
        </button>
        {actionError && (
          <p role="alert" className="basis-full text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {actionError}
          </p>
        )}
      </div>
    );

    let content: React.ReactNode;
    if (!canCompare) {
      content = (
        <Notice tone="info" role="status">
          <p>Choose two different versions to compare.</p>
        </Notice>
      );
    } else if (problem?.kind === "error" && !cmp) {
      content = (
        <Notice tone="error" role="alert" title="The versions could not be compared">
          <p>{problem.message}</p>
          <button type="button" onClick={() => setReloadKey((k) => k + 1)} className={`${btn} mt-2`}>
            <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
            Try again
          </button>
        </Notice>
      );
    } else if (problem?.kind === "not_ready" || cmp?.status === "versions_not_ready") {
      const pair = [fromId, toId]
        .map((vid) => (cmp ? [cmp.from_version, cmp.to_version] : (versions ?? [])).find((v) => v.id === vid))
        .filter((v): v is VersionSummary => Boolean(v));
      content = (
        <div className="space-y-3">
          <Notice tone="info" role="status" title="One of these versions is still being processed">
            <p>
              {problem?.message ||
                "The comparison starts automatically once both versions have finished processing. This page updates by itself."}
            </p>
          </Notice>
          <ul className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {pair.map((v) => (
              <VersionProgress key={v.id} version={v} />
            ))}
          </ul>
        </div>
      );
    } else if (!cmp) {
      content = <Spinner text={fetching ? "Loading the comparison..." : "Preparing the comparison..."} />;
    } else if (cmp.status === "running") {
      content = <Spinner text={`Comparing ${versionName(cmp.from_version)} with ${versionName(cmp.to_version)}. This can take a minute...`} />;
    } else if (cmp.status === "not_run") {
      content = (
        <div className="p-8 bg-white rounded-2xl border border-slate-200 text-center space-y-3">
          <p className="text-sm text-slate-700">These two versions have not been compared yet.</p>
          <button type="button" onClick={handleRerun} disabled={rerunning} className={btn}>
            <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
            Run comparison
          </button>
        </div>
      );
    } else {
      const s = cmp.summary;
      content = (
        <div className="space-y-4">
          <ul aria-label="Summary of changes" className="flex flex-wrap gap-2">
            <Chip tone="blue">
              {s.key_changes} key {s.key_changes === 1 ? "change" : "changes"}
            </Chip>
            <Chip tone="green">{s.added} added</Chip>
            <Chip tone="red">{s.removed} removed</Chip>
            <Chip tone="amber">{s.modified} changed</Chip>
            <Chip>{s.unchanged} unchanged</Chip>
            <Chip tone="orange">{s.material} material</Chip>
          </ul>

          <div role="tablist" aria-label="Comparison view" className="flex gap-1 border-b border-slate-200">
            {(
              [
                ["list", "Change list"],
                ["side", "Side by side"],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                role="tab"
                id={`tab-${key}`}
                aria-selected={tab === key}
                aria-controls={`panel-${key}`}
                tabIndex={tab === key ? 0 : -1}
                onClick={() => setTab(key)}
                onKeyDown={(e) => {
                  if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                    const next: Tab = key === "list" ? "side" : "list";
                    setTab(next);
                    document.getElementById(`tab-${next}`)?.focus();
                  }
                }}
                className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 ${
                  tab === key
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-slate-500 hover:text-slate-800 hover:border-slate-300"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {tab === "list" ? (
            <div role="tabpanel" id="panel-list" aria-labelledby="tab-list">
              <ChangeList
                contractId={id}
                cmp={cmp}
                includeUnchanged={includeUnchanged}
                onIncludeUnchanged={setIncludeUnchanged}
                refreshing={fetching}
              />
            </div>
          ) : (
            <div role="tabpanel" id="panel-side" aria-labelledby="tab-side">
              {sectionsError ? (
                <Notice tone="error" role="alert" title="Could not load the side-by-side view">
                  <p>{sectionsError}</p>
                  <button type="button" onClick={() => setReloadKey((k) => k + 1)} className={`${btn} mt-2`}>
                    <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
                    Try again
                  </button>
                </Notice>
              ) : sectionsLoading || !sectionsForPair ? (
                <Spinner text="Loading the side-by-side view..." />
              ) : (
                <AlignedSectionsView
                  contractId={id}
                  fromVersion={cmp.from_version}
                  toVersion={cmp.to_version}
                  sections={sectionsForPair.sections}
                />
              )}
            </div>
          )}
        </div>
      );
    }

    return (
      <>
        {controls}
        {problem?.kind === "error" && cmp && (
          <Notice tone="error" role="alert" title="Could not refresh the comparison">
            <p>{problem.message}</p>
          </Notice>
        )}
        {cmp && cmp.notes.length > 0 && (
          <Notice tone="info" title="Notes on this comparison">
            <ul className="list-disc pl-4 text-xs space-y-0.5">
              {cmp.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </Notice>
        )}
        {content}
      </>
    );
  }

  return (
    <div>
      <PageHeader
        title={title ? `${title} — Changes` : "Contract changes"}
        subtitle="What changed between two versions, with evidence in both documents"
        breadcrumbs={[
          { label: "Dashboard", href: "/" },
          { label: title ?? "Contract", href: `/contracts/${id}` },
          { label: "Changes" },
        ]}
        actions={uploadButton}
      />
      <ContractTabs contractId={id} active="compare" />

      <div className="p-4 sm:p-8 space-y-6">
        <p className="text-xs text-slate-600">
          Differences in key terms are worked out directly from the extracted text. The AI only explains what a change
          could mean, and its explanation can be missing. Every change links to the exact text in both versions.
        </p>

        {body()}

        <AiDisclaimer className="rounded-xl border" />
        <div aria-live="polite" className="sr-only">
          {announce}
        </div>
      </div>

      {uploadOpen && <UploadVersionDialog contractId={id} onClose={() => setUploadOpen(false)} onChanged={loadInfo} />}
    </div>
  );
}

export default function ComparePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense fallback={<div className="p-12 text-center text-sm text-slate-400">Loading versions...</div>}>
      <ComparePageBody id={id} />
    </Suspense>
  );
}
