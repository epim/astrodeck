// lib/base.ts — base-path awareness so the SPA works BOTH at the server root (`/`,
// local LAN) and tunnelled under the relay at `/h/<home_id>/`. The app has no URL
// router (views are internal Zustand state), so runtime API/WS/auth URLs are
// prefixed with the mount base here (they would otherwise be absolute and 404 at
// the relay root).
//
// Vite is built with `base: "./"` so the index.html assets are relative and load
// under either mount; this helper prefixes the same base onto the runtime URLs.
//
// ROOT CAUSE of the live "Couldn't load drivers — Unexpected token '<', <!doctype…"
// bug (2026-07-19): the base used to be `window.location.pathname` verbatim. But the
// relay serves the SPA HTML for ANY unmatched deep path (a client-side "route"), so
// the loaded pathname can be DEEPER than the mount root — a shared/bookmarked deep
// link, or a reload at a sub-path, lands the SPA at e.g. `/h/home-1/equipment`.
// Taking the whole pathname as the base then prefixed every API URL too deeply
// (`/h/home-1/equipment/api/drivers`); the relay tunnels that as `/equipment/api/
// drivers`, a non-`/api` path the home answers from its SPA catch-all as index.html
// (200 text/html), so `res.json()` blew up on the HTML. It was BROWSER-ONLY (curl
// hits the literal correct path) and intermittent (only when the load pathname was
// deep). The mount is ALWAYS the `/h/<home_id>` prefix — pin to that, not the
// current location depth.

/**
 * Derive the relay mount base from a location pathname. Under the relay the whole
 * app is mounted at `/h/<home_id>/` — the only prefix the relay adds — and anything
 * after it is in-app state, not part of the mount. So take the `/h/<home_id>` prefix
 * only; any deeper segments (a deep link / reload) are ignored. Locally the app is
 * root-mounted, so a non-`/h/...` pathname yields "" (root).
 */
export function deriveBase(pathname: string): string {
  const m = pathname.match(/^\/h\/[^/]+/);
  return m ? m[0] : "";
}

export const BASE: string = deriveBase(window.location.pathname);

/** Prefix an absolute app path (e.g. "/api/me") with the mount base. */
export const u = (path: string): string => BASE + path;
