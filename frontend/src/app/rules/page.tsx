"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { CheckCircle2, Info, ListChecks, Pencil, Plus, Trash2 } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { SeverityBadge } from "@/components/ui/FlagBadge";
import { createPolicyRule, deletePolicyRule, errorMessage, fetchPolicyRules, updatePolicyRule } from "@/lib/api";
import { trapTabKey, useDialogFocus } from "@/lib/useDialogFocus";
import type {
  FlagSeverity,
  PolicyCatalogItem,
  PolicyOperator,
  PolicyRule,
  PolicyRuleBody,
  PolicyRulesResponse,
} from "@/lib/types";

const MAX_MESSAGE = 500;
const MIN_DAYS = 0;
const MAX_DAYS = 3650;
const SEVERITIES: FlagSeverity[] = ["HIGH", "MEDIUM", "LOW"];

const focusRing =
  "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600";
const inputClass = `block w-full px-3 py-2 rounded-lg border border-slate-300 bg-white text-sm text-slate-900 ${focusRing}`;

interface RuleDraft {
  name: string;
  field: string;
  operator: PolicyOperator;
  value: number | null;
  target: string | null;
  severity: FlagSeverity;
  message: string | null;
  enabled: boolean;
}

interface Preset {
  label: string;
  draft: Omit<RuleDraft, "message" | "enabled" | "name">;
}

const PRESETS: Preset[] = [
  {
    label: "Termination notice longer than 60 days",
    draft: { field: "termination_notice_days", operator: "gt", value: 60, target: null, severity: "MEDIUM" },
  },
  {
    label: "Payment terms longer than 45 days",
    draft: { field: "payment_days", operator: "gt", value: 45, target: null, severity: "LOW" },
  },
  {
    label: "Renewal notice longer than 90 days",
    draft: { field: "renewal_notice_days", operator: "gt", value: 90, target: null, severity: "MEDIUM" },
  },
  {
    label: "Any auto-renewal",
    draft: { field: "auto_renew", operator: "is_true", value: null, target: null, severity: "MEDIUM" },
  },
  {
    label: "No renewal notice period found",
    draft: {
      field: "missing_field",
      operator: "missing",
      value: null,
      target: "renewal_notice_period",
      severity: "HIGH",
    },
  },
  {
    label: "No expiry date found",
    draft: { field: "missing_field", operator: "missing", value: null, target: "expiration_date", severity: "HIGH" },
  },
];

type DialogState = { rule: PolicyRule | null; preset: Preset | null };

/** "Termination notice period is more than 60 days" / "Auto-renewal is on" / "Renewal notice period is missing". */
function ruleSentence(
  r: { field: string; operator: string; value: number | null; target: string | null },
  data: PolicyRulesResponse,
): string {
  const item = data.catalog.find((c) => c.key === r.field);
  const op = data.operator_labels[r.operator] ?? r.operator;
  if (!item) return `${r.field} ${op}`;
  if (item.kind === "missing") {
    const targetLabel = r.target ? (data.missing_targets[r.target] ?? r.target) : "A value";
    return `${targetLabel} ${op}`;
  }
  if (item.kind === "bool") return `${item.label} ${op}`;
  if (r.value == null) return `${item.label} ${op}`;
  return `${item.label} ${op} ${r.value}${item.unit ? ` ${item.unit}` : ""}`;
}

function toBody(d: RuleDraft): PolicyRuleBody {
  return {
    name: d.name,
    field: d.field,
    operator: d.operator,
    value: d.value,
    target: d.target,
    severity: d.severity,
    message: d.message,
    enabled: d.enabled,
  };
}

function SeverityRadio({
  name,
  value,
  onChange,
}: {
  name: string;
  value: FlagSeverity;
  onChange: (s: FlagSeverity) => void;
}) {
  return (
    <fieldset className="space-y-1">
      <legend className="text-xs font-semibold text-slate-700">Severity</legend>
      <div className="flex flex-wrap gap-2">
        {SEVERITIES.map((s) => (
          <label
            key={s}
            className={`inline-flex items-center gap-2 rounded-xl border px-3 py-2 cursor-pointer has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-blue-600 ${
              value === s ? "border-blue-500 bg-blue-50" : "border-slate-200 bg-white hover:bg-slate-50"
            }`}
          >
            <input
              type="radio"
              name={name}
              value={s}
              checked={value === s}
              onChange={() => onChange(s)}
              className="h-4 w-4"
            />
            <SeverityBadge severity={s} />
          </label>
        ))}
      </div>
    </fieldset>
  );
}

