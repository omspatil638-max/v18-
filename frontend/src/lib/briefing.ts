const FALLBACK_TIMEZONES = [
  "UTC",
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "America/Sao_Paulo",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Africa/Johannesburg",
  "Asia/Dubai",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
  "Pacific/Auckland",
];

export const DEFAULT_BRIEFING_HOUR = 8;

/** This browser's IANA time zone, or "UTC" when it cannot say. */
export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Every time zone the browser knows, or a short fallback list; `include` is always in the result. */
export function timezoneOptions(include: string[] = []): string[] {
  let zones: string[] = FALLBACK_TIMEZONES;
  try {
    const supported = Intl.supportedValuesOf?.("timeZone");
    if (supported && supported.length > 0) zones = supported;
  } catch {
    // Older browsers: the fallback list is used.
  }
  const all = new Set(zones);
  all.add("UTC");
  for (const z of include) if (z) all.add(z);
  return Array.from(all).sort((a, b) => a.localeCompare(b));
}

/** "8:00 AM" or "08:00", following the visitor's locale. */
export function hourLabel(hour: number): string {
  try {
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(
      new Date(2000, 0, 1, hour, 0),
    );
  } catch {
    return `${String(hour).padStart(2, "0")}:00`;
  }
}

export const HOURS: number[] = Array.from({ length: 24 }, (_, h) => h);
