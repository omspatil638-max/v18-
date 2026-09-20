"use client";

import { useEffect, useId, useRef, useState } from "react";
import { BellRing, Info, Send, X } from "lucide-react";
import {
  errorMessage,
  fetchAlertSettings,
  fetchPushPublicKey,
  sendTestEmail,
  sendTestPush,
  subscribePush,
  unsubscribePush,
  updateAlertSettings,
} from "@/lib/api";
import { currentSubscription, isPushSupported, subscribeBrowser, unsubscribeBrowser } from "@/lib/notifications";
import type { AlertSettings } from "@/lib/types";

const REPEAT_HOUR_CHOICES = [6, 12, 24, 48, 72];

const MAX_LEAD_TIMES = 6;
const MIN_DAYS = 1;
const MAX_DAYS = 365;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function sortedUnique(values: number[]): number[] {
  return Array.from(new Set(values)).sort((a, b) => b - a);
}

/** Validates the "add a lead time" input. Returns the number or an error message. */
function parseLeadTime(raw: string, existing: number[]): { value: number } | { error: string } {
  const text = raw.trim();
  if (!text) return { error: "Enter a number of days." };
  if (!/^\d+$/.test(text)) return { error: "Use a whole number of days." };
  const value = Number(text);
  if (value < MIN_DAYS || value > MAX_DAYS) return { error: `Use a number between ${MIN_DAYS} and ${MAX_DAYS}.` };
  if (existing.includes(value)) return { error: `${value} days is already in the list.` };
  if (existing.length >= MAX_LEAD_TIMES) return { error: `You can have at most ${MAX_LEAD_TIMES} lead times.` };
  return { value };
}

