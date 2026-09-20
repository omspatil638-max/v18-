"use client";

import { useEffect, useRef } from "react";
import { fetchAlertSettings, fetchAlerts } from "./api";
import { currentSubscription } from "./notifications";

const CHECK_EVERY_MS = 5 * 60 * 1000;
const HOUR_MS = 60 * 60 * 1000;
const STORAGE_KEY = "contractlens:inpage-alert";
const MAX_TITLES = 3;

interface LastShown {
  at: number;
  ids: string[];
}

function readLastShown(): LastShown | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const { at, ids } = parsed as { at?: unknown; ids?: unknown };
    if (typeof at !== "number") return null;
    return { at, ids: Array.isArray(ids) ? ids.filter((i): i is string => typeof i === "string") : [] };
  } catch {
    return null;
  }
}

function writeLastShown(value: LastShown): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  } catch {
    // Storage unavailable: worst case the notice repeats on the next check.
  }
}

/**
 * Fallback for browsers that are not registered for push: while the app is open, show one summary
 * notification of due, unacknowledged alerts, no more often than the user's reminder interval.
 * Never asks for permission (only the reminder-settings dialog does) and fails silently.
 */
export function useInPageAlerts(enabled: boolean): void {
  const busy = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;

    async function check() {
      if (busy.current || cancelled) return;
      if (typeof window === "undefined" || !("Notification" in window)) return;
      if (Notification.permission !== "granted") return;
      busy.current = true;
      try {
        // A registered browser gets real push from the server; don't double up.
        if (await currentSubscription()) return;

        const settings = await fetchAlertSettings();
        const due = (await fetchAlerts({ dueOnly: true })).filter((a) => !a.acknowledged);
        if (cancelled || due.length === 0) return;

        const ids = due.map((a) => a.id).sort();
        const last = readLastShown();
        if (last) {
          if (settings.repeat_enabled) {
            if (Date.now() - last.at < settings.repeat_hours * HOUR_MS) return;
          } else if (ids.every((id) => last.ids.includes(id))) {
            // Repeats are off: only speak up again when something new is due.
            return;
          }
        }

        const titles = due.slice(0, MAX_TITLES).map((a) => a.contract_title);
        const more = due.length - titles.length;
        const body = titles.join(", ") + (more > 0 ? ` and ${more} more` : "");
        // Record first so a second open tab racing this one is less likely to show it twice.
        writeLastShown({ at: Date.now(), ids });
        const notification = new Notification(
          `${due.length} ${due.length === 1 ? "deadline needs" : "deadlines need"} attention`,
          { body, tag: "contractlens-due" },
        );
        notification.onclick = () => {
          window.focus();
          notification.close();
          if (window.location.pathname !== "/") window.location.assign("/");
        };
      } catch {
        // Offline, signed out, or notifications blocked: try again at the next check.
      } finally {
        busy.current = false;
      }
    }

    void check();
    const timer = window.setInterval(() => void check(), CHECK_EVERY_MS);
    const onFocus = () => void check();
    window.addEventListener("focus", onFocus);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [enabled]);
}
