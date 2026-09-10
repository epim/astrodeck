/* AstroDeck service worker (next/ARCHITECTURE.md section 12, S4).
 *
 * WHAT IT IS FOR: the app shell opens on a phone that has just walked out of
 * WiFi range, so the tab shows the UI and its own "no link" state instead of
 * the browser's dinosaur. It is NOT an offline mode - nothing about a rig can be
 * served from a cache.
 *
 * WHAT IT MUST NEVER CACHE: /api, /ws and /auth. A cached rig status is a lie
 * with a timestamp on it, a cached /api/me is a stale identity, and a cached
 * POST response would be a command that looks like it landed. Those are passed
 * straight through, and a failure is a failure.
 *
 * Navigations are network-first with a cached shell as the fallback; hashed
 * build assets under ./assets/ are cache-first, because their names change with
 * their contents and a hit is never stale.
 *
 * CACHE_VERSION IS BUMPED BY HAND. Vite's `define` does not reach this file (it
 * is copied verbatim out of public/), so there is no __APP_VERSION__ here to
 * read. Bump it when the caching RULES change; the hashed asset names already
 * handle a normal release, and `activate` deletes every cache that is not the
 * current one, so a bump is always safe.
 *
 * The two manifest icons are NOT hand-drawn binaries with no source. Regenerate
 * them from the repo root with the standard-library script beside the shell
 * (no Pillow, no canvas, no node build step):
 *
 *     python ui/src/next/shell/make-icons.py
 */

const CACHE_VERSION = "astrodeck-shell-v1";
const SHELL = ["./", "./index.html", "./manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION)
      .then((c) => c.addAll(SHELL))
      // A shell that cannot be pre-cached is not a reason to refuse to install:
      // the runtime handlers below fill the cache on the first successful load.
      .catch(() => undefined)
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)),
      ))
      .then(() => self.clients.claim()),
  );
});

function isPassThrough(url) {
  const p = url.pathname;
  // The relay mounts the app under /h/<home_id>/, so the API prefix is matched
  // anywhere in the path rather than only at its start.
  return p.includes("/api/") || p.endsWith("/api")
    || p.includes("/ws") || p.includes("/auth/") || p.endsWith("/auth")
    || p.includes("/healthz");
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                 // never a command

  let url;
  try { url = new URL(req.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;  // tiles, fonts: not ours
  if (isPassThrough(url)) return;

  // Navigations: network first, cached shell second. A stale shell that boots
  // and says "no link" beats a browser error page, because the first tells the
  // user the rig is still imaging and the second does not.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE_VERSION).then((c) => c.put("./index.html", copy)).catch(() => undefined);
          return res;
        })
        .catch(() => caches.match("./index.html").then((hit) => hit || caches.match("./"))),
    );
    return;
  }

  // Hashed build output: cache first. The filename changes when the bytes do.
  if (url.pathname.includes("/assets/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res && res.ok) {
          const copy = res.clone();
          caches.open(CACHE_VERSION).then((c) => c.put(req, copy)).catch(() => undefined);
        }
        return res;
      })),
    );
  }
});