interface RuleDialogProps {
  data: PolicyRulesResponse;
  state: DialogState;
  onClose: () => void;
  onSaved: (res: PolicyRulesResponse) => void;
}

function RuleDialog({ data, state, onClose, onSaved }: RuleDialogProps) {
  const uid = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const nameRef = useRef<HTMLInputElement | null>(null);
  const { rule, preset } = state;
  const firstMissingTarget = Object.keys(data.missing_targets)[0] ?? null;

  const [draft, setDraft] = useState<RuleDraft>(() => {
    if (rule) {
      return {
        name: rule.name,
        field: rule.field,
        operator: rule.operator,
        value: rule.value,
        target: rule.target,
        severity: rule.severity,
        message: rule.message,
        enabled: rule.enabled,
      };
    }
    if (preset) return { ...preset.draft, name: preset.label, message: null, enabled: true };
    const first = data.catalog[0];
    return {
      name: "",
      field: first?.key ?? "",
      operator: first?.operators[0] ?? "gt",
      value: null,
      target: first?.kind === "missing" ? firstMissingTarget : null,
      severity: "MEDIUM",
      message: null,
      enabled: true,
    };
  });
  const [valueText, setValueText] = useState(() => (draft.value == null ? "" : String(draft.value)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useDialogFocus(nameRef);

  const item: PolicyCatalogItem | undefined = data.catalog.find((c) => c.key === draft.field);
  const kind = item?.kind ?? "number";

  function patch(p: Partial<RuleDraft>) {
    setDraft((d) => ({ ...d, ...p }));
    setError(null);
  }

  function changeField(key: string) {
    const next = data.catalog.find((c) => c.key === key);
    if (!next) return;
    patch({
      field: key,
      operator: next.operators[0] ?? draft.operator,
      value: null,
      target: next.kind === "missing" ? (draft.target ?? firstMissingTarget) : null,
    });
    if (next.kind !== "number") setValueText("");
  }

  function parsedValue(): number | null {
    const t = valueText.trim();
    if (t === "") return null;
    const n = Number(t);
    return Number.isInteger(n) ? n : null;
  }

  const current: RuleDraft = { ...draft, value: kind === "number" ? parsedValue() : null };
  const preview = item ? ruleSentence(current, data) : "";
  const showValue = kind === "number";
  const previewText = showValue && current.value == null ? `${item?.label ?? ""} ${data.operator_labels[draft.operator] ?? ""} ...` : preview;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    const name = draft.name.trim();
    if (!name) {
      setError("Give the rule a name.");
      return;
    }
    if (kind === "number") {
      const v = parsedValue();
      if (v == null || v < MIN_DAYS || v > MAX_DAYS) {
        setError(`Enter a whole number of ${item?.unit ?? "days"} from ${MIN_DAYS} to ${MAX_DAYS}.`);
        return;
      }
    }
    if (kind === "missing" && !draft.target) {
      setError("Choose which value to check.");
      return;
    }
    const message = (draft.message ?? "").trim();
    const body = toBody({
      ...draft,
      name,
      value: kind === "number" ? parsedValue() : null,
      target: kind === "missing" ? draft.target : null,
      message: message || null,
    });
    setBusy(true);
    setError(null);
    try {
      const res = rule ? await updatePolicyRule(rule.id, body) : await createPolicyRule(body);
      onSaved(res);
    } catch (err: unknown) {
      setError(errorMessage(err, "Could not save the rule. Please try again."));
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
  const descId = `${uid}-desc`;
  const messageLength = (draft.message ?? "").length;

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
        aria-busy={busy}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-slate-200 focus:outline-none"
      >
        <form onSubmit={handleSubmit} noValidate className="p-5 space-y-4">
          <div className="space-y-1">
            <h2 id={titleId} className="text-base font-bold text-slate-900">
              {rule ? "Edit rule" : "Add a rule"}
            </h2>
            <p id={descId} className="text-xs text-slate-600">
              When you save, all your analysed contracts are checked against your rules again. A rule only checks values
              the app has found; an unknown value never triggers it.
            </p>
          </div>

          <div className="space-y-1">
            <label htmlFor={`${uid}-name`} className="text-xs font-semibold text-slate-700">
              Rule name
            </label>
            <input
              ref={nameRef}
              id={`${uid}-name`}
              type="text"
              value={draft.name}
              onChange={(e) => patch({ name: e.target.value })}
              autoComplete="off"
              className={inputClass}
            />
          </div>

          <div className="space-y-1">
            <label htmlFor={`${uid}-field`} className="text-xs font-semibold text-slate-700">
              What to check
            </label>
            <select
              id={`${uid}-field`}
              value={draft.field}
              onChange={(e) => changeField(e.target.value)}
              className={inputClass}
            >
              {data.catalog.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          {kind === "missing" ? (
            <div className="space-y-1">
              <label htmlFor={`${uid}-target`} className="text-xs font-semibold text-slate-700">
                Which value
              </label>
              <select
                id={`${uid}-target`}
                value={draft.target ?? ""}
                onChange={(e) => patch({ target: e.target.value })}
                className={inputClass}
              >
                {Object.entries(data.missing_targets).map(([key, label]) => (
                  <option key={key} value={key}>
                    {label}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <label htmlFor={`${uid}-op`} className="text-xs font-semibold text-slate-700">
                  Condition
                </label>
                <select
                  id={`${uid}-op`}
                  value={draft.operator}
                  onChange={(e) => patch({ operator: e.target.value as PolicyOperator })}
                  className={inputClass}
                >
                  {(item?.operators ?? []).map((op) => (
                    <option key={op} value={op}>
                      {data.operator_labels[op] ?? op}
                    </option>
                  ))}
                </select>
              </div>
              {showValue && (
                <div className="space-y-1">
                  <label htmlFor={`${uid}-value`} className="text-xs font-semibold text-slate-700">
                    Number of {item?.unit ?? "days"}
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      id={`${uid}-value`}
                      type="number"
                      inputMode="numeric"
                      min={MIN_DAYS}
                      max={MAX_DAYS}
                      step={1}
                      value={valueText}
                      onChange={(e) => {
                        setValueText(e.target.value);
                        setError(null);
                      }}
                      aria-describedby={`${uid}-value-hint`}
                      className={inputClass}
                    />
                    {item?.unit && <span className="text-sm text-slate-700">{item.unit}</span>}
                  </div>
                  <p id={`${uid}-value-hint`} className="text-xs text-slate-500">
                    From {MIN_DAYS} to {MAX_DAYS}.
                  </p>
                </div>
              )}
            </div>
          )}

          <p className="text-xs rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-slate-800 break-words">
            <span className="font-bold uppercase tracking-wider text-[10px] text-slate-500 block">This rule flags a contract when</span>
            <span className="text-sm font-semibold">{previewText || "..."}</span>
          </p>

          <SeverityRadio name={`${uid}-sev`} value={draft.severity} onChange={(s) => patch({ severity: s })} />

          <div className="space-y-1">
            <label htmlFor={`${uid}-message`} className="text-xs font-semibold text-slate-700">
              Text shown in the flag (optional)
            </label>
            <textarea
              id={`${uid}-message`}
              rows={3}
              maxLength={MAX_MESSAGE}
              value={draft.message ?? ""}
              onChange={(e) => patch({ message: e.target.value })}
              aria-describedby={`${uid}-message-hint`}
              className={inputClass}
            />
            <p id={`${uid}-message-hint`} className="text-xs text-slate-500">
              {messageLength} of {MAX_MESSAGE} characters. If left empty, the flag shows the rule name and the actual value.
            </p>
          </div>

          <label className="flex items-center gap-2 text-sm text-slate-800">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => patch({ enabled: e.target.checked })}
              className="h-4 w-4"
            />
            Rule is on
          </label>

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
              className={`inline-flex items-center justify-center gap-1.5 px-4 py-2 text-xs font-bold rounded-xl text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-60 ${focusRing}`}
            >
              <CheckCircle2 className="w-3.5 h-3.5" aria-hidden="true" />
              {busy ? "Saving and re-checking..." : "Save rule"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function PresetChips({
  disabled,
  onPick,
}: {
  disabled: boolean;
  onPick: (p: Preset) => void;
}) {
  return (
    <ul className="flex flex-wrap gap-2">
      {PRESETS.map((p) => (
        <li key={p.label}>
          <button
            type="button"
            disabled={disabled}
            onClick={() => onPick(p)}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold bg-white border border-slate-300 text-slate-800 hover:bg-blue-50 hover:border-blue-300 disabled:opacity-50 disabled:hover:bg-white ${focusRing}`}
          >
            <Plus className="w-3 h-3 shrink-0" aria-hidden="true" />
            {p.label}
          </button>
        </li>
      ))}
    </ul>
  );
}

function RuleCard({
  rule,
  data,
  toggling,
  onToggle,
  onEdit,
  onDelete,
}: {
  rule: PolicyRule;
  data: PolicyRulesResponse;
  toggling: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <li className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 sm:p-5 space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-bold text-slate-900 break-words min-w-0">{rule.name}</h3>
        <SeverityBadge severity={rule.severity} />
        {!rule.enabled && (
          <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-slate-100 text-slate-700 border border-slate-200 whitespace-nowrap">
            Off
          </span>
        )}
      </div>
      <p className="text-sm text-slate-800 break-words">Flags a contract when: {ruleSentence(rule, data)}</p>
      {rule.message && <p className="text-xs text-slate-600 break-words">Flag text: {rule.message}</p>}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button
          type="button"
          role="switch"
          aria-checked={rule.enabled}
          aria-label={`Rule is on: ${rule.name}`}
          disabled={toggling}
          onClick={onToggle}
          className={`inline-flex items-center gap-2 px-2 py-1 rounded-lg text-xs font-semibold text-slate-800 disabled:opacity-60 ${focusRing}`}
        >
          <span
            aria-hidden="true"
            className={`relative inline-block h-5 w-9 rounded-full transition-colors ${rule.enabled ? "bg-blue-600" : "bg-slate-400"}`}
          >
            <span
              className={`absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-all ${rule.enabled ? "left-[18px]" : "left-0.5"}`}
            />
          </span>
          {rule.enabled ? "On" : "Off"}
        </button>
        <button
          type="button"
          onClick={onEdit}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 ${focusRing}`}
        >
          <Pencil className="w-3.5 h-3.5" aria-hidden="true" />
          Edit
          <span className="sr-only"> {rule.name}</span>
        </button>
        <button
          type="button"
          onClick={onDelete}
          className={`inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-white border border-red-300 text-red-800 hover:bg-red-50 ${focusRing}`}
        >
          <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
          Delete
          <span className="sr-only"> {rule.name}</span>
        </button>
      </div>
    </li>
  );
}

export default function RulesPage() {
  const [data, setData] = useState<PolicyRulesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [toggling, setToggling] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<PolicyRule | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await fetchPolicyRules());
      setError(null);
    } catch (e: unknown) {
      setError(errorMessage(e, "Could not load your rules."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleToggle(rule: PolicyRule) {
    if (toggling) return;
    setToggling(rule.id);
    setActionError(null);
    setStatus("");
    try {
      const res = await updatePolicyRule(rule.id, {
        name: rule.name,
        field: rule.field,
        operator: rule.operator,
        value: rule.value,
        target: rule.target,
        severity: rule.severity,
        message: rule.message,
        enabled: !rule.enabled,
      });
      setData(res);
      setStatus(`Rule "${rule.name}" turned ${rule.enabled ? "off" : "on"}. Your contracts were re-checked.`);
    } catch (e: unknown) {
      setActionError(errorMessage(e, "Could not change the rule. Please try again."));
    } finally {
      setToggling(null);
    }
  }

  async function handleConfirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await deletePolicyRule(deleting.id);
      const name = deleting.name;
      setDeleting(null);
      setStatus(`Rule "${name}" deleted. Your contracts were re-checked.`);
      setActionError(null);
      try {
        setData(await fetchPolicyRules());
      } catch {
        // The delete worked; keep showing the list without the removed rule.
        setData((d) => (d ? { ...d, rules: d.rules.filter((r) => r.id !== deleting.id) } : d));
      }
    } catch (e: unknown) {
      setDeleteError(errorMessage(e, "Could not delete the rule. Please try again."));
    } finally {
      setDeleteBusy(false);
    }
  }

  function handleSaved(res: PolicyRulesResponse) {
    setData(res);
    setDialog(null);
    setActionError(null);
    setStatus("Rule saved. Your contracts were re-checked.");
  }

  const atLimit = data ? data.rules.length >= data.max_rules : false;
  const limitId = "rules-limit-note";

  return (
    <div>
      <PageHeader
        title="Your rules"
        subtitle="Your own standards, checked against every contract"
        breadcrumbs={[{ label: "Dashboard", href: "/" }, { label: "Rules" }]}
        actions={
          data ? (
            <button
              type="button"
              onClick={() => setDialog({ rule: null, preset: null })}
              disabled={atLimit}
              aria-describedby={atLimit ? limitId : undefined}
              className={`inline-flex items-center gap-1.5 px-4 py-2 text-xs font-bold rounded-xl text-white bg-blue-600 hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed ${focusRing}`}
            >
              <Plus className="w-3.5 h-3.5" aria-hidden="true" />
              Add a rule
            </button>
          ) : undefined
        }
      />

      <div className="p-4 sm:p-8 space-y-6 max-w-5xl mx-auto">
        <div className="space-y-3">
          <p className="text-sm text-slate-700">
            Set your own standards. When a contract breaks one, it appears in Needs review with the rule&apos;s name and
            the actual value. Rules only check values the app has found; an unknown value never triggers a rule.
          </p>
          <div role="note" className="flex items-start gap-2 text-xs text-slate-600 bg-slate-100 border border-slate-200 rounded-xl px-3 py-2">
            <Info className="w-4 h-4 shrink-0 mt-px text-slate-500" aria-hidden="true" />
            <p>
              <span className="font-semibold text-slate-700">AI-assisted, not legal advice.</span> Rules compare against
              values that were read from your documents by AI, so a value can be wrong. Check the source before relying
              on a flag.
            </p>
          </div>
        </div>

        <div role="status" className={status ? "text-sm font-semibold text-green-800 bg-green-50 border border-green-200 rounded-xl px-3 py-2 break-words" : "sr-only"}>
          {status}
        </div>

        {actionError && (
          <p role="alert" className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2 break-words">
            {actionError}
          </p>
        )}

        {loading && !data ? (
          <p role="status" className="p-8 text-center text-sm text-slate-500">
            Loading your rules...
          </p>
        ) : error && !data ? (
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
        ) : data ? (
          <>
            {atLimit && (
              <p id={limitId} className="text-xs text-slate-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                You have reached the limit of {data.max_rules} rules. Delete or edit a rule to add another.
              </p>
            )}

            {data.rules.length === 0 ? (
              <div className="p-6 sm:p-8 text-center bg-white border border-slate-200 rounded-2xl space-y-3">
                <ListChecks className="w-8 h-8 mx-auto text-slate-400" aria-hidden="true" />
                <p className="text-sm font-semibold text-slate-800">You have no rules yet.</p>
                <p className="text-xs text-slate-500 max-w-md mx-auto">
                  Start from an example below, or use Add a rule. You can change anything before saving.
                </p>
                <div className="flex justify-center">
                  <PresetChips disabled={atLimit} onPick={(p) => setDialog({ rule: null, preset: p })} />
                </div>
              </div>
            ) : (
              <>
                <section aria-labelledby="rules-heading" className="space-y-3">
                  <h2 id="rules-heading" className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                    Your rules ({data.rules.length} of {data.max_rules})
                  </h2>
                  <ul className="space-y-3">
                    {data.rules.map((r) => (
                      <RuleCard
                        key={r.id}
                        rule={r}
                        data={data}
                        toggling={toggling === r.id}
                        onToggle={() => void handleToggle(r)}
                        onEdit={() => setDialog({ rule: r, preset: null })}
                        onDelete={() => {
                          setDeleteError(null);
                          setDeleting(r);
                        }}
                      />
                    ))}
                  </ul>
                </section>
                {!atLimit && (
                  <section aria-labelledby="examples-heading" className="space-y-2">
                    <h2 id="examples-heading" className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                      Start from an example
                    </h2>
                    <PresetChips disabled={false} onPick={(p) => setDialog({ rule: null, preset: p })} />
                  </section>
                )}
              </>
            )}
          </>
        ) : null}
      </div>

      {dialog && data && (
        <RuleDialog data={data} state={dialog} onClose={() => setDialog(null)} onSaved={handleSaved} />
      )}

      {deleting && (
        <ConfirmDialog
          title="Delete this rule?"
          confirmLabel="Delete rule"
          busyLabel="Deleting..."
          busy={deleteBusy}
          error={deleteError}
          onConfirm={() => void handleConfirmDelete()}
          onCancel={() => {
            if (!deleteBusy) setDeleting(null);
          }}
        >
          <p>
            &ldquo;{deleting.name}&rdquo; will be removed and the flags it created will go away when your contracts are
            re-checked. Other flags are not affected.
          </p>
        </ConfirmDialog>
      )}
    </div>
  );
}
