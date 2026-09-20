"use client";

import { Fragment, useId, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, CheckCircle2, Info, MessageSquareText, MinusCircle, Search, UserCheck } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { TypeAndTagChips } from "@/components/ContractChips";
import { StatusChip } from "@/components/ui/StatusChip";
import { askContracts, errorMessage } from "@/lib/api";
import { locationLabel, sourceHref } from "@/lib/format";
import type { AskFact, AskInterpretedBy, AskMatch, AskPassage, AskResponse } from "@/lib/types";

const MAX_LEN = 500;

const DEFAULT_EXAMPLES = [
  "Which contracts expire in the next 90 days?",
  "Which contracts auto-renew?",
  "Which contracts expire this quarter and auto-renew?",
  "Which contracts have a notice period longer than 60 days?",
  "Which contracts have high-priority review flags?",
  "Which contracts mention arbitration?",
];

const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";

const INTERPRETED_NOTE: Record<AskInterpretedBy, string | null> = {
  rules: "understood by rules",
  ai: "understood with AI help (wording only)",
  keyword: "text search",
  none: null,
};

/** Plain-text snippet with «matched» markers, rendered as <mark> elements. Never HTML. */
function MarkedSnippet({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  const re = /«([^»]*)»/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(<Fragment key={`t${i}`}>{text.slice(last, m.index)}</Fragment>);
    parts.push(
      <mark key={`m${i}`} className="bg-yellow-200 text-slate-900 rounded-sm px-0.5">
        {m[1]}
      </mark>,
    );
    last = m.index + m[0].length;
    i += 1;
  }
  if (last < text.length) parts.push(<Fragment key="tail">{text.slice(last).replace(/[«»]/g, "")}</Fragment>);
  // A stray marker left by a cut-off snippet is dropped rather than shown.
  return <>{parts.length > 0 ? parts : text.replace(/[«»]/g, "")}</>;
}

const neutralChip =
  "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold border whitespace-nowrap";

function FactChip({ fact }: { fact: AskFact }) {
  if (fact.reviewed) {
    return (
      <span
        title="You checked this value against the document and confirmed or corrected it."
        className={`${neutralChip} bg-blue-50 text-blue-800 border-blue-200`}
      >
        <UserCheck className="w-3 h-3 shrink-0" aria-hidden="true" />
        Reviewed by you
      </span>
    );
  }
  switch (fact.status) {
    case "verified":
    case "needs_review":
      return <StatusChip status={fact.status} />;
    case "flag":
      return (
        <span title="An open review flag raised on this contract." className={`${neutralChip} bg-slate-100 text-slate-700 border-slate-200`}>
          <AlertTriangle className="w-3 h-3 shrink-0" aria-hidden="true" />
          Review flag
        </span>
      );
    case "user":
      return (
        <span title="Set by you, not extracted by AI." className={`${neutralChip} bg-slate-100 text-slate-700 border-slate-200`}>
          <UserCheck className="w-3 h-3 shrink-0" aria-hidden="true" />
          Set by you
        </span>
      );
    case "party":
      return (
        <span title="A party named in the contract." className={`${neutralChip} bg-slate-100 text-slate-700 border-slate-200`}>
          <CheckCircle2 className="w-3 h-3 shrink-0" aria-hidden="true" />
          Party
        </span>
      );
    case "not_found":
      return (
        <span title="This item was not found in the document text." className={`${neutralChip} bg-slate-100 text-slate-600 border-slate-200`}>
          <MinusCircle className="w-3 h-3 shrink-0" aria-hidden="true" />
          Not found in document
        </span>
      );
  }
}

function FactRow({ contractId, fact }: { contractId: string; fact: AskFact }) {
  const location = locationLabel(fact.page, null);
  return (
    <li className="py-2 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
      <span className="w-full sm:w-44 shrink-0 text-xs font-semibold text-slate-600 break-words">{fact.label}</span>
      <span className="font-semibold text-slate-900 break-words min-w-0">{fact.value}</span>
      <span className="flex flex-wrap items-center gap-2">
        <FactChip fact={fact} />
        {fact.page != null && location ? (
          <Link
            href={sourceHref(contractId, fact.page, fact.quote)}
            title={fact.quote ? `"${fact.quote}"` : undefined}
            className={`text-xs font-semibold text-blue-700 hover:text-blue-900 ${focusRing}`}
          >
            {location}
            <span className="sr-only"> in source</span>
          </Link>
        ) : (
          <span className="text-xs text-slate-500">No source page</span>
        )}
      </span>
      {fact.quote && (
        <span className="w-full text-xs text-slate-500 break-words italic">&ldquo;{fact.quote}&rdquo;</span>
      )}
    </li>
  );
}

