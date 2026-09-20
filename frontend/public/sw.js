/* ContractLens service worker: shows push notifications for due deadline alerts. No caching. */

self.addEventListener("install", function () {
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("push", function (event) {
  var payload = {};
  try {
    if (event.data) payload = event.data.json();
  } catch (e) {
    try {
      payload = { body: event.data.text() };
    } catch (e2) {
      payload = {};
    }
  }
  if (typeof payload !== "object" || payload === null) payload = {};

  var title = typeof payload.title === "string" && payload.title ? payload.title : "ContractLens";
  var body = typeof payload.body === "string" ? payload.body : "A deadline needs your attention.";
  var url = typeof payload.url === "string" && payload.url ? payload.url : "/";
  var tag = typeof payload.tag === "string" && payload.tag ? payload.tag : "contractlens-alert";

  event.waitUntil(
    self.registration.showNotification(title, {
      body: body,
      tag: tag,
      renotify: true,
      data: { url: url },
    })
  );
});

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var data = event.notification.data;
  var target = data && typeof data.url === "string" && data.url ? data.url : "/";
  var absolute;
  try {
    absolute = new URL(target, self.location.origin);
  } catch (e) {
    absolute = new URL("/", self.location.origin);
  }
  // Only ever open pages on this app's own origin.
  if (absolute.origin !== self.location.origin) absolute = new URL("/", self.location.origin);

  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windows) {
      for (var i = 0; i < windows.length; i++) {
        var client = windows[i];
        if (new URL(client.url).origin === self.location.origin && "focus" in client) {
          return client.focus().then(function (focused) {
            if (focused && "navigate" in focused) return focused.navigate(absolute.href);
            return undefined;
          });
        }
      }
      return self.clients.openWindow(absolute.href);
    })
  );
});
