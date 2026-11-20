/**
 * Offline shell for the kiosk.
 *
 * The terminal service runs on the kiosk itself, so the usual outage is the internet, which this does not touch. This
 * covers the other one: the page asked for while the service is restarting or the browser was reopened with no
 * network. The app and the things it needs to draw (copy, the plan, the flight board, the voice vocabulary) load
 * from the last good copy. Journeys, assistance, staff actions, voice sockets and the clock are never cached: a
 * stale answer to those would be worse than no answer.
 */
const SHELL = "sa-shell-v3";
const RUNTIME = "sa-runtime-v3";
const PRECACHE = ["/", "/staff/", "/voice/capture-worklet.js", "/icon.svg"];
const NEVER = [/^\/journeys/, /^\/staff\/cases/, /^\/demo/, /^\/system/, /^\/voice\/(stream|speak|status)/, /^\/route\//, /^\/clock/, /^\/traffic/];
const RUNTIME_OK = [/^\/i18n\//, /^\/map\/layout/, /^\/flights/, /^\/voice\/vocabulary/];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL).then((cache) => Promise.allSettled(PRECACHE.map((url) => cache.add(url)))).then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("sa-") && k !== SHELL && k !== RUNTIME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function cacheFirst(request) {
  const hit = await caches.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok) (await caches.open(SHELL)).put(request, response.clone());
  return response;
}

async function networkFirst(request, bucket, fallbackPage) {
  try {
    const response = await fetch(request);
    if (response.ok) (await caches.open(bucket)).put(request, response.clone());
    return response;
  } catch (error) {
    const hit = (await caches.match(request)) || (await caches.match(request, { ignoreSearch: true }));
    if (hit) return hit;
    if (fallbackPage) {
      const page = await caches.match(fallbackPage);
      if (page) return page;
    }
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  const path = url.pathname;
  if (NEVER.some((pattern) => pattern.test(path))) return;
  if (path.startsWith("/_next/static/")) {
    event.respondWith(cacheFirst(request));
  } else if (request.mode === "navigate") {
    event.respondWith(networkFirst(request, SHELL, path.startsWith("/staff") ? "/staff/" : "/"));
  } else if (path === "/voice/capture-worklet.js" || RUNTIME_OK.some((pattern) => pattern.test(path))) {
    event.respondWith(networkFirst(request, RUNTIME, null));
  }
});
