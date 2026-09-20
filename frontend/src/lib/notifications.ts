import type { PushSubscriptionPayload } from "./types";

const SW_URL = "/sw.js";

/** True when this browser (and address) can register a service worker and use web push. */
export function isPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "Notification" in window &&
    "serviceWorker" in navigator &&
    "PushManager" in window
  );
}

/** Decodes a base64url string (as used for VAPID public keys) into bytes. */
export function urlBase64ToUint8Array(base64Url: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64Url.length % 4)) % 4);
  const base64 = (base64Url + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

function sameBytes(a: ArrayBuffer | null, b: Uint8Array): boolean {
  if (!a || a.byteLength !== b.length) return false;
  const view = new Uint8Array(a);
  return view.every((v, i) => v === b[i]);
}

/** This browser's existing push subscription, or null (also null when push is unsupported or no worker is registered). */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  const registration = await navigator.serviceWorker.getRegistration();
  if (!registration) return null;
  return registration.pushManager.getSubscription();
}

/**
 * Registers the service worker and subscribes this browser to push with the server's public key.
 * Notification permission must already be granted. Throws an Error with a readable message on failure.
 */
export async function subscribeBrowser(publicKey: string): Promise<PushSubscriptionPayload> {
  if (!isPushSupported()) throw new Error("This browser does not support push notifications.");
  const key = urlBase64ToUint8Array(publicKey);
  await navigator.serviceWorker.register(SW_URL);
  const registration = await navigator.serviceWorker.ready;

  let subscription = await registration.pushManager.getSubscription();
  // A subscription made with a different server key can't be reused.
  if (subscription && !sameBytes(subscription.options.applicationServerKey, key)) {
    await subscription.unsubscribe();
    subscription = null;
  }
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  }

  const json = subscription.toJSON();
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!json.endpoint || !p256dh || !auth) {
    throw new Error("The browser returned an incomplete push subscription.");
  }
  return { endpoint: json.endpoint, keys: { p256dh, auth } };
}

/** Removes this browser's push subscription. Resolves to its endpoint, or null when there was none. */
export async function unsubscribeBrowser(): Promise<string | null> {
  const subscription = await currentSubscription();
  if (!subscription) return null;
  const endpoint = subscription.endpoint;
  await subscription.unsubscribe();
  return endpoint;
}
