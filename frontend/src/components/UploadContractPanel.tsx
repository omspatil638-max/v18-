"use client";

import { useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { CheckCircle2, Loader2, Upload } from "lucide-react";
import { errorMessage, fetchSystemStatus, uploadContract } from "@/lib/api";
import type { ContractSummary } from "@/lib/types";

const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";

interface UploadContractPanelProps {
  /** Called with the new contract once the server accepted the upload. */
  onUploaded: (contract: ContractSummary) => void;
  /** Leave out the "Open <title>" link after an upload (used where leaving the page would interrupt a flow). */
  hideOpenLink?: boolean;
}

/** Upload a PDF or Word (.docx) contract from the library page. Click to choose a file, or drop one here. */
export function UploadContractPanel({ onUploaded, hideOpenLink = false }: UploadContractPanelProps) {
  const uid = useId();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [maxMb, setMaxMb] = useState<number | null>(null);
  const [uploading, setUploading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<ContractSummary | null>(null);
  const [dragging, setDragging] = useState(false);

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

  /** Returns an error message, or null when the file can be uploaded. */
  function checkFile(f: File): string | null {
    const name = f.name.toLowerCase();
    if (!name.endsWith(".pdf") && !name.endsWith(".docx")) return "Only PDF (.pdf) and Word (.docx) files are supported.";
    if (f.size === 0) return "That file is empty.";
    if (maxMb != null && f.size > maxMb * 1024 * 1024) {
      return `That file is ${(f.size / (1024 * 1024)).toFixed(1)} MB, which is over the ${maxMb} MB upload limit.`;
    }
    return null;
  }

  async function handleFile(f: File) {
    if (uploading) return;
    setDone(null);
    const problem = checkFile(f);
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setUploading(f.name);
    try {
      const created = await uploadContract(f);
      setDone(created);
      onUploaded(created);
    } catch (e: unknown) {
      setError(errorMessage(e, "Upload failed."));
    } finally {
      setUploading(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const hintId = `${uid}-hint`;

  return (
    <section
      aria-label="Upload a contract"
      onDragOver={(e) => {
        e.preventDefault();
        if (!uploading) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        const f = e.dataTransfer.files?.[0];
        if (f) void handleFile(f);
      }}
      className={`p-4 rounded-2xl border-2 border-dashed bg-white flex flex-col sm:flex-row sm:items-center gap-3 ${
        dragging ? "border-blue-500 bg-blue-50" : "border-slate-300"
      }`}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-slate-900">Upload a contract</p>
        <p id={hintId} className="text-xs text-slate-600">
          PDF (.pdf) or Word (.docx){maxMb != null ? `, up to ${maxMb} MB` : ""}. Drop a file here or choose one. Scanned
          documents take longer.
        </p>
      </div>
      <input
        ref={fileRef}
        id={`${uid}-file`}
        type="file"
        accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        aria-label="Contract file (PDF or Word)"
        aria-describedby={hintId}
        disabled={uploading !== null}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void handleFile(f);
        }}
        className="sr-only"
      />
      <button
        type="button"
        onClick={() => fileRef.current?.click()}
        disabled={uploading !== null}
        aria-describedby={hintId}
        className={`inline-flex items-center justify-center gap-1.5 px-4 py-2 text-xs font-bold rounded-xl bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 shrink-0 ${focusRing}`}
      >
        {uploading ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" />
        ) : (
          <Upload className="w-3.5 h-3.5" aria-hidden="true" />
        )}
        {uploading ? "Uploading..." : "Upload contract"}
      </button>

      <div className="sm:basis-full space-y-2 empty:hidden">
        {uploading && (
          <p role="status" className="flex items-center gap-2 text-xs text-blue-900 bg-blue-50 border border-blue-200 rounded-lg px-3 py-2">
            <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" aria-hidden="true" />
            <span className="break-all">Uploading {uploading}...</span>
          </p>
        )}
        {error && (
          <p role="alert" className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </p>
        )}
        {done && (
          <p role="status" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-green-900 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
            <CheckCircle2 className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            <span>Uploaded. Processing has started.</span>
            {hideOpenLink ? (
              <span className="font-semibold break-all">{done.title}</span>
            ) : (
              <Link href={`/contracts/${done.id}`} className={`font-bold underline text-green-900 hover:text-green-950 ${focusRing}`}>
                Open {done.title}
              </Link>
            )}
          </p>
        )}
      </div>
    </section>
  );
}