function MatchCard({ match }: { match: AskMatch }) {
  return (
    <li className="p-4 sm:p-5 bg-white border border-slate-200 rounded-2xl shadow-sm space-y-2">
      <div className="space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            href={`/contracts/${match.contract_id}`}
            className={`text-sm font-bold text-slate-900 hover:text-blue-600 break-words ${focusRing}`}
          >
            {match.title}
          </Link>
          <TypeAndTagChips contractType={match.contract_type} tags={match.tags} />
        </div>
        {match.counterparty && <p className="text-xs text-slate-600 break-words">{match.counterparty}</p>}
      </div>
      {match.facts.length > 0 && (
        <ul className="divide-y divide-slate-100" aria-label={`Values that matched for ${match.title}`}>
          {match.facts.map((f, i) => (
            <FactRow key={`${f.label}-${i}`} contractId={match.contract_id} fact={f} />
          ))}
        </ul>
      )}
    </li>
  );
}

function PassageCard({ passage }: { passage: AskPassage }) {
  const location = locationLabel(passage.page, passage.section) ?? `Page ${passage.page}`;
  return (
    <li className="p-4 sm:p-5 bg-white border border-slate-200 rounded-2xl shadow-sm space-y-2">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link
          href={`/contracts/${passage.contract_id}`}
          className={`text-sm font-bold text-slate-900 hover:text-blue-600 break-words ${focusRing}`}
        >
          {passage.title}
        </Link>
        <span className="text-xs font-semibold text-slate-600">{location}</span>
      </div>
      <p className="text-sm text-slate-800 break-words leading-relaxed">
        <MarkedSnippet text={passage.snippet} />
      </p>
      <Link
        href={sourceHref(passage.contract_id, passage.page, passage.quote)}
        className={`inline-block text-xs font-semibold text-blue-700 hover:text-blue-900 ${focusRing}`}
      >
        Open in source
        <span className="sr-only"> ({passage.title}, page {passage.page})</span>
      </Link>
    </li>
  );
}

function ExampleChips({ examples, onPick, disabled }: { examples: string[]; onPick: (q: string) => void; disabled: boolean }) {
  return (
    <ul className="flex flex-wrap gap-2" aria-label="Example questions">
      {examples.map((ex) => (
        <li key={ex} className="max-w-full">
          <button
            type="button"
            disabled={disabled}
            onClick={() => onPick(ex)}
            className={`max-w-full px-3 py-1.5 rounded-full border border-slate-300 bg-white text-xs font-semibold text-slate-700 text-left hover:bg-slate-50 hover:border-slate-400 disabled:opacity-50 ${focusRing}`}
          >
            {ex}
          </button>
        </li>
      ))}
    </ul>
  );
}

function Interpretation({ result }: { result: AskResponse }) {
  const note = INTERPRETED_NOTE[result.interpreted_by];
  if (result.interpretation.length === 0 && !note) return null;
  return (
    <div className="space-y-1.5">
      {result.interpretation.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold text-slate-600">Searched for:</span>
          <ul className="flex flex-wrap gap-2" aria-label="Searched for">
            {result.interpretation.map((c, i) => (
              <li
                key={`${c}-${i}`}
                className="px-2.5 py-1 rounded-full bg-indigo-50 border border-indigo-200 text-xs font-semibold text-indigo-900 break-words max-w-full"
              >
                {c}
              </li>
            ))}
          </ul>
        </div>
      )}
      {note && <p className="text-xs text-slate-500">({note})</p>}
    </div>
  );
}

