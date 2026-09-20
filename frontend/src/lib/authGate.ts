import { fetchAuthConfig } from "./api";
import type { AuthConfig } from "./types";

/**
 * Session cache for the sign-up gate in AppShell, so moving between pages does not ask the server every time.
 * Module state lives only until the page is reloaded.
 */
let configPromise: Promise<AuthConfig> | null = null;
let onboarded = false;

/** The auth config, fetched once per page load. A failed fetch is not cached, so the next call retries. */
export function getAuthConfig(): Promise<AuthConfig> {
  if (!configPromise) {
    configPromise = fetchAuthConfig().catch((e: unknown) => {
      configPromise = null;
      throw e;
    });
  }
  return configPromise;
}

/** Forget the cached config (after sign-up the server mode changes from "setup" to "login"). */
export function resetAuthGate(): void {
  configPromise = null;
  onboarded = false;
}

export function isKnownOnboarded(): boolean {
  return onboarded;
}

export function markOnboarded(): void {
  onboarded = true;
}
