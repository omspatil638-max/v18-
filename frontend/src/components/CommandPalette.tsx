"use client";

import { Fragment, useEffect, useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CornerDownLeft, FileText, Quote, Search, Tag, X } from "lucide-react";
import { StatusChip } from "@/components/ui/StatusChip";
import { OPEN_SEARCH_EVENT, errorMessage, searchAll } from "@/lib/api";
import { formatDate, locationLabel, sourceHref } from "@/lib/format";
import { trapTabKey, useDialogFocus } from "@/lib/useDialogFocus";
import type { SearchResponse, VerificationStatus } from "@/lib/types";

const DEBOUNCE_MS = 250;
const MIN_CHARS = 2;

const QUICK_LINKS: { label: string; href: string }[] = [
  { label: "Dashboard", href: "/" },
  { label: "Needs review", href: "/review" },
  { label: "Renewals", href: "/renewals" },
  { label: "Ask your contracts", href: "/ask" },
  { label: "Calendar", href: "/calendar" },
  { label: "Data quality", href: "/data-quality" },
  { label: "Rules", href: "/rules" },
  { label: "Contracts", href: "/contracts" },
];

interface PaletteItem {
  id: string;
  group: "goto" | "contracts" | "values" | "passages";
  href: string;
  title: string;
  /** Second line of plain text. */
  detail?: string | null;
  /** Contract title shown small above/next to a value or passage. */
  contractTitle?: string;
  status?: VerificationStatus;
  snippet?: string;
}

const GROUP_LABEL: Record<PaletteItem["group"], string> = {
  goto: "Go to",
  contracts: "Contracts",
  values: "Values",
  passages: "Passages",
};

const GROUP_ORDER: PaletteItem["group"][] = ["goto", "contracts", "values", "passages"];

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
  if (last < text.length) parts.push(<Fragment key="tail">{text.slice(last)}</Fragment>);
  // Any stray marker left over from a cut-off snippet is dropped rather than shown.
  return <>{parts.length > 0 ? parts : text.replace(/[«»]/g, "")}</>;
}

function GroupIcon({ group }: { group: PaletteItem["group"] }) {
  const cls = "w-4 h-4 shrink-0 mt-0.5 text-slate-500";
  if (group === "contracts") return <FileText className={cls} aria-hidden="true" />;
  if (group === "values") return <Tag className={cls} aria-hidden="true" />;
  if (group === "passages") return <Quote className={cls} aria-hidden="true" />;
  return <CornerDownLeft className={cls} aria-hidden="true" />;
}

function buildItems(query: string, data: SearchResponse | null): PaletteItem[] {
  const q = query.trim().toLowerCase();
  const items: PaletteItem[] = QUICK_LINKS.filter((l) => !q || l.label.toLowerCase().includes(q)).map((l) => ({
    id: `goto-${l.href}`,
    group: "goto",
    href: l.href,
    title: l.label,
  }));
  if (!data) return items;
  for (const c of data.contracts) {
    const expiry = formatDate(c.expiry_date);
    const bits = [c.detail, c.contract_type, c.tags.length > 0 ? c.tags.join(", ") : null, expiry ? `Expires ${expiry}` : null];
    items.push({
      id: `contract-${c.contract_id}`,
      group: "contracts",
      href: `/contracts/${c.contract_id}`,
      title: c.title,
      detail: bits.filter(Boolean).join(" · ") || null,
    });
  }
  data.fields.forEach((f, i) => {
    items.push({
      id: `field-${f.contract_id}-${i}`,
      group: "values",
      href: f.page != null ? sourceHref(f.contract_id, f.page, f.quote) : `/contracts/${f.contract_id}`,
      title: `${f.label}: ${f.value}`,
      contractTitle: f.title,
      status: f.status,
    });
  });
  data.passages.forEach((p, i) => {
    items.push({
      id: `passage-${p.contract_id}-${i}`,
      group: "passages",
      href: sourceHref(p.contract_id, p.page, p.quote),
      title: locationLabel(p.page, p.section) ?? `Page ${p.page}`,
      contractTitle: p.title,
      snippet: p.snippet,
    });
  });
  return items;
}

