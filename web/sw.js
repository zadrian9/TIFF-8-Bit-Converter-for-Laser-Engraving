/**
 * Service Worker for TIFF 8-Bit Converter PWA
 * Caches app shell and Pyodide WebAssembly runtime for offline execution
 */

const PREFIX = "tiff-converter-pwa-";
const CACHE_NAME = PREFIX + "v1.2";
const APP_SHELL = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./worker.js",
  "./converter_core.py",
  "./manifest.json",
  "./vendor/jszip.min.js",
  "./icons/icon.svg",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/icon-maskable-512.png",
  "./icons/apple-touch-icon-180.png"
];

const RUNTIME_HOSTS = new Set([
  "cdn.jsdelivr.net",
  "pypi.org",
  "files.pythonhosted.org"
]);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith(PREFIX) && key !== CACHE_NAME)
            .map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  const sameOrigin = url.origin === location.origin;
  if (!sameOrigin && !RUNTIME_HOSTS.has(url.hostname)) return;

  // Same-origin navigations: network-first with cached shell fallback
  if (sameOrigin && req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((networkResponse) => {
          if (networkResponse && networkResponse.ok) {
            const copy = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put("./index.html", copy));
          }
          return networkResponse;
        })
        .catch(() => caches.match("./index.html"))
    );
    return;
  }

  // Other same-origin & allowed Pyodide/PyPI assets: cache-first with background refresh for same-origin
  event.respondWith(
    caches.match(req).then((cachedResponse) => {
      if (cachedResponse) {
        if (sameOrigin) {
          fetch(req)
            .then((networkResponse) => {
              if (networkResponse && networkResponse.ok) {
                caches.open(CACHE_NAME).then((cache) => cache.put(req, networkResponse));
              }
            })
            .catch(() => {});
        }
        return cachedResponse;
      }

      return fetch(req).then((networkResponse) => {
        if (
          networkResponse &&
          (networkResponse.ok || (!sameOrigin && networkResponse.type === "opaque"))
        ) {
          const responseToCache = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(req, responseToCache));
        }
        return networkResponse;
      });
    })
  );
});
