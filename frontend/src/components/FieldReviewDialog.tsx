"use client";

import { useId, useRef, useState } from "react";
import { CheckCircle2, Undo2, UserCheck } from "lucide-react";
import { StatusChip } from "@/components/ui/StatusChip";
import { SourceRef } from "@/components/ui/SourceRef";
import { errorMessage, reviewField } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { trapTabKey, useDialogFocus } from "@/lib/useDialogFocus";
import type { ExtractedField, FieldReviewRequest, UserReviewAction } from "@/lib/types";

type Kind = "date" | "period" | "bool" | "text";
type Choice = "confirm" | "correct" | "not_in_contract";

const KINDS: Record<string, Kind> = {
  effective_date: "date",
  expiration_date: "date",
  renewal_notice_period: "period",
  termination_notice_period: "period",
  auto_renew: "bool",
  renewal_terms: "text",
  payment_terms: "text",
  termination_conditions: "text",
};

const KIND_HINT: Record<Kind, string> = {
  date: 'A date such as 2027-03-31 or "March 31, 2027". A date like 03/04/2027 is rejected because it could be read two ways.',
  period: 'A length of time such as "30 days", "4 weeks", "3 months" or "1 year".',
  bool: "Does the contract renew automatically?",
  text: "Write it the way the contract states it (up to 2000 characters).",
};

const MAX_TEXT = 2000;

const ACTION_PHRASE: Record<UserReviewAction, string> = {
  confirmed: "confirmed",
  corrected: "corrected",
  not_in_contract: "marked as not in the contract",
};

/** Small chip for the field list and the dialog. */
export function ReviewedByYouChip({ action }: { action: UserReviewAction }) {
  return (
    <span
      title="You reviewed this value yourself. The original AI value is kept and can be restored."
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap bg-blue-50 text-blue-800 border-blue-200"
    >
      <UserCheck className="w-3 h-3 shrink-0" aria-hidden="true" />
      Reviewed by you: {action === "not_in_contract" ? "not in contract" : action}
    </span>
  );
}

function hasCurrentValue(field: ExtractedField): boolean {
  return (field.status === "verified" || field.status === "needs_review") && Boolean(field.display_value);
}

function initialInput(field: ExtractedField, kind: Kind): string {
  if (kind === "bool") {
    const v = (field.display_value ?? "").trim().toLowerCase();
    if (/^(yes|true)/.test(v)) return "yes";
    if (/^(no|false)/.test(v)) return "no";
    return "";
  }
  if (kind === "date") {
    const iso = field.value && typeof field.value.date === "string" ? field.value.date : null;
    return iso ?? field.display_value ?? "";
  }
  return field.display_value ?? "";
}

interface FieldReviewDialogProps {
  contractId: string;
  versionId?: string | null;
  field: ExtractedField;
  /** Human label such as "Effective Date". */
  label: string;
  onClose: () => void;
  /** Called after the server accepted the review. The page should reload the contract and announce `message`. */
  onSaved: (message: string) => void;
}

const focusRing =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";
const inputClass = `block w-full px-3 py-2 rounded-lg border border-slate-300 bg-white text-sm text-slate-900 ${focusRing}`;

