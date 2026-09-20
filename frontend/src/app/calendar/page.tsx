"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  BellRing,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  CircleDollarSign,
  ClipboardList,
  Download,
  Info,
  Circle,
} from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { DeadlineTypeBadge, OverdueBadge, UnverifiedBadge, isUnverified } from "@/components/ui/DateBadges";
import { calendarUrl, errorMessage, fetchGlobalDeadlines } from "@/lib/api";
import { dueInText, isoDate, locationLabel, sourceHref } from "@/lib/format";
import type { DeadlineType, DeadlineWithContext } from "@/lib/types";

const MONTHS = [
  "January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December",
];
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const focusRing = "focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600";

interface TypeStyle {
  short: string;
  className: string;
  Icon: typeof Circle;
}

const TYPE_STYLE: Record<string, TypeStyle> = {
  EXPIRY: { short: "Expiry", className: "bg-slate-200 text-slate-900 border-slate-300", Icon: CalendarClock },
  RENEWAL_NOTICE: { short: "Notice", className: "bg-indigo-100 text-indigo-900 border-indigo-300", Icon: BellRing },
  PAYMENT: { short: "Payment", className: "bg-green-100 text-green-900 border-green-300", Icon: CircleDollarSign },
  OBLIGATION: { short: "Task", className: "bg-sky-100 text-sky-900 border-sky-300", Icon: ClipboardList },
  OTHER: { short: "Other", className: "bg-slate-100 text-slate-800 border-slate-200", Icon: Circle },
};

function styleFor(type: DeadlineType | string): TypeStyle {
  return TYPE_STYLE[type] ?? TYPE_STYLE.OTHER;
}

function DayChip({ d }: { d: DeadlineWithContext }) {
  const overdue = d.is_overdue;
  const { short, className, Icon } = overdue
    ? { short: "Overdue", className: "bg-red-100 text-red-900 border-red-400", Icon: AlertTriangle }
    : styleFor(d.deadline_type);
  return (
    <span
      className={`flex items-center gap-1 min-w-0 px-1.5 py-0.5 rounded border text-[10px] font-semibold leading-tight ${className}`}
    >
      <Icon className="w-3 h-3 shrink-0" aria-hidden="true" />
      <span className="truncate">
        {short} · {d.contract_title}
      </span>
    </span>
  );
}

function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  const date = new Date(y, m - 1, d);
  return date.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