function PaletteDialog({ onClose }: { onClose: () => void }) {
  const uid = useId();
  const router = useRouter();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const requestId = useRef(0);
  const [query, setQuery] = useState("");
  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const [settledFor, setSettledFor] = useState<string | null>(null);

  useDialogFocus(inputRef);

  const trimmed = query.trim();
  const searchable = trimmed.length >= MIN_CHARS;

  // Debounced search. A newer query (or closing) makes older responses irrelevant.
  useEffect(() => {
    const id = ++requestId.current;
    if (!searchable) {
      setData(null);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      searchAll(trimmed, controller.signal)
        .then((res) => {
          if (id !== requestId.current) return;
          setData(res);
          setSettledFor(trimmed);
          setError(null);
          setLoading(false);
        })
        .catch((e: unknown) => {
          if (id !== requestId.current || controller.signal.aborted) return;
          setData(null);
          setSettledFor(null);
          setError(errorMessage(e, "Search failed. Please try again."));
          setLoading(false);
        });
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [trimmed, searchable]);

  const items = useMemo(() => buildItems(query, searchable ? data : null), [query, searchable, data]);
  const activeIndex = Math.min(active, Math.max(items.length - 1, 0));
  const optionId = (i: number) => `${uid}-opt-${i}`;

  useEffect(() => {
    setActive(0);
  }, [trimmed, data]);

  useEffect(() => {
    document.getElementById(`${uid}-opt-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, uid, items]);

  function go(item: PaletteItem) {
    onClose();
    router.push(item.href);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (items.length === 0) return;
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((activeIndex + step + items.length) % items.length);
      return;
    }
    if (e.key === "Enter" && e.target === inputRef.current) {
      e.preventDefault();
      const item = items[activeIndex];
      if (item) go(item);
      return;
    }
    trapTabKey(e, dialogRef.current);
  }

  const resultCount = items.filter((i) => i.group !== "goto").length;
  const nothingFound = searchable && !loading && !error && data !== null && settledFor === trimmed && resultCount === 0;
  const statusText = !searchable
    ? ""
    : loading
      ? "Searching..."
      : error
        ? ""
        : resultCount === 0
          ? "No results."
          : `${resultCount} ${resultCount === 1 ? "result" : "results"}.`;

  const titleId = `${uid}-title`;
  const listId = `${uid}-list`;
  let flat = 0;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center p-3 sm:pt-[10vh] bg-slate-900/60"
      data-print-hide
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-xl max-h-[85vh] flex flex-col bg-white rounded-2xl shadow-2xl border border-slate-200 focus:outline-none overflow-hidden"
      >
        <h2 id={titleId} className="sr-only">
          Search
        </h2>
        <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-200">
          <Search className="w-4 h-4 shrink-0 text-slate-500" aria-hidden="true" />
          <input
            ref={inputRef}
            type="text"
            role="combobox"
            aria-expanded={true}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={items.length > 0 ? optionId(activeIndex) : undefined}
            aria-label="Search contracts, values and text, or jump to a page"
            autoComplete="off"
            spellCheck={false}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search contracts, values and text"
            className="flex-1 min-w-0 py-2 text-sm text-slate-900 placeholder:text-slate-500 bg-transparent outline-none focus-visible:outline-none"
          />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close search"
            className="p-2 rounded-lg text-slate-500 hover:text-slate-900 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div role="status" className="sr-only">
          {statusText}
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto overscroll-contain p-2">
          <div id={listId} role="listbox" aria-label="Search results">
            {GROUP_ORDER.map((group) => {
              const groupItems = items.filter((i) => i.group === group);
              if (groupItems.length === 0) return null;
              const headingId = `${uid}-group-${group}`;
              return (
                <div key={group} role="group" aria-labelledby={headingId} className="mb-2 last:mb-0">
                  <p
                    id={headingId}
                    className="px-2 pt-1 pb-1 text-[11px] font-bold uppercase tracking-wider text-slate-500"
                  >
                    {GROUP_LABEL[group]}
                  </p>
                  {groupItems.map((item) => {
                    const index = flat++;
                    const selected = index === activeIndex;
                    return (
                      <div
                        key={item.id}
                        id={optionId(index)}
                        role="option"
                        aria-selected={selected}
                        onMouseMove={() => {
                          if (!selected) setActive(index);
                        }}
                        onClick={() => go(item)}
                        className={`flex items-start gap-2.5 px-2 py-2 rounded-lg cursor-pointer ${
                          selected ? "bg-blue-50 ring-1 ring-blue-300" : "hover:bg-slate-50"
                        }`}
                      >
                        <GroupIcon group={item.group} />
                        <div className="min-w-0 flex-1 space-y-0.5">
                          <p className="text-sm font-semibold text-slate-900 break-words">{item.title}</p>
                          {item.contractTitle && (
                            <p className="text-xs text-slate-600 break-words">{item.contractTitle}</p>
                          )}
                          {item.snippet && (
                            <p className="text-xs text-slate-700 break-words leading-relaxed">
                              <MarkedSnippet text={item.snippet} />
                            </p>
                          )}
                          {item.detail && <p className="text-xs text-slate-500 break-words">{item.detail}</p>}
                          {item.status && (
                            <div className="pt-0.5">
                              <StatusChip status={item.status} />
                            </div>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>

          {loading && (
            <p className="px-2 py-3 text-xs text-slate-500" aria-hidden="true">
              Searching...
            </p>
          )}
          {error && (
            <p role="alert" className="mx-2 my-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {error}
            </p>
          )}
          {nothingFound && (
            <p className="px-2 py-3 text-xs text-slate-600">
              Nothing found for &lsquo;{trimmed}&rsquo;. Search looks in titles, parties, tags, extracted values and
              contract text.
            </p>
          )}
          {!searchable && trimmed.length > 0 && (
            <p className="px-2 py-2 text-xs text-slate-500">Type at least {MIN_CHARS} characters to search.</p>
          )}
        </div>

        <p className="px-3 py-2 border-t border-slate-100 text-[11px] text-slate-500">
          Values are AI-extracted and passages are document text: open the source to check. Not legal advice.
        </p>
      </div>
    </div>
  );
}

/** Ctrl+K / Cmd+K search palette. Mounted once in AppShell. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
    }
    function handleOpen() {
      setOpen(true);
    }
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener(OPEN_SEARCH_EVENT, handleOpen);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener(OPEN_SEARCH_EVENT, handleOpen);
    };
  }, []);

  if (!open) return null;
  return <PaletteDialog onClose={() => setOpen(false)} />;
}
