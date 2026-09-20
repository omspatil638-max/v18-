"use client";

import { useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { errorMessage, updateContract } from "@/lib/api";
import { trapTabKey, useDialogFocus } from "@/lib/useDialogFocus";
import type { ContractSummary, ContractUpdate } from "@/lib/types";

export const CONTRACT_TYPE_SUGGESTIONS = ["Service agreement", "NDA", "Lease", "Employment", "Vendor", "Customer", "Other"];
export const MAX_TAGS = 10;
export const MAX_TAG_LENGTH = 30;
const MAX_TYPE_LENGTH = 40;

interface ContractDetailsDialogProps {
  contract: Pick<ContractSummary, "id" | "title" | "counterparty" | "contract_type" | "tags">;
  /** Extra type suggestions, for example the types already used in the library. */
  typeSuggestions?: string[];
  onClose: () => void;
  onSaved: (updated: ContractSummary) => void;
}

const focusRing =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";
const inputClass = `block w-full px-3 py-2 rounded-lg border border-slate-300 bg-white text-sm text-slate-900 ${focusRing}`;

function sameList(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

export function ContractDetailsDialog({ contract, typeSuggestions = [], onClose, onSaved }: ContractDetailsDialogProps) {
  const uid = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const titleRef = useRef<HTMLInputElement | null>(null);
  const [title, setTitle] = useState(contract.title);
  const [counterparty, setCounterparty] = useState(contract.counterparty ?? "");
  const [type, setType] = useState(contract.contract_type ?? "");
  const [tags, setTags] = useState<string[]>(contract.tags ?? []);
  const [tagInput, setTagInput] = useState("");
  const [tagError, setTagError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useDialogFocus(titleRef);

  const suggestions = Array.from(new Set([...CONTRACT_TYPE_SUGGESTIONS, ...typeSuggestions]));

  /** Adds one tag from `raw`. Returns the new list, or null (with a message) when it is not acceptable. */
  function withTag(current: string[], raw: string): string[] | null {
    const t = raw.trim().replace(/\s+/g, " ");
    if (!t) return current;
    if (t.length > MAX_TAG_LENGTH) {
      setTagError(`A tag can be at most ${MAX_TAG_LENGTH} characters.`);
      return null;
    }
    if (current.some((x) => x.toLowerCase() === t.toLowerCase())) {
      setTagError(`"${t}" is already added.`);
      return null;
    }
    if (current.length >= MAX_TAGS) {
      setTagError(`You can add at most ${MAX_TAGS} tags.`);
      return null;
    }
    setTagError(null);
    return [...current, t];
  }

  function commitTagInput(): boolean {
    const next = withTag(tags, tagInput);
    if (next === null) return false;
    setTags(next);
    setTagInput("");
    return true;
  }

  function handleTagKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commitTagInput();
    }
  }

  function handleTagChange(e: React.ChangeEvent<HTMLInputElement>) {
    const v = e.target.value;
    // A pasted "a, b, c" becomes three tags.
    if (v.includes(",")) {
      let next = tags;
      const parts = v.split(",");
      const rest = parts.pop() ?? "";
      for (const part of parts) {
        const added = withTag(next, part);
        if (added === null) {
          setTags(next);
          setTagInput(v);
          return;
        }
        next = added;
      }
      setTags(next);
      setTagInput(rest);
      return;
    }
    setTagInput(v);
    setTagError(null);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    const newTitle = title.trim();
    if (!newTitle) {
      setError("The title cannot be empty.");
      return;
    }
    let finalTags = tags;
    if (tagInput.trim()) {
      const next = withTag(tags, tagInput);
      if (next === null) return;
      finalTags = next;
    }
    const update: ContractUpdate = {};
    if (newTitle !== contract.title) update.title = newTitle;
    const newParty = counterparty.trim();
    if (newParty !== (contract.counterparty ?? "")) update.counterparty = newParty === "" ? null : newParty;
    const newType = type.trim();
    if (newType !== (contract.contract_type ?? "")) update.contract_type = newType;
    if (!sameList(finalTags, contract.tags ?? [])) update.tags = finalTags;
    if (Object.keys(update).length === 0) {
      onClose();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onSaved(await updateContract(contract.id, update));
    } catch (err: unknown) {
      setError(errorMessage(err, "Could not save the details. Please try again."));
      setBusy(false);
    }
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
        aria-busy={busy}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-slate-200 focus:outline-none"
      >
        <form onSubmit={handleSubmit} noValidate className="p-5 space-y-4">
          <h2 id={titleId} className="text-base font-bold text-slate-900">
            Edit details
          </h2>

          <div className="space-y-1">
            <label htmlFor={`${uid}-title-input`} className="text-xs font-semibold text-slate-700">
              Title
            </label>
            <input
              ref={titleRef}
              id={`${uid}-title-input`}
              type="text"
              value={title}
              maxLength={200}
              onChange={(e) => {
                setTitle(e.target.value);
                setError(null);
              }}
              className={inputClass}
            />
          </div>

          <div className="space-y-1">
            <label htmlFor={`${uid}-party`} className="text-xs font-semibold text-slate-700">
              Counterparty
            </label>
            <input
              id={`${uid}-party`}
              type="text"
              value={counterparty}
              maxLength={200}
              onChange={(e) => setCounterparty(e.target.value)}
              className={inputClass}
            />
          </div>

          <div className="space-y-1">
            <label htmlFor={`${uid}-type`} className="text-xs font-semibold text-slate-700">
              Type
            </label>
            <input
              id={`${uid}-type`}
              type="text"
              list={`${uid}-types`}
              value={type}
              maxLength={MAX_TYPE_LENGTH}
              onChange={(e) => setType(e.target.value)}
              placeholder="For example NDA"
              className={inputClass}
            />
            <datalist id={`${uid}-types`}>
              {suggestions.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
            <p className="text-xs text-slate-500">Your own label. Leave empty for none.</p>
          </div>

          <div className="space-y-2">
            <label htmlFor={`${uid}-tag`} className="text-xs font-semibold text-slate-700">
              Tags
            </label>
            {tags.length > 0 && (
              <ul className="flex flex-wrap gap-1.5" aria-label="Current tags">
                {tags.map((t) => (
                  <li
                    key={t}
                    className="inline-flex items-center gap-1 pl-2.5 pr-1 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-800 border border-slate-200 max-w-full"
                  >
                    <span className="break-all">{t}</span>
                    <button
                      type="button"
                      onClick={() => setTags((prev) => prev.filter((x) => x !== t))}
                      aria-label={`Remove tag ${t}`}
                      className={`p-1 rounded-full text-slate-500 hover:text-slate-900 hover:bg-slate-200 ${focusRing}`}
                    >
                      <X className="w-3 h-3" aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <input
              id={`${uid}-tag`}
              type="text"
              value={tagInput}
              onChange={handleTagChange}
              onKeyDown={handleTagKeyDown}
              disabled={tags.length >= MAX_TAGS}
              aria-describedby={`${uid}-tag-hint`}
              aria-invalid={tagError ? true : undefined}
              placeholder={tags.length >= MAX_TAGS ? "Tag limit reached" : "Type a tag, press Enter or comma"}
              className={inputClass}
            />
            <p id={`${uid}-tag-hint`} className="text-xs text-slate-500">
              Up to {MAX_TAGS} tags, {MAX_TAG_LENGTH} characters each. {tags.length} of {MAX_TAGS} used.
            </p>
            {tagError && (
              <p role="alert" className="text-xs text-red-700">
                {tagError}
              </p>
            )}
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
              disabled={busy}
              className={`px-4 py-2 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50 ${focusRing}`}
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy}
              className={`px-4 py-2 text-xs font-bold rounded-xl text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60 ${focusRing}`}
            >
              {busy ? "Saving..." : "Save"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