function Results({ result, onPick, disabled }: { result: AskResponse; onPick: (q: string) => void; disabled: boolean }) {
  const examples = result.examples.length > 0 ? result.examples : DEFAULT_EXAMPLES;
  const emptyPortfolio =
    result.total_contracts === 0 && (result.status === "answered" || result.status === "no_match");

  if (emptyPortfolio) {
    return (
      <div className="p-8 text-center bg-white border border-slate-200 rounded-2xl space-y-2">
        <p className="text-sm font-semibold text-slate-800">There are no contracts to search yet</p>
        <Link
          href="/"
          className={`inline-block text-sm font-semibold text-blue-700 hover:text-blue-900 underline ${focusRing}`}
        >
          Upload a contract first
        </Link>
      </div>
    );
  }

  if (result.status === "not_understood" || result.status === "unavailable_ai") {
    return (
      <div className="space-y-4">
        <div className="p-4 rounded-2xl bg-amber-50 border border-amber-200 space-y-1">
          <h2 className="text-sm font-bold text-amber-900">
            {result.status === "unavailable_ai" ? "That question needs the AI, which is not available right now" : "I could not turn that into a search"}
          </h2>
          <p className="text-sm text-amber-900 break-words">{result.summary}</p>
        </div>
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-600">Try one of these instead</p>
          <ExampleChips examples={examples} onPick={onPick} disabled={disabled} />
        </div>
      </div>
    );
  }

  if (result.status === "passages") {
    return (
      <div className="space-y-4">
        <h2 className="text-base font-bold text-slate-900 break-words">Text search results (not an answer)</h2>
        <p className="text-sm text-slate-700 break-words">{result.summary}</p>
        <Interpretation result={result} />
        {result.passages.length === 0 ? (
          <p className="text-sm text-slate-600">No passages found.</p>
        ) : (
          <ul className="space-y-3">
            {result.passages.map((p, i) => (
              <PassageCard key={`${p.contract_id}-${p.page}-${i}`} passage={p} />
            ))}
          </ul>
        )}
      </div>
    );
  }

  if (result.status === "no_match") {
    return (
      <div className="space-y-4">
        <h2 className="text-base font-bold text-slate-900 break-words">No contract matches</h2>
        <p className="text-sm text-slate-700 break-words">{result.summary}</p>
        <Interpretation result={result} />
        <p className="text-xs text-slate-600">
          A contract whose value is unknown or missing never counts as a match, so it is not listed here. {result.total_contracts}{" "}
          {result.total_contracts === 1 ? "contract was" : "contracts were"} checked.
        </p>
        <div className="space-y-2">
          <p className="text-xs font-semibold text-slate-600">Try an example</p>
          <ExampleChips examples={examples} onPick={onPick} disabled={disabled} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-base font-bold text-slate-900 break-words">{result.summary}</h2>
      <Interpretation result={result} />
      <p className="text-xs text-slate-600">
        {result.matches.length} of {result.total_contracts} {result.total_contracts === 1 ? "contract" : "contracts"} analysed
        matched. The values below are the ones that made each contract match.
      </p>
      <ul className="space-y-3">
        {result.matches.map((m) => (
          <MatchCard key={m.contract_id} match={m} />
        ))}
      </ul>
    </div>
  );
}

export default function AskPage() {
  const inputId = useId();
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<AskResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ message: string; question: string } | null>(null);
  const requestId = useRef(0);

  async function ask(raw: string) {
    const q = raw.trim();
    if (!q) return;
    const id = ++requestId.current;
    setLoading(true);
    setError(null);
    try {
      const res = await askContracts(q);
      if (id !== requestId.current) return;
      setResult(res);
    } catch (e: unknown) {
      if (id !== requestId.current) return;
      setError({ message: errorMessage(e, "Could not answer that question."), question: q });
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }

  function pick(q: string) {
    setQuestion(q);
    void ask(q);
  }

  const examples = result && result.examples.length > 0 ? result.examples : DEFAULT_EXAMPLES;

  return (
    <div>
      <PageHeader
        title="Ask your contracts"
        subtitle="Filters and searches run over your extracted values. Nothing is written by AI. Check each value's source."
        breadcrumbs={[{ label: "Dashboard", href: "/" }, { label: "Ask" }]}
      />

      <div className="p-4 sm:p-8 space-y-6 max-w-4xl mx-auto">
        <div role="note" className="flex items-start gap-2 text-xs text-slate-600 bg-slate-100 border border-slate-200 rounded-xl px-3 py-2">
          <Info className="w-4 h-4 shrink-0 mt-px text-slate-500" aria-hidden="true" />
          <p>
            <span className="font-semibold text-slate-700">AI-assisted, not legal advice.</span> Your question is turned into a
            filter, and the filter is run in code over the stored, source-linked values. A model never writes the answer. The rows
            shown are the real values that made each contract match.
          </p>
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            void ask(question);
          }}
          className="space-y-3"
        >
          <label htmlFor={inputId} className="sr-only">
            Your question about your contracts
          </label>
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1 min-w-0">
              <MessageSquareText
                className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-slate-400 pointer-events-none"
                aria-hidden="true"
              />
              <input
                id={inputId}
                type="text"
                value={question}
                maxLength={MAX_LEN}
                onChange={(e) => setQuestion(e.target.value)}
                autoComplete="off"
                placeholder="Which contracts auto-renew in the next 6 months?"
                aria-label="Your question about your contracts"
                className={`w-full pl-9 pr-3 py-2.5 rounded-xl border border-slate-300 bg-white text-sm text-slate-900 placeholder:text-slate-500 ${focusRing}`}
              />
            </div>
            <button
              type="submit"
              disabled={loading || question.trim().length === 0}
              className={`inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-sm font-bold shadow-sm disabled:opacity-50 disabled:cursor-not-allowed ${focusRing}`}
            >
              <Search className="w-4 h-4" aria-hidden="true" />
              {loading ? "Looking..." : "Ask"}
            </button>
          </div>
          <div className="space-y-1.5">
            <p className="text-xs font-semibold text-slate-600">Examples</p>
            <ExampleChips examples={examples} onPick={pick} disabled={loading} />
          </div>
        </form>

        <div aria-live="polite" aria-busy={loading} className="space-y-4">
          {loading && (
            <p role="status" className="text-sm text-slate-600">
              Looking through your contracts...
            </p>
          )}
          {error && (
            <div role="alert" className="p-4 rounded-2xl bg-red-50 border border-red-200 text-sm text-red-900 space-y-2">
              <p className="font-semibold break-words">{error.message}</p>
              <button
                type="button"
                onClick={() => void ask(error.question)}
                disabled={loading}
                className={`px-3 py-1.5 rounded-xl bg-white border border-red-300 text-xs font-bold text-red-800 hover:bg-red-100 disabled:opacity-50 ${focusRing}`}
              >
                Try again
              </button>
            </div>
          )}
          {result && !error && !loading && <Results result={result} onPick={pick} disabled={loading} />}
        </div>
      </div>
    </div>
  );
}
