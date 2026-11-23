/**
 * Service Worker for TIFF 8-Bit Converter PWA
 * Caches app shell and Pyodide WebAssembly runtime for offline execution
 */

const CACHE_NAME = "tiff-converter-pwa-v1.1";
const APP_SHELL = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./worker.js",
  "./converter_core.py",
  "./manifest.json",
  "./icons/icon.svg"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(APP_SHELL);
    }).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Cache Pyodide CDN packages and app files
  if (
    url.origin === location.origin ||
    url.hostname.includes("jsdelivr.net") ||
    url.hostname.includes("cloudflare.com") ||
    url.hostname.includes("pypi.org") ||
    url.hostname.includes("pythonhosted.org")
  ) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        if (cachedResponse) {
          // Fetch updated version in background for local shell files
          if (url.origin === location.origin) {
            fetch(event.request)
              .then((networkResponse) => {
                if (networkResponse && networkResponse.status === 200) {
                  caches.open(CACHE_NAME).then((cache) => {
                    cache.put(event.request, networkResponse);
                  });
                }
              })
              .catch(() => {});
          }
          return cachedResponse;
        }

        return fetch(event.request).then((networkResponse) => {
          if (
            networkResponse &&
            (networkResponse.status === 200 || networkResponse.type === "opaque")
          ) {
            const responseToCache = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(event.request, responseToCache);
            });
          }
          return networkResponse;
        });
      })
    );
  }
});
