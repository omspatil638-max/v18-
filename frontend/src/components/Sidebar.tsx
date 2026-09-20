"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  FileText,
  ClipboardCheck,
  Scale,
  PlusCircle,
  LogOut,
  Library,
  RefreshCw,
  CalendarDays,
  ShieldCheck,
  Search,
  MessageSquareText,
  SlidersHorizontal,
} from "lucide-react";
import {
  FIELDS_CHANGED_EVENT,
  FLAGS_CHANGED_EVENT,
  OPEN_SEARCH_EVENT,
  fetchContracts,
  fetchDataQuality,
  fetchFlagSummary,
  fetchMe,
  fetchSystemStatus,
  logout,
} from "@/lib/api";
import type { AuthMode, ContractSummary, FlagSummary, User } from "@/lib/types";

const navLinkClass = (active: boolean) =>
  `mt-1 flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400 ${
    active ? "bg-blue-600 text-white shadow-md" : "text-slate-400 hover:bg-slate-800/80 hover:text-white"
  }`;

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  return parts
    .slice(0, 2)
    .map((p) => p[0].toUpperCase())
    .join("");
}

interface SidebarProps {
  /** id for aria-controls on the drawer toggle. */
  id: string;
  /** Below `md` the sidebar is an off-canvas drawer; this says whether it is showing. */
  open: boolean;
  drawerRef: React.RefObject<HTMLElement>;
  /** Called when a link inside the sidebar is followed (closes the drawer). */
  onNavigate: () => void;
  onKeyDown: (e: React.KeyboardEvent<HTMLElement>) => void;
}