function DeadlineItem({ d }: { d: DeadlineWithContext }) {
  const location = locationLabel(d.source_page, d.source_section);
  return (
    <li
      className={`p-3 rounded-xl border space-y-1.5 ${
        d.is_overdue ? "bg-red-50 border-red-300 border-l-4" : "bg-white border-slate-200"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <DeadlineTypeBadge type={d.deadline_type} />
        {d.is_overdue && <OverdueBadge label={`Overdue by ${Math.abs(d.days_until)} ${Math.abs(d.days_until) === 1 ? "day" : "days"}`} />}
        {isUnverified(d.verification_status) && <UnverifiedBadge />}
        {!d.is_overdue && <span className="text-xs font-semibold text-slate-600">{dueInText(d.days_until)}</span>}
      </div>
      <p className="text-sm text-slate-900 break-words">
        <Link href={`/contracts/${d.contract_id}`} className={`font-bold hover:text-blue-600 ${focusRing}`}>
          {d.contract_title}
        </Link>
        <span className="text-slate-500"> — </span>
        {d.label}
      </p>
      {d.source_page != null && location ? (
        <Link
          href={sourceHref(d.contract_id, d.source_page, d.source_quote)}
          className={`inline-block text-xs font-semibold text-blue-700 hover:text-blue-900 ${focusRing}`}
        >
          Source: {location}
        </Link>
      ) : (
        <p className="text-xs text-slate-500">
          {d.deadline_type === "OBLIGATION" || d.responsible_party
            ? "Source location not verified."
            : "Source: derived from contract dates."}
        </p>
      )}
    </li>
  );
}

export default function CalendarPage() {
  const today = useMemo(() => new Date(), []);
  const todayIso = isoDate(today);
  const [view, setView] = useState({ year: today.getFullYear(), month: today.getMonth() });
  const [selected, setSelected] = useState<string | null>(todayIso);
  const [deadlines, setDeadlines] = useState<DeadlineWithContext[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const gridRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setDeadlines(await fetchGlobalDeadlines(1825, true));
      setError(null);
    } catch (e: unknown) {
      setError(errorMessage(e, "Could not load deadlines."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const byDate = useMemo(() => {
    const map = new Map<string, DeadlineWithContext[]>();
    for (const d of deadlines) {
      const list = map.get(d.deadline_date) ?? [];
      list.push(d);
      map.set(d.deadline_date, list);
    }
    return map;
  }, [deadlines]);

  const monthPrefix = `${view.year}-${String(view.month + 1).padStart(2, "0")}-`;
  const monthDeadlines = useMemo(
    () => deadlines.filter((d) => d.deadline_date.startsWith(monthPrefix)).sort((a, b) => a.deadline_date.localeCompare(b.deadline_date)),
    [deadlines, monthPrefix],
  );
  const agenda = useMemo(() => {
    const groups: { date: string; items: DeadlineWithContext[] }[] = [];
    for (const d of monthDeadlines) {
      const last = groups[groups.length - 1];
      if (last && last.date === d.deadline_date) last.items.push(d);
      else groups.push({ date: d.deadline_date, items: [d] });
    }
    return groups;
  }, [monthDeadlines]);

  const firstOfMonth = `${monthPrefix}01`;
  const earlierOverdue = useMemo(
    () => deadlines.filter((d) => d.is_overdue && d.deadline_date < firstOfMonth).sort((a, b) => a.deadline_date.localeCompare(b.deadline_date)),
    [deadlines, firstOfMonth],
  );

  const daysInMonth = new Date(view.year, view.month + 1, 0).getDate();
  const leading = (new Date(view.year, view.month, 1).getDay() + 6) % 7; // Monday first
  const cells: (number | null)[] = [
    ...Array<null>(leading).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => i + 1),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  function shiftMonth(delta: number) {
    const d = new Date(view.year, view.month + delta, 1);
    setView({ year: d.getFullYear(), month: d.getMonth() });
    setSelected(null);
  }

  function goToday() {
    setView({ year: today.getFullYear(), month: today.getMonth() });
    setSelected(todayIso);
  }

  function jumpTo(iso: string) {
    const [y, m] = iso.split("-").map(Number);
    setView({ year: y, month: m - 1 });
    setSelected(iso);
  }

  function handleGridKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    const target = e.target;
    if (!(target instanceof HTMLElement) || !target.dataset.date) return;
    const steps: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    const step = steps[e.key];
    if (step === undefined) return;
    e.preventDefault();
    const [y, m, d] = target.dataset.date.split("-").map(Number);
    const next = isoDate(new Date(y, m - 1, d + step));
    const el = gridRef.current?.querySelector<HTMLElement>(`[data-date="${next}"]`);
    el?.focus();
  }

  const monthHeading = `${MONTHS[view.month]} ${view.year}`;
  const selectedItems = selected ? (byDate.get(selected) ?? []) : [];
  const isThisMonth = view.year === today.getFullYear() && view.month === today.getMonth();

  return (
    <div>
      <PageHeader
        title="Calendar"
        subtitle="Contract deadlines by day"
        breadcrumbs={[{ label: "Dashboard", href: "/" }, { label: "Calendar" }]}
        actions={
          <a
            href={calendarUrl()}
            download
            className={`inline-flex items-center gap-1.5 px-3 py-2 bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 text-xs font-bold rounded-xl shadow-sm ${focusRing}`}
          >
            <Download className="w-4 h-4" aria-hidden="true" />
            Download .ics
          </a>
        }
      />

      <div className="p-4 sm:p-8 space-y-6 max-w-6xl mx-auto">
        <div role="note" className="flex items-start gap-2 text-xs text-slate-600 bg-slate-100 border border-slate-200 rounded-xl px-3 py-2">
          <Info className="w-4 h-4 shrink-0 mt-px text-slate-500" aria-hidden="true" />
          <p>
            <span className="font-semibold text-slate-700">AI-assisted, not legal advice.</span> Dates come from what the
            AI read in your contracts. Open the source link on a deadline to check it. Completed obligations are not
            shown.
          </p>
        </div>

        {error && !loading ? (
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
        ) : (
          <>
            {/* Month navigation */}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 aria-live="polite" className="text-lg font-bold text-slate-900">
                {monthHeading}
              </h2>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => shiftMonth(-1)}
                  aria-label="Previous month"
                  className={`p-2 rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 ${focusRing}`}
                >
                  <ChevronLeft className="w-4 h-4" aria-hidden="true" />
                </button>
                <button
                  type="button"
                  onClick={goToday}
                  disabled={isThisMonth && selected === todayIso}
                  className={`px-3 py-2 rounded-xl bg-white border border-slate-300 text-xs font-bold text-slate-800 hover:bg-slate-50 disabled:opacity-50 ${focusRing}`}
                >
                  Today
                </button>
                <button
                  type="button"
                  onClick={() => shiftMonth(1)}
                  aria-label="Next month"
                  className={`p-2 rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 ${focusRing}`}
                >
                  <ChevronRight className="w-4 h-4" aria-hidden="true" />
                </button>
              </div>
            </div>

            {loading && (
              <p role="status" className="text-xs text-slate-500">
                Loading deadlines...
              </p>
            )}

            {earlierOverdue.length > 0 && (
              <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-red-900 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
                <span>
                  {earlierOverdue.length} overdue {earlierOverdue.length === 1 ? "deadline is" : "deadlines are"} in earlier
                  months.
                </span>
                <button
                  type="button"
                  onClick={() => jumpTo(earlierOverdue[0].deadline_date)}
                  className={`font-bold underline ${focusRing}`}
                >
                  Go to the oldest
                </button>
              </p>
            )}

            {/* Month grid (md and up) */}
            <div className="hidden md:block space-y-4" aria-busy={loading}>
              <div
                ref={gridRef}
                role="group"
                aria-label={`${monthHeading} calendar`}
                onKeyDown={handleGridKeyDown}
                className="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden"
              >
                <div className="grid grid-cols-7 border-b border-slate-200 bg-slate-50">
                  {WEEKDAYS.map((w) => (
                    <div key={w} className="px-2 py-2 text-[11px] font-bold uppercase tracking-wider text-slate-500 text-center">
                      {w}
                    </div>
                  ))}
                </div>
                <div className="grid grid-cols-7">
                  {cells.map((day, i) => {
                    if (day === null) {
                      return <div key={`e${i}`} aria-hidden="true" className="min-h-24 border-b border-r border-slate-100 bg-slate-50/60" />;
                    }
                    const iso = `${monthPrefix}${String(day).padStart(2, "0")}`;
                    const items = byDate.get(iso) ?? [];
                    const isToday = iso === todayIso;
                    const isSelected = iso === selected;
                    const hasOverdue = items.some((d) => d.is_overdue);
                    const monthName = MONTHS[view.month];
                    const label = `${day} ${monthName}${isToday ? ", today" : ""}, ${
                      items.length === 0 ? "no deadlines" : `${items.length} ${items.length === 1 ? "deadline" : "deadlines"}`
                    }${hasOverdue ? ", overdue" : ""}`;
                    const shown = items.slice(0, 2);
                    return (
                      <button
                        key={iso}
                        type="button"
                        data-date={iso}
                        aria-label={label}
                        aria-pressed={isSelected}
                        aria-current={isToday ? "date" : undefined}
                        onClick={() => setSelected(iso)}
                        className={`min-h-24 p-1.5 text-left align-top flex flex-col gap-1 border-b border-r border-slate-100 hover:bg-blue-50/60 focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-blue-600 ${
                          isSelected ? "bg-blue-50 ring-2 ring-inset ring-blue-500" : "bg-white"
                        }`}
                      >
                        <span
                          className={`inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold ${
                            isToday ? "bg-blue-600 text-white" : "text-slate-700"
                          }`}
                        >
                          {day}
                        </span>
                        {shown.map((d) => (
                          <DayChip key={d.id} d={d} />
                        ))}
                        {items.length > shown.length && (
                          <span className="text-[10px] font-semibold text-slate-600">+{items.length - shown.length} more</span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Selected day */}
              <section aria-labelledby="day-heading" className="space-y-2">
                {selected ? (
                  <>
                    <h3 id="day-heading" className="text-xs font-bold text-slate-500 uppercase tracking-wider">
                      {longDate(selected)}
                    </h3>
                    {selectedItems.length === 0 ? (
                      <p className="text-sm text-slate-600 bg-white border border-slate-200 rounded-xl px-4 py-3">
                        No deadlines on this day.
                      </p>
                    ) : (
                      <ul className="space-y-2">
                        {selectedItems.map((d) => (
                          <DeadlineItem key={d.id} d={d} />
                        ))}
                      </ul>
                    )}
                  </>
                ) : (
                  <h3 id="day-heading" className="text-sm text-slate-600 font-normal">
                    Choose a day to see its deadlines.
                  </h3>
                )}
              </section>
            </div>

            {/* Agenda (below md) */}
            <section aria-label={`${monthHeading} agenda`} className="md:hidden space-y-4" aria-busy={loading}>
              {agenda.length === 0 ? (
                <p className="text-sm text-slate-600 bg-white border border-slate-200 rounded-xl px-4 py-3">
                  No deadlines in {monthHeading}.
                </p>
              ) : (
                agenda.map((g) => (
                  <div key={g.date} className="space-y-2">
                    <h3 className="text-xs font-bold text-slate-500 uppercase tracking-wider">{longDate(g.date)}</h3>
                    <ul className="space-y-2">
                      {g.items.map((d) => (
                        <DeadlineItem key={d.id} d={d} />
                      ))}
                    </ul>
                  </div>
                ))
              )}
            </section>

            {!loading && deadlines.length === 0 && !error && (
              <p className="text-xs text-slate-500">
                No dated deadlines yet. They appear once a contract&apos;s dates have been extracted; contracts still
                processing are not included.
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
