"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { GitCompare, Loader2, Upload, X } from "lucide-react";
import { ProcessingBanner } from "@/components/ui/ProcessingBanner";
import { errorMessage, fetchSystemStatus, uploadVersion } from "@/lib/api";
import { versionName } from "@/lib/format";
import type { ContractStatusInfo, DocumentKind, VersionSummary } from "@/lib/types";
import { isProcessing } from "@/lib/types";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const KIND_OPTIONS: { value: DocumentKind; label: string }[] = [
  { value: "MAIN", label: "New version of the main agreement" },
  { value: "AMENDMENT", label: "Amendment" },
  { value: "SOW", label: "Statement of work" },
  { value: "EXHIBIT", label: "Exhibit / schedule" },
  { value: "OTHER", label: "Other" },
];

type Phase = "form" | "uploading" | "processing" | "finished";

interface UploadVersionDialogProps {
  contractId: string;
  onClose: () => void;
  /** Called after the upload was accepted and again when processing of the new version finishes. */
  onChanged?: () => void;
}

/** Accessible modal to upload a further version (v2, an amendment, ...) of an existing contract. */
export function UploadVersionDialog({ contractId, onClose, onChanged }: UploadVersionDialogProps) {
  const uid = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [maxMb, setMaxMb] = useState<number | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState<DocumentKind>("MAIN");
  const [makeCurrent, setMakeCurrent] = useState(true);
  const [formError, setFormError] = useState<string | null>(null);

  const [phase, setPhase] = useState<Phase>("form");
  const [uploaded, setUploaded] = useState<VersionSummary | null>(null);
  const [final, setFinal] = useState<ContractStatusInfo | null>(null);

  // Remember what had focus so it can be restored on close; move focus into the dialog.
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (fileRef.current) fileRef.current.focus();
    else dialogRef.current?.focus();
    return () => {
      previouslyFocused?.focus();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchSystemStatus()
      .then((s) => {
        if (!cancelled) setMaxMb(s.max_upload_mb);
      })
      .catch(() => {
        // Without the limit the server still enforces it and its message is shown.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;
    const nodes = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (nodes.length === 0) {
      e.preventDefault();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === dialogRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  /** Returns an error message, or null when the file can be uploaded. */
  function checkFile(f: File | null): string | null {
    if (!f) return "Choose a PDF or Word file to upload.";
    const lower = f.name.toLowerCase();
    if (!lower.endsWith(".pdf") && !lower.endsWith(".docx")) return "Only PDF (.pdf) and Word (.docx) files are supported.";
    if (f.size === 0) return "That file is empty.";
    if (maxMb != null && f.size > maxMb * 1024 * 1024) {
      return `That file is ${(f.size / (1024 * 1024)).toFixed(1)} MB, which is over the ${maxMb} MB upload limit.`;
    }
    return null;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const problem = checkFile(file);
    if (problem || !file) {
      setFormError(problem);
      return;
    }
    setFormError(null);
    setPhase("uploading");
    try {
      const created = await uploadVersion(contractId, file, {
        label: label.trim() || undefined,
        kind,
        makeCurrent,
      });
      setUploaded(created);
      setPhase(isProcessing(created.status) ? "processing" : "finished");
      if (!isProcessing(created.status)) {
        setFinal({
          id: contractId,
          current_version_id: null,
          status: created.status,
          stage: created.stage,
          progress: created.progress,
          extraction_status: created.extraction_status,
          extraction_error: created.extraction_error,
        });
      }
      onChanged?.();
    } catch (err: unknown) {
      setFormError(errorMessage(err, "Upload failed."));
      setPhase("form");
    }
  }

  function handleProcessed(info: ContractStatusInfo) {
    setFinal(info);
    setPhase("finished");
    onChanged?.();
  }

  const titleId = `${uid}-title`;
  const descId = `${uid}-desc`;
  const inputClass =
    "w-full px-3 py-2 text-sm rounded-lg border border-slate-300 bg-white text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 disabled:bg-slate-100 disabled:text-slate-500";
  const secondaryBtn =
    "px-4 py-2 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";
  const primaryBtn =
    "inline-flex items-center gap-1.5 px-4 py-2 text-xs font-bold rounded-xl bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";

  const succeeded = final?.status === "READY";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && phase !== "uploading") onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-slate-200 focus:outline-none"
      >
        <div className="flex items-start justify-between gap-4 p-5 border-b border-slate-100">
          <div>
            <h2 id={titleId} className="text-base font-bold text-slate-900">
              Upload new version
            </h2>
            <p id={descId} className="text-xs text-slate-500 mt-0.5">
              Add a renegotiated version or an amendment. The earlier version is kept, so you can compare the two.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close upload dialog"
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-900 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        {phase === "form" || phase === "uploading" ? (
          <form onSubmit={handleSubmit} noValidate>
            <div className="p-5 space-y-4">
              <div className="space-y-1">
                <label htmlFor={`${uid}-file`} className="text-xs font-semibold text-slate-700">
                  PDF or Word file
                </label>
                <input
                  ref={fileRef}
                  id={`${uid}-file`}
                  type="file"
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  disabled={phase === "uploading"}
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null;
                    setFile(f);
                    setFormError(f ? checkFile(f) : null);
                  }}
                  aria-describedby={`${uid}-file-hint`}
                  className="block w-full text-xs text-slate-700 file:mr-3 file:px-3 file:py-1.5 file:rounded-lg file:border file:border-slate-300 file:bg-white file:text-xs file:font-bold file:text-slate-800 hover:file:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                />
                <p id={`${uid}-file-hint`} className="text-[11px] text-slate-500">
                  PDF (.pdf) or Word (.docx){maxMb != null ? `, up to ${maxMb} MB` : ""}. Scanned documents take longer.
                </p>
              </div>

              <div className="space-y-1">
                <label htmlFor={`${uid}-label`} className="text-xs font-semibold text-slate-700">
                  Label <span className="font-normal text-slate-500">(optional)</span>
                </label>
                <input
                  id={`${uid}-label`}
                  type="text"
                  value={label}
                  maxLength={120}
                  disabled={phase === "uploading"}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder='For example "v2" or "Amendment 1"'
                  className={inputClass}
                />
              </div>

              <div className="space-y-1">
                <label htmlFor={`${uid}-kind`} className="text-xs font-semibold text-slate-700">
                  What is this document?
                </label>
                <select
                  id={`${uid}-kind`}
                  value={kind}
                  disabled={phase === "uploading"}
                  onChange={(e) => setKind(e.target.value as DocumentKind)}
                  className={inputClass}
                >
                  {KIND_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex items-start gap-3">
                <input
                  id={`${uid}-current`}
                  type="checkbox"
                  checked={makeCurrent}
                  disabled={phase === "uploading"}
                  onChange={(e) => setMakeCurrent(e.target.checked)}
                  className="mt-0.5 h-4 w-4 rounded border-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                />
                <div>
                  <label htmlFor={`${uid}-current`} className="text-sm font-semibold text-slate-800">
                    Make this the current version
                  </label>
                  <p className="text-xs text-slate-500">
                    The current version drives the dashboard, deadlines and review flags.
                  </p>
                </div>
              </div>

              <div aria-live="polite" className="min-h-[1rem] space-y-2">
                {phase === "uploading" && (
                  <p className="flex items-center gap-2 text-xs text-blue-900 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
                    Uploading {file?.name ?? "the file"}...
                  </p>
                )}
                {formError && (
                  <p role="alert" className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                    {formError}
                  </p>
                )}
              </div>
            </div>

            <div className="flex flex-wrap justify-end gap-2 p-5 border-t border-slate-100">
              <button type="button" onClick={onClose} disabled={phase === "uploading"} className={`${secondaryBtn} disabled:opacity-50`}>
                Cancel
              </button>
              <button type="submit" disabled={phase === "uploading"} className={primaryBtn}>
                <Upload className="w-3.5 h-3.5" aria-hidden="true" />
                {phase === "uploading" ? "Uploading..." : "Upload version"}
              </button>
            </div>
          </form>
        ) : (
          <>
            <div className="p-5 space-y-4">
              {uploaded && (
                <p className="text-sm text-slate-800">
                  <span className="font-semibold">{versionName(uploaded)}</span> was uploaded
                  {uploaded.filename ? ` (${uploaded.filename})` : ""}.
                  {makeCurrent ? " It is now the current version." : " It was added without becoming the current version."}
                </p>
              )}

              {phase === "processing" && uploaded && (
                <>
                  <ProcessingBanner
                    contractId={contractId}
                    versionId={uploaded.id}
                    status={uploaded.status}
                    stage={uploaded.stage}
                    progress={uploaded.progress}
                    onDone={handleProcessed}
                  />
                  <p className="text-xs text-slate-500">
                    Processing continues in the background if you close this dialog.
                  </p>
                </>
              )}

              <div aria-live="polite">
                {phase === "finished" && final && succeeded && (
                  <p className="text-xs text-green-900 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
                    Processing finished. You can now compare this version with the previous one.
                  </p>
                )}
                {phase === "finished" && final && !succeeded && (
                  <p role="alert" className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                    {final.status === "UNSUPPORTED"
                      ? "This document has no readable text (it may be password-protected), so it cannot be compared."
                      : "Processing failed, so this version cannot be compared."}
                    {final.extraction_error ? ` ${final.extraction_error}` : ""}
                  </p>
                )}
              </div>
            </div>

            <div className="flex flex-wrap justify-end gap-2 p-5 border-t border-slate-100">
              <button type="button" onClick={onClose} className={secondaryBtn}>
                Close
              </button>
              {phase === "finished" && succeeded && uploaded && (
                <Link
                  href={`/contracts/${contractId}/compare?to=${encodeURIComponent(uploaded.id)}`}
                  className={primaryBtn}
                >
                  <GitCompare className="w-3.5 h-3.5" aria-hidden="true" />
                  Compare with previous version
                </Link>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