export function Sidebar({ id, open, drawerRef, onNavigate, onKeyDown }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const [contracts, setContracts] = useState<ContractSummary[]>([]);
  const [user, setUser] = useState<User | null>(null);
  const [authMode, setAuthMode] = useState<AuthMode | null>(null);
  const [flagSummary, setFlagSummary] = useState<FlagSummary | null>(null);
  const [qualityOpen, setQualityOpen] = useState<number | null>(null);
  const onLogin = pathname.startsWith("/login");

  useEffect(() => {
    if (onLogin) return;
    let cancelled = false;
    async function loadNav() {
      // Each request fails independently; the sidebar simply shows less if one does.
      const [list, me, sys, flags] = await Promise.all([
        fetchContracts().catch(() => null),
        fetchMe().catch(() => null),
        fetchSystemStatus().catch(() => null),
        fetchFlagSummary().catch(() => null),
      ]);
      if (cancelled) return;
      if (list) setContracts(list);
      setFlagSummary(flags);
      setUser(me);
      setAuthMode(sys ? sys.auth_mode : null);
    }
    loadNav();
    // Refresh when pathname changes (e.g. after upload or login)
    return () => {
      cancelled = true;
    };
  }, [pathname, onLogin]);

  // Keep the "Needs review" count fresh when a flag is resolved/dismissed/reopened elsewhere.
  useEffect(() => {
    if (onLogin) return;
    let cancelled = false;
    function refresh() {
      fetchFlagSummary()
        .then((flags) => {
          if (!cancelled) setFlagSummary(flags);
        })
        .catch(() => {});
    }
    window.addEventListener(FLAGS_CHANGED_EVENT, refresh);
    return () => {
      cancelled = true;
      window.removeEventListener(FLAGS_CHANGED_EVENT, refresh);
    };
  }, [onLogin]);

  // "Data quality" count: fetched on mount, when the window regains focus, and after a field is reviewed.
  // Quiet on error: the badge just stays as it was.
  useEffect(() => {
    if (onLogin) return;
    let cancelled = false;
    function refresh() {
      fetchDataQuality()
        .then((report) => {
          if (!cancelled) setQualityOpen(report.open_issues);
        })
        .catch(() => {});
    }
    refresh();
    window.addEventListener("focus", refresh);
    window.addEventListener(FIELDS_CHANGED_EVENT, refresh);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refresh);
      window.removeEventListener(FIELDS_CHANGED_EVENT, refresh);
    };
  }, [onLogin]);

  function openSearch() {
    onNavigate();
    // After the drawer has closed and given focus back, so the palette remembers a visible element.
    window.setTimeout(() => window.dispatchEvent(new Event(OPEN_SEARCH_EVENT)), 0);
  }

  async function handleLogout() {
    try {
      await logout();
    } finally {
      setUser(null);
      setContracts([]);
      setFlagSummary(null);
      router.push("/login");
    }
  }

  if (onLogin) return null;

  return (
    <aside
      ref={drawerRef}
      id={id}
      data-print-hide
      tabIndex={-1}
      aria-label="Sidebar"
      onKeyDown={onKeyDown}
      className={`fixed inset-y-0 left-0 z-40 w-64 max-w-[85vw] flex flex-col bg-slate-900 text-slate-200 shrink-0 border-r border-slate-800 focus:outline-none transition-[transform,visibility] duration-200 motion-reduce:transition-none md:static md:z-auto md:max-w-none md:translate-x-0 md:visible md:transition-none ${
        open ? "translate-x-0 visible" : "-translate-x-full invisible"
      }`}
    >
      {/* Logo */}
      <div className="flex items-center gap-3 px-6 py-5 border-b border-slate-800">
        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-gradient-to-tr from-blue-600 to-indigo-500 shadow-md">
          <Scale className="w-5 h-5 text-white" />
        </div>
        <div>
          <span className="font-bold text-white text-lg tracking-tight block">
            ContractLens
          </span>
          <span className="text-[10px] uppercase font-bold text-blue-400 tracking-wider">
            AI Document Intelligence
          </span>
        </div>
      </div>

      {/* Nav */}
      <nav
        className="flex-1 px-4 py-6 space-y-6 overflow-y-auto overscroll-contain"
        onClick={(e) => {
          if (e.target instanceof Element && e.target.closest("a")) onNavigate();
        }}
      >
        <div>
          <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wider px-3 mb-2">
            Main Menu
          </p>
          <button
            type="button"
            onClick={openSearch}
            aria-haspopup="dialog"
            aria-keyshortcuts="Control+K Meta+K"
            className="mb-2 w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold text-slate-300 bg-slate-800/60 border border-slate-700/60 hover:bg-slate-800 hover:text-white transition-all focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
          >
            <Search className="w-4 h-4 shrink-0" aria-hidden="true" />
            <span className="flex-1 text-left">Search</span>
            <kbd className="hidden md:inline px-1.5 py-0.5 rounded border border-slate-600 text-[10px] font-semibold text-slate-400">
              Ctrl K
            </kbd>
          </button>
          <Link href="/" className={navLinkClass(pathname === "/").replace("mt-1 ", "")}>
            <LayoutDashboard className="w-4 h-4 shrink-0" aria-hidden="true" />
            Dashboard
          </Link>
          <Link href="/contracts" className={navLinkClass(pathname === "/contracts")}>
            <Library className="w-4 h-4 shrink-0" aria-hidden="true" />
            Contracts
          </Link>
          <Link
            href="/review"
            aria-label={
              flagSummary && flagSummary.open > 0
                ? `Needs review, ${flagSummary.open} open${flagSummary.high > 0 ? `, ${flagSummary.high} high priority` : ""}`
                : undefined
            }
            className={navLinkClass(pathname.startsWith("/review"))}
          >
            <ClipboardCheck className="w-4 h-4 shrink-0" aria-hidden="true" />
            <span className="flex-1">Needs review</span>
            {flagSummary && flagSummary.open > 0 && (
              <span
                className={`min-w-5 px-1.5 py-0.5 rounded-full text-[10px] font-bold text-center text-white ${
                  flagSummary.high > 0 ? "bg-red-600" : "bg-amber-600"
                }`}
              >
                {flagSummary.open}
              </span>
            )}
          </Link>
          <Link href="/renewals" className={navLinkClass(pathname.startsWith("/renewals"))}>
            <RefreshCw className="w-4 h-4 shrink-0" aria-hidden="true" />
            Renewals
          </Link>
          <Link href="/ask" className={navLinkClass(pathname.startsWith("/ask"))}>
            <MessageSquareText className="w-4 h-4 shrink-0" aria-hidden="true" />
            Ask
          </Link>
          <Link href="/calendar" className={navLinkClass(pathname.startsWith("/calendar"))}>
            <CalendarDays className="w-4 h-4 shrink-0" aria-hidden="true" />
            Calendar
          </Link>
          <Link
            href="/data-quality"
            aria-label={
              qualityOpen != null && qualityOpen > 0
                ? `Data quality, ${qualityOpen} open ${qualityOpen === 1 ? "item" : "items"}`
                : undefined
            }
            className={navLinkClass(pathname.startsWith("/data-quality"))}
          >
            <ShieldCheck className="w-4 h-4 shrink-0" aria-hidden="true" />
            <span className="flex-1">Data quality</span>
            {qualityOpen != null && qualityOpen > 0 && (
              <span className="min-w-5 px-1.5 py-0.5 rounded-full text-[10px] font-bold text-center text-white bg-amber-600">
                {qualityOpen}
              </span>
            )}
          </Link>
          <Link href="/rules" className={navLinkClass(pathname.startsWith("/rules"))}>
            <SlidersHorizontal className="w-4 h-4 shrink-0" aria-hidden="true" />
            Rules
          </Link>
        </div>

        {/* Dynamic Contracts List */}
        <div>
          <div className="flex items-center justify-between px-3 mb-2">
            <p className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">
              Contracts ({contracts.length})
            </p>
            <Link
              href="/"
              title="Upload new contract"
              className="text-slate-400 hover:text-blue-400 transition-colors"
            >
              <PlusCircle className="w-3.5 h-3.5" />
            </Link>
          </div>

          {contracts.length === 0 ? (
            <p className="text-xs text-slate-500 px-3 py-2 italic">
              No contracts uploaded yet
            </p>
          ) : (
            <div className="space-y-1">
              {contracts.map((c) => {
                const active = pathname.includes(c.id);
                return (
                  <Link
                    key={c.id}
                    href={`/contracts/${c.id}`}
                    className={`flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs font-medium transition-all ${
                      active
                        ? "bg-blue-600/20 text-blue-300 border border-blue-500/30 font-semibold"
                        : "text-slate-400 hover:bg-slate-800/60 hover:text-white"
                    }`}
                  >
                    <FileText className="w-3.5 h-3.5 shrink-0 text-blue-400" />
                    <span className="truncate">{c.title}</span>
                  </Link>
                );
              })}
            </div>
          )}
        </div>
      </nav>

      {/* User Footer */}
      <div className="p-4 border-t border-slate-800">
        {user ? (
          <div className="px-3 py-2.5 rounded-xl bg-slate-800/60 border border-slate-700/50 flex items-center gap-3">
            <div
              className="w-7 h-7 rounded-full bg-blue-600 text-white flex items-center justify-center text-xs font-bold shrink-0"
              aria-hidden="true"
            >
              {initials(user.name)}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs text-white font-semibold truncate">{user.name}</p>
              <p className="text-[10px] text-slate-400 truncate">{user.email}</p>
            </div>
            {authMode === "login" && (
              <button
                type="button"
                onClick={handleLogout}
                title="Sign out"
                aria-label="Sign out"
                className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
              >
                <LogOut className="w-4 h-4" />
              </button>
            )}
          </div>
        ) : (
          <p className="px-3 py-2 text-[11px] text-slate-500 italic">Not signed in</p>
        )}
      </div>
    </aside>
  );
}
