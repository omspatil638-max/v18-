"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Menu, Scale } from "lucide-react";
import { Sidebar } from "@/components/Sidebar";
import { CommandPalette } from "@/components/CommandPalette";
import { AiDisclaimer } from "@/components/ui/AiDisclaimer";
import { useInPageAlerts } from "@/lib/useInPageAlerts";

const DRAWER_ID = "app-sidebar";
const DESKTOP_QUERY = "(min-width: 768px)";

/**
 * Page chrome. From `md` up the sidebar is a fixed column; below `md` it is an off-canvas drawer
 * opened from a slim top bar. Rendered inside <body className="flex flex-col md:flex-row ...">.
 */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const onLogin = pathname.startsWith("/login");
  // Public one-click page opened from an email link: works signed out, so no sidebar and no API calls from the shell.
  const onPublic = pathname.startsWith("/ack/");
  const [open, setOpen] = useState(false);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const drawerRef = useRef<HTMLElement>(null);
  const wasOpen = useRef(false);

  const close = useCallback(() => setOpen(false), []);

  useInPageAlerts(!onLogin && !onPublic);

  // Close on route change.
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Close when the viewport grows to the desktop layout, where the drawer is a plain column.
  useEffect(() => {
    const media = window.matchMedia(DESKTOP_QUERY);
    function handleChange(e: MediaQueryListEvent) {
      if (e.matches) setOpen(false);
    }
    media.addEventListener("change", handleChange);
    return () => media.removeEventListener("change", handleChange);
  }, []);

  // Focus moves into the drawer when it opens and back to the button when it closes.
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      drawerRef.current?.focus();
    } else if (wasOpen.current) {
      wasOpen.current = false;
      buttonRef.current?.focus();
    }
  }, [open]);

  // Escape closes the drawer.
  useEffect(() => {
    if (!open) return;
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open]);

  // Keep Tab inside the open drawer (the backdrop covers the rest of the page).
  function handleDrawerKeyDown(e: React.KeyboardEvent<HTMLElement>) {
    if (!open || e.key !== "Tab") return;
    const drawer = drawerRef.current;
    if (!drawer) return;
    const nodes = Array.from(
      drawer.querySelectorAll<HTMLElement>('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'),
    );
    if (nodes.length === 0) {
      e.preventDefault();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === drawer)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  if (onPublic) {
    return (
      <main className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
        <div className="relative flex-1 min-h-0 overflow-y-auto">{children}</div>
      </main>
    );
  }

  return (
    <>
      {!onLogin && (
        <header
          data-print-hide
          className="md:hidden shrink-0 flex items-center gap-3 px-2 py-1.5 bg-slate-900 text-white border-b border-slate-800"
        >
          <button
            ref={buttonRef}
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            aria-controls={DRAWER_ID}
            aria-label="Navigation menu"
            className="p-2.5 rounded-lg text-slate-200 hover:bg-slate-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-400"
          >
            <Menu className="w-5 h-5" aria-hidden="true" />
          </button>
          <div className="flex items-center gap-2 min-w-0">
            <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-gradient-to-tr from-blue-600 to-indigo-500 shrink-0">
              <Scale className="w-4 h-4 text-white" aria-hidden="true" />
            </div>
            <span className="font-bold text-white tracking-tight truncate">ContractLens</span>
          </div>
        </header>
      )}

      {!onLogin && open && (
        <div
          data-print-hide
          aria-hidden="true"
          onClick={close}
          className="fixed inset-0 z-30 bg-slate-900/60 md:hidden"
        />
      )}

      <Sidebar
        id={DRAWER_ID}
        open={open}
        drawerRef={drawerRef}
        onNavigate={close}
        onKeyDown={handleDrawerKeyDown}
      />

      {!onLogin && <CommandPalette />}

      <main className="flex-1 flex flex-col min-w-0 min-h-0 overflow-hidden">
        <div data-print-hide className="shrink-0">
          <AiDisclaimer className="border-b" />
        </div>
        <div className="relative flex-1 min-h-0 overflow-y-auto">{children}</div>
      </main>
    </>
  );
}