export function AlertSettingsDialog({ onClose }: { onClose: () => void }) {
  const uid = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const firstFieldRef = useRef<HTMLInputElement | null>(null);

  const [settings, setSettings] = useState<AlertSettings | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [leadDays, setLeadDays] = useState<number[]>([]);
  const [leadInput, setLeadInput] = useState("");
  const [leadError, setLeadError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [repeatEnabled, setRepeatEnabled] = useState(true);
  const [repeatHours, setRepeatHours] = useState(24);

  const pushSupported = isPushSupported();
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">(
    pushSupported ? Notification.permission : "unsupported",
  );
  const [browserSubscribed, setBrowserSubscribed] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [pushMessage, setPushMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [pushTesting, setPushTesting] = useState(false);
  const [pushTestResult, setPushTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; text: string } | null>(null);

  // Remember what had focus so it can be restored on close; move focus into the dialog.
  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialogRef.current?.focus();
    return () => {
      previouslyFocused?.focus();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetchAlertSettings()
      .then((s) => {
        if (cancelled) return;
        setSettings(s);
        setLeadDays(sortedUnique(s.lead_days));
        setEmail(s.email_to ?? "");
        setEmailEnabled(s.email_enabled);
        setRepeatEnabled(s.repeat_enabled);
        setRepeatHours(s.repeat_hours);
      })
      .catch((e: unknown) => {
        if (!cancelled) setLoadError(errorMessage(e, "Could not load reminder settings."));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Is this browser already registered for push?
  useEffect(() => {
    let cancelled = false;
    currentSubscription()
      .then((sub) => {
        if (!cancelled) setBrowserSubscribed(sub !== null);
      })
      .catch(() => {
        if (!cancelled) setBrowserSubscribed(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Once loaded, focus the first field.
  useEffect(() => {
    if (settings) firstFieldRef.current?.focus();
  }, [settings]);

  function handleKeyDown(e: React.KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab") return;
    const nodes = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    if (nodes.length === 0) {
      e.preventDefault();
      return;
    }
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || active === dialogRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  function addLeadTime() {
    const parsed = parseLeadTime(leadInput, leadDays);
    if ("error" in parsed) {
      setLeadError(parsed.error);
      return;
    }
    setLeadError(null);
    setLeadDays((prev) => sortedUnique([...prev, parsed.value]));
    setLeadInput("");
    setSaved(false);
  }

  function removeLeadTime(value: number) {
    setLeadDays((prev) => prev.filter((d) => d !== value));
    setLeadError(null);
    setSaved(false);
  }

  const trimmedEmail = email.trim();
  const emailInvalid = trimmedEmail !== "" && !EMAIL_PATTERN.test(trimmedEmail);
  const canEnableEmail = trimmedEmail !== "" && !emailInvalid;
  const savedEmail = settings?.email_to ?? null;

  async function handleSave() {
    setSaveError(null);
    setSaved(false);
    if (leadDays.length === 0) {
      setSaveError("Add at least one lead time.");
      return;
    }
    if (emailInvalid) {
      setSaveError("That email address does not look valid.");
      return;
    }
    if (emailEnabled && !canEnableEmail) {
      setSaveError("Add an email address before turning on email alerts.");
      return;
    }
    setSaving(true);
    try {
      const updated = await updateAlertSettings({
        lead_days: leadDays,
        email_to: trimmedEmail,
        email_enabled: emailEnabled,
        repeat_enabled: repeatEnabled,
        repeat_hours: repeatHours,
      });
      setSettings(updated);
      setLeadDays(sortedUnique(updated.lead_days));
      setEmail(updated.email_to ?? "");
      setEmailEnabled(updated.email_enabled);
      setRepeatEnabled(updated.repeat_enabled);
      setRepeatHours(updated.repeat_hours);
      setSaved(true);
    } catch (e: unknown) {
      setSaveError(errorMessage(e, "Could not save reminder settings."));
    } finally {
      setSaving(false);
    }
  }

  async function handleTestEmail() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await sendTestEmail();
      setTestResult({ ok: true, text: res?.message || "Test email sent." });
    } catch (e: unknown) {
      setTestResult({ ok: false, text: errorMessage(e, "Could not send the test email.") });
    } finally {
      setTesting(false);
    }
  }

  async function enablePush() {
    setPushMessage(null);
    setPushTestResult(null);
    if (permission === "denied") {
      setPushMessage({
        ok: false,
        text: "Notifications are blocked for this site. Click the lock or site-settings icon next to the address bar, set Notifications to Allow, then try again.",
      });
      return;
    }
    setPushBusy(true);
    try {
      if (permission !== "granted") {
        const result = await Notification.requestPermission();
        setPermission(result);
        if (result !== "granted") {
          setPushMessage({
            ok: false,
            text:
              result === "denied"
                ? "Permission was denied, so browser notifications stay off. To change your mind, allow Notifications for this site in the browser's site settings."
                : "Permission was not given, so browser notifications stay off. You can try again any time.",
          });
          return;
        }
      }
      const { public_key: publicKey } = await fetchPushPublicKey();
      if (!publicKey) {
        setPushMessage({ ok: false, text: "The server has no push key configured, so notifications can't be turned on." });
        return;
      }
      const subscription = await subscribeBrowser(publicKey);
      await subscribePush(subscription);
      const updated = await updateAlertSettings({ push_enabled: true });
      setSettings(updated);
      setBrowserSubscribed(true);
      setPushMessage({ ok: true, text: "Browser notifications are on for this browser." });
    } catch (e: unknown) {
      setPushMessage({ ok: false, text: errorMessage(e, "Could not turn on browser notifications.") });
    } finally {
      setPushBusy(false);
    }
  }

  async function disablePush() {
    setPushMessage(null);
    setPushTestResult(null);
    setPushBusy(true);
    try {
      const sub = await currentSubscription();
      if (sub) await unsubscribePush(sub.endpoint);
      await unsubscribeBrowser();
      const updated = await updateAlertSettings({ push_enabled: false });
      setSettings(updated);
      setBrowserSubscribed(false);
      setPushMessage({ ok: true, text: "Browser notifications are off." });
    } catch (e: unknown) {
      setPushMessage({ ok: false, text: errorMessage(e, "Could not turn off browser notifications.") });
    } finally {
      setPushBusy(false);
    }
  }

  async function handleTestPush() {
    setPushTesting(true);
    setPushTestResult(null);
    try {
      const res = await sendTestPush();
      setPushTestResult({ ok: true, text: res?.message || "Test notification sent." });
    } catch (e: unknown) {
      setPushTestResult({ ok: false, text: errorMessage(e, "Could not send the test notification.") });
    } finally {
      setPushTesting(false);
    }
  }

  const repeatChoices = Array.from(new Set([...REPEAT_HOUR_CHOICES, repeatHours])).sort((a, b) => a - b);
  const emailDirty = trimmedEmail !== (savedEmail ?? "");
  const titleId = `${uid}-title`;
  const descId = `${uid}-desc`;
  const inputClass =
    "w-full px-3 py-2 text-sm rounded-lg border border-slate-300 bg-white text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 disabled:bg-slate-100 disabled:text-slate-500";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/60"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className="w-full max-w-lg max-h-[90vh] overflow-y-auto bg-white rounded-2xl shadow-2xl border border-slate-200 focus:outline-none"
      >
        <div className="flex items-start justify-between gap-4 p-5 border-b border-slate-100">
          <div>
            <h2 id={titleId} className="text-base font-bold text-slate-900">
              Reminder settings
            </h2>
            <p id={descId} className="text-xs text-slate-500 mt-0.5">
              Choose how many days before a deadline you want to be alerted. Alerts are reminders, not legal advice.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close reminder settings"
            className="p-1.5 rounded-lg text-slate-500 hover:text-slate-900 hover:bg-slate-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            <X className="w-4 h-4" aria-hidden="true" />
          </button>
        </div>

        <div className="p-5 space-y-6">
          {loadError ? (
            <p role="alert" className="text-sm text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {loadError}
            </p>
          ) : !settings ? (
            <p role="status" className="text-sm text-slate-500 text-center py-6">
              Loading settings...
            </p>
          ) : (
            <>
              {/* Lead times */}
              <fieldset className="space-y-2">
                <legend className="text-xs font-bold uppercase tracking-wider text-slate-500">Lead times</legend>
                <p className="text-xs text-slate-500">
                  Alert me this many days before a deadline (up to {MAX_LEAD_TIMES}). Default:{" "}
                  {settings.default_lead_days.length > 0 ? settings.default_lead_days.join(", ") : "none"} days.
                </p>
                <ul className="flex flex-wrap gap-2" aria-label="Current lead times">
                  {leadDays.length === 0 && <li className="text-xs text-slate-500 italic">No lead times set.</li>}
                  {leadDays.map((d) => (
                    <li
                      key={d}
                      className="inline-flex items-center gap-1 pl-3 pr-1 py-1 rounded-full bg-blue-50 border border-blue-200 text-xs font-semibold text-blue-900"
                    >
                      {d} {d === 1 ? "day" : "days"}
                      <button
                        type="button"
                        onClick={() => removeLeadTime(d)}
                        aria-label={`Remove ${d} day lead time`}
                        className="p-0.5 rounded-full hover:bg-blue-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                      >
                        <X className="w-3 h-3" aria-hidden="true" />
                      </button>
                    </li>
                  ))}
                </ul>
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <label htmlFor={`${uid}-lead`} className="sr-only">
                      Add a lead time in days
                    </label>
                    <input
                      ref={firstFieldRef}
                      id={`${uid}-lead`}
                      type="text"
                      inputMode="numeric"
                      value={leadInput}
                      onChange={(e) => {
                        setLeadInput(e.target.value);
                        setLeadError(null);
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") {
                          e.preventDefault();
                          addLeadTime();
                        }
                      }}
                      placeholder={`Days (${MIN_DAYS}-${MAX_DAYS})`}
                      aria-invalid={leadError ? true : undefined}
                      aria-describedby={leadError ? `${uid}-lead-err` : undefined}
                      disabled={leadDays.length >= MAX_LEAD_TIMES}
                      className={inputClass}
                    />
                  </div>
                  <button
                    type="button"
                    onClick={addLeadTime}
                    disabled={leadDays.length >= MAX_LEAD_TIMES}
                    className="px-3 py-2 text-xs font-bold rounded-lg bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                  >
                    Add
                  </button>
                </div>
                {leadError && (
                  <p id={`${uid}-lead-err`} role="alert" className="text-xs text-red-700">
                    {leadError}
                  </p>
                )}
              </fieldset>

              {/* Email */}
              <fieldset className="space-y-3">
                <legend className="text-xs font-bold uppercase tracking-wider text-slate-500">Email</legend>
                {!settings.smtp_configured && (
                  <div className="flex items-start gap-2 text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
                    <Info className="w-4 h-4 shrink-0 mt-0.5 text-slate-500" aria-hidden="true" />
                    <p>
                      Email is not set up on the server yet (run Setup-Email.cmd in the project folder, then restart).
                      In-app alerts still work.
                    </p>
                  </div>
                )}
                <div className="space-y-1">
                  <label htmlFor={`${uid}-email`} className="text-xs font-semibold text-slate-700">
                    Email address
                  </label>
                  <input
                    id={`${uid}-email`}
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(e) => {
                      setEmail(e.target.value);
                      setSaved(false);
                      setTestResult(null);
                      if (e.target.value.trim() === "") setEmailEnabled(false);
                    }}
                    placeholder="you@example.com"
                    aria-invalid={emailInvalid ? true : undefined}
                    aria-describedby={emailInvalid ? `${uid}-email-err` : undefined}
                    className={inputClass}
                  />
                  {emailInvalid && (
                    <p id={`${uid}-email-err`} className="text-xs text-red-700">
                      That email address does not look valid.
                    </p>
                  )}
                </div>

                <div className="flex items-start gap-3">
                  <input
                    id={`${uid}-enabled`}
                    type="checkbox"
                    checked={emailEnabled}
                    disabled={!canEnableEmail}
                    onChange={(e) => {
                      setEmailEnabled(e.target.checked);
                      setSaved(false);
                    }}
                    aria-describedby={!canEnableEmail ? `${uid}-enabled-hint` : undefined}
                    className="mt-0.5 h-4 w-4 rounded border-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                  />
                  <div>
                    <label htmlFor={`${uid}-enabled`} className="text-sm font-semibold text-slate-800">
                      Email me due alerts
                    </label>
                    {!canEnableEmail && (
                      <p id={`${uid}-enabled-hint`} className="text-xs text-slate-500">
                        Add a valid email address to turn this on.
                      </p>
                    )}
                  </div>
                </div>

                <div className="space-y-1">
                  <button
                    type="button"
                    onClick={handleTestEmail}
                    disabled={testing || !savedEmail || emailDirty}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                  >
                    <Send className="w-3.5 h-3.5" aria-hidden="true" />
                    {testing ? "Sending..." : "Send test email"}
                  </button>
                  {(!savedEmail || emailDirty) && (
                    <p className="text-xs text-slate-500">Save your email address first, then send a test.</p>
                  )}
                  <div aria-live="polite">
                    {testResult && (
                      <p
                        className={`text-xs rounded-lg px-3 py-2 border ${
                          testResult.ok
                            ? "text-green-900 bg-green-50 border-green-200"
                            : "text-slate-800 bg-amber-50 border-amber-200"
                        }`}
                      >
                        {testResult.text}
                      </p>
                    )}
                  </div>
                </div>
              </fieldset>

              {/* Repeat reminders */}
              <fieldset className="space-y-3">
                <legend className="text-xs font-bold uppercase tracking-wider text-slate-500">Repeat reminders</legend>
                <div className="flex items-start gap-3">
                  <input
                    id={`${uid}-repeat`}
                    type="checkbox"
                    checked={repeatEnabled}
                    onChange={(e) => {
                      setRepeatEnabled(e.target.checked);
                      setSaved(false);
                    }}
                    className="mt-0.5 h-4 w-4 rounded border-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                  />
                  <label htmlFor={`${uid}-repeat`} className="text-sm font-semibold text-slate-800">
                    If I haven&apos;t marked it read, remind me again
                  </label>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <label htmlFor={`${uid}-repeat-hours`} className="text-xs font-semibold text-slate-700">
                    Remind me every
                  </label>
                  <select
                    id={`${uid}-repeat-hours`}
                    value={repeatHours}
                    disabled={!repeatEnabled}
                    onChange={(e) => {
                      setRepeatHours(Number(e.target.value));
                      setSaved(false);
                    }}
                    className="px-3 py-2 text-sm rounded-lg border border-slate-300 bg-white text-slate-900 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 disabled:bg-slate-100 disabled:text-slate-500"
                  >
                    {repeatChoices.map((h) => (
                      <option key={h} value={h}>
                        {h} hours
                      </option>
                    ))}
                  </select>
                </div>
                <p className="text-xs text-slate-500">
                  Reminders stop as soon as the alert is marked read, or after {settings.max_repeats} reminders, whichever
                  comes first. They apply to email and browser notifications.
                </p>
              </fieldset>

              {/* Browser notifications */}
              <fieldset className="space-y-3">
                <legend className="text-xs font-bold uppercase tracking-wider text-slate-500">Browser notifications</legend>
                {!settings.push_available ? (
                  <p className="text-xs text-slate-500">
                    Push notifications are not set up on the server yet (run Setup-Notifications.cmd in the project
                    folder, then restart).
                  </p>
                ) : !pushSupported ? (
                  <p className="text-xs text-slate-500">
                    This browser can&apos;t show push notifications (it may lack support, or this page may need to be
                    opened over https or on localhost).
                  </p>
                ) : (
                  <>
                    <p id={`${uid}-push-hint`} className="text-xs text-slate-500">
                      When you turn this on, your browser will ask for permission to show notifications. They work even
                      when ContractLens is closed, as long as your browser is running.
                    </p>
                    <div className="flex items-start gap-3">
                      <input
                        id={`${uid}-push`}
                        type="checkbox"
                        checked={settings.push_enabled}
                        disabled={pushBusy}
                        onChange={(e) => {
                          if (e.target.checked) void enablePush();
                          else void disablePush();
                        }}
                        aria-describedby={`${uid}-push-hint`}
                        className="mt-0.5 h-4 w-4 rounded border-slate-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600 disabled:opacity-50"
                      />
                      <div>
                        <label htmlFor={`${uid}-push`} className="text-sm font-semibold text-slate-800">
                          Send me browser notifications
                        </label>
                        <p className="text-xs text-slate-500">
                          {settings.push_subscriptions}{" "}
                          {settings.push_subscriptions === 1 ? "browser is" : "browsers are"} registered
                          {settings.push_enabled ? (browserSubscribed ? "; this one is included." : "; this one is not.") : "."}
                        </p>
                      </div>
                    </div>
                    {permission === "denied" && (
                      <p className="text-xs text-amber-900 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
                        Notifications are blocked for this site in your browser. Allow them in the site settings (the icon
                        next to the address bar), then turn this on.
                      </p>
                    )}
                    {settings.push_enabled && !browserSubscribed && permission !== "denied" && (
                      <button
                        type="button"
                        onClick={() => void enablePush()}
                        disabled={pushBusy}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                      >
                        <BellRing className="w-3.5 h-3.5" aria-hidden="true" />
                        Also use this browser
                      </button>
                    )}
                    <div aria-live="polite">
                      {pushBusy && (
                        <p role="status" className="text-xs text-slate-500">
                          Working...
                        </p>
                      )}
                      {pushMessage && (
                        <p
                          className={`text-xs rounded-lg px-3 py-2 border ${
                            pushMessage.ok
                              ? "text-green-900 bg-green-50 border-green-200"
                              : "text-slate-800 bg-amber-50 border-amber-200"
                          }`}
                        >
                          {pushMessage.text}
                        </p>
                      )}
                    </div>
                    <div className="space-y-1">
                      <button
                        type="button"
                        onClick={handleTestPush}
                        disabled={pushTesting || pushBusy || !settings.push_enabled}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-bold rounded-lg bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
                      >
                        <Send className="w-3.5 h-3.5" aria-hidden="true" />
                        {pushTesting ? "Sending..." : "Send test notification"}
                      </button>
                      <div aria-live="polite">
                        {pushTestResult && (
                          <p
                            className={`text-xs rounded-lg px-3 py-2 border ${
                              pushTestResult.ok
                                ? "text-green-900 bg-green-50 border-green-200"
                                : "text-slate-800 bg-amber-50 border-amber-200"
                            }`}
                          >
                            {pushTestResult.text}
                          </p>
                        )}
                      </div>
                    </div>
                  </>
                )}
              </fieldset>

              <div aria-live="polite" className="min-h-[1rem]">
                {saveError && (
                  <p role="alert" className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                    {saveError}
                  </p>
                )}
                {saved && !saveError && (
                  <p className="text-xs text-green-900 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
                    Settings saved.
                  </p>
                )}
              </div>
            </>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-2 p-5 border-t border-slate-100">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-bold rounded-xl bg-white border border-slate-300 text-slate-800 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-blue-600"
          >
            Close
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !settings}
            className="px-4 py-2 text-xs font-bold rounded-xl bg-blue-600 hover:bg-blue-700 text-white disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