export function FieldReviewDialog({ contractId, versionId, field, label, onClose, onSaved }: FieldReviewDialogProps) {
  const uid = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const firstRef = useRef<HTMLInputElement | null>(null);
  const kind: Kind = KINDS[field.field_key] ?? "text";
  const canConfirm = hasCurrentValue(field);
  const review = field.notes?.user_review ?? null;
  const original = review?.original ?? null;
  const history = review?.history ?? [];

  const [choice, setChoice] = useState<Choice>(canConfirm ? "confirm" : "correct");
  const [value, setValue] = useState(() => initialInput(field, kind));
  const [quote, setQuote] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"save" | "revert" | null>(null);
  const [error, setError] = useState<string | null>(null);

  useDialogFocus(firstRef);

  const trimmed = value.trim();

  async function submit(body: FieldReviewRequest, message: string, which: "save" | "revert") {
    if (busy) return;
    setBusy(which);
    setError(null);
    try {
      await reviewField(contractId, field.id, body);
      onSaved(message);
    } catch (e: unknown) {
      setError(errorMessage(e, "Could not save your review. Please try again."));
      setBusy(null);
    }
  }

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (choice === "correct" && trimmed === "") {
      setError("Enter the correct value, or choose one of the other options.");
      return;
    }
    const noteText = note.trim();
    const withNote: Pick<FieldReviewRequest, "note"> = noteText ? { note: noteText } : {};
    if (choice === "confirm") {
      void submit({ action: "confirm", ...withNote }, `${label} confirmed.`, "save");
    } else if (choice === "not_in_contract") {
      void submit({ action: "not_in_contract", ...withNote }, `${label} marked as not stated in the contract.`, "save");
    } else {
      const quoteText = quote.trim();
      const body: FieldReviewRequest = {
        action: "correct",
        value: kind === "bool" ? trimmed === "yes" : trimmed,
        ...(quoteText ? { quote: quoteText } : {}),
        ...withNote,
      };
      void submit(body, `${label} corrected.`, "save");
    }
  }

  function handleRevert() {
    void submit({ action: "revert" }, `${label} restored to the original AI value.`, "revert");
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      if (!busy) onClose();
      return;
    }
    trapTabKey(e, dialogRef.current);
  }

  const titleId = `${uid}-title`;
  const descId = `${uid}-desc`;
  const saveLabel = choice === "confirm" ? "Confirm this value" : choice === "correct" ? "Save correction" : "Save";

  const options: { value: Choice; title: string; help: string; hidden?: boolean }[] = [
    { value: "confirm", title: "This is right", help: "The value above matches the contract.", hidden: !canConfirm },
    { value: "correct", title: "Change it", help: "Enter what the contract actually says." },
    {
      value: "not_in_contract",
      title: "The contract doesn't say this",
      help: "The contract does not state this at all.",
    },
  ];

  const currentText = field.status === "not_found" ? "Not found in this document" : (field.display_value ?? "No value");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        aria-busy={busy !== null}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-slate-200 focus:outline-none"
      >
        <form onSubmit={handleSave} noValidate className="p-5 space-y-4">
          <div className="space-y-1">
            <h2 id={titleId} className="text-base font-bold text-slate-900 break-words">
              Review: {label}
            </h2>
            <p id={descId} className="text-xs text-slate-600">
              This value was read from the document by AI, so it can be wrong. Your review is recorded on this field and
              the original AI value is kept. AI-assisted, not legal advice.
            </p>
          </div>

          {/* Current value */}
          <div className="space-y-2 rounded-xl border border-slate-200 bg-slate-50 p-3">
            <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Current value</p>
            <p className="text-sm font-semibold text-slate-900 break-words whitespace-pre-wrap">{currentText}</p>
            <div className="flex flex-wrap items-center gap-2">
              <StatusChip status={field.status} />
              {review && <ReviewedByYouChip action={review.action} />}
            </div>
            {hasCurrentValue(field) && (
              <SourceRef
                contractId={contractId}
                versionId={versionId}
                page={field.page}
                section={field.section}
                quote={field.source_quote}
              />
            )}
          </div>

          {/* Existing review */}
          {review && (
            <div className="space-y-2 rounded-xl border border-blue-200 bg-blue-50/60 p-3 text-xs text-slate-800">
              <p className="font-semibold">
                You {ACTION_PHRASE[review.action]} this on {formatDate(review.at, "long") ?? review.at}.
              </p>
              {review.note && <p className="break-words">Your note: {review.note}</p>}
              {original && (
                <div className="space-y-1">
                  <p className="text-[10px] font-bold uppercase tracking-wider text-slate-500">Original AI value</p>
                  <p className="text-sm font-semibold break-words whitespace-pre-wrap">
                    {original.status === "not_found" ? "Not found in this document" : (original.display_value ?? "No value")}
                  </p>
                  <StatusChip status={original.status} />
                  {original.page != null && (
                    <SourceRef
                      contractId={contractId}
                      versionId={versionId}
                      page={original.page}
                      section={original.section}
                      quote={original.source_quote}
                    />
                  )}
                </div>
              )}
              {history.length > 1 && (
                <details>
                  <summary className={`cursor-pointer select-none font-semibold text-slate-600 ${focusRing}`}>
                    History ({history.length})
                  </summary>
                  <ul className="mt-1 space-y-0.5 list-disc pl-4 text-slate-600">
                    {history.map((h, i) => (
                      <li key={i} className="break-words">
                        {formatDate(h.at, "short") ?? h.at}: {h.action}
                        {h.from || h.to ? ` (${h.from ?? "none"} to ${h.to ?? "none"})` : ""}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {original && (
                <button
                  type="button"
                  onClick={handleRevert}
                  disabled={busy !== null}
                  className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-60 ${focusRing}`}
                >
                  <Undo2 className="w-3.5 h-3.5" aria-hidden="true" />
                  {busy === "revert" ? "Restoring..." : "Revert to the original AI value"}
                </button>
              )}
            </div>
          )}

          {/* Choices */}
          <fieldset className="space-y-2" disabled={busy !== null}>
            <legend className="text-xs font-bold uppercase tracking-wider text-slate-500 mb-1">
              {review ? "Change your review" : "What is right?"}
            </legend>
            {options
              .filter((o) => !o.hidden)
              .map((o) => {
                const selected = choice === o.value;
                return (
                  <label
                    key={o.value}
                    className={`flex items-start gap-3 rounded-xl border p-3 cursor-pointer has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-blue-600 ${
                      selected ? "border-blue-500 bg-blue-50" : "border-slate-200 bg-white hover:bg-slate-50"
                    }`}
                  >
                    <input
                      ref={selected ? firstRef : undefined}
                      type="radio"
                      name={`${uid}-choice`}
                      value={o.value}
                      checked={selected}
                      onChange={() => {
                        setChoice(o.value);
                        setError(null);
                      }}
                      className="mt-0.5 h-4 w-4 shrink-0"
                    />
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-slate-900">{o.title}</span>
                      <span className="block text-xs text-slate-600">{o.help}</span>
                    </span>
                  </label>
                );
              })}
          </fieldset>

          {choice === "correct" && (
            <div className="space-y-3">
              <div className="space-y-1">
                {kind === "bool" ? (
                  <fieldset className="space-y-1">
                    <legend className="text-xs font-semibold text-slate-700">Correct value</legend>
                    <div className="flex flex-wrap gap-4">
                      {(["yes", "no"] as const).map((v) => (
                        <label key={v} className="inline-flex items-center gap-2 text-sm text-slate-800">
                          <input
                            type="radio"
                            name={`${uid}-bool`}
                            value={v}
                            checked={value === v}
                            onChange={() => {
                              setValue(v);
                              setError(null);
                            }}
                            className="h-4 w-4"
                          />
                          {v === "yes" ? "Yes, it renews automatically" : "No"}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ) : (
                  <>
                    <label htmlFor={`${uid}-value`} className="text-xs font-semibold text-slate-700">
                      Correct value
                    </label>
                    {kind === "text" ? (
                      <textarea
                        id={`${uid}-value`}
                        rows={4}
                        maxLength={MAX_TEXT}
                        value={value}
                        onChange={(e) => {
                          setValue(e.target.value);
                          setError(null);
                        }}
                        aria-describedby={`${uid}-value-hint`}
                        className={inputClass}
                      />
                    ) : (
                      <input
                        id={`${uid}-value`}
                        type="text"
                        value={value}
                        onChange={(e) => {
                          setValue(e.target.value);
                          setError(null);
                        }}
                        placeholder={kind === "date" ? "2027-03-31" : "30 days"}
                        aria-describedby={`${uid}-value-hint`}
                        className={inputClass}
                      />
                    )}
                  </>
                )}
                <p id={`${uid}-value-hint`} className="text-xs text-slate-500">
                  {KIND_HINT[kind]}
                </p>
              </div>

              <div className="space-y-1">
                <label htmlFor={`${uid}-quote`} className="text-xs font-semibold text-slate-700">
                  Quote from the document (optional)
                </label>
                <textarea
                  id={`${uid}-quote`}
                  rows={3}
                  value={quote}
                  onChange={(e) => setQuote(e.target.value)}
                  aria-describedby={`${uid}-quote-hint`}
                  className={inputClass}
                />
                <p id={`${uid}-quote-hint`} className="text-xs text-slate-600">
                  Paste the exact words from the contract that show this. If they are found in the document and contain
                  the value, the field becomes Verified. Without a quote from the document this stays marked
                  &lsquo;needs review&rsquo; because nothing in the document has been shown to say it.
                </p>
              </div>
            </div>
          )}

          <div className="space-y-1">
            <label htmlFor={`${uid}-note`} className="text-xs font-semibold text-slate-700">
              Note (optional)
            </label>
            <input
              id={`${uid}-note`}
              type="text"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className={inputClass}
            />
          </div>

          {error && (
            <p role="alert" className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2 break-words">
              {error}
            </p>
          )}

          <div className="flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              disabled={busy !== null}
              className={`px-4 py-2 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50 ${focusRing}`}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy !== null}
              className={`inline-flex items-center justify-center gap-1.5 px-4 py-2 text-xs font-bold rounded-xl text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60 ${focusRing}`}
            >
              <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />
              {busy === "save" ? "Saving..." : saveLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
