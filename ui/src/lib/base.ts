// lib/base.ts — base-path awareness so the SPA works BOTH at the server root (`/`,
// local LAN) and tunnelled under the relay at `/h/<home_id>/`. The app has no
// URL routing (views are internal Zustand state), so the entry path IS the mount
// root: empty locally, `/h/home-1` under the relay. Computed ONCE at load.
//
// Vite is built with `base: "./"` so the index.html assets are relative and load
// under either mount; this helper prefixes the same base onto runtime API/WS/auth
// URLs (which would otherwise be absolute and 404 at the relay root).

export const BASE: string = window.location.pathname.replace(/\/+$/, "");

/** Prefix an absolute app path (e.g. "/api/me") with the mount base. */
export const u = (path: string): string => BASE + path;
