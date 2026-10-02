// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// router.ts - the hash router for the new front end (ARCHITECTURE.md section 3).
//
// Grammar:  #/<hub>[/<sub>][/<sheet>[/<sheet2>]][?k=v&k2=v2]
//
// Why the hash and not the History API: the SPA is served from the rig at "/"
// on the LAN and from "/h/<home_id>/" through the relay, and vite builds it
// with base "./". A path router would have to learn its own mount point at
// runtime and would 404 on a hard reload behind any server that does not
// rewrite unknown paths to index.html. The hash is mount-point-free, survives
// the reload, and is what the legacy UI's absence of a router leaves free.
//
// Sheets live IN the route, so the browser's own Back button closes one and a
// sheet can be deep-linked (support asks "open #/rig/devices/camera and read me
// the setpoint"). Hub changes and sheet opens PUSH a history entry; a sub-nav
// change REPLACES, because flipping between NOW / GALLERY / FLOWS is looking
// around one screen, not travelling, and eight taps of it should not cost eight
// presses of Back to leave.

import { useSyncExternalStore } from "react";

export type HubId = "sky" | "weather" | "session" | "rig" | "monitor" | "settings";

export const HUB_IDS: readonly HubId[] = ["sky", "weather", "session", "rig", "monitor", "settings"];

export interface Route {
  hub: HubId;
  /** The resolved sub-nav section. "" for a hub that has none (sky). */
  sub: string;
  /** 0..2 sheet names, outermost first. */
  sheets: string[];
  params: Record<string, string>;
}

/** The sub-nav sections per hub, in the order the chips render. The FIRST entry
 *  is the default the router resolves when the hash names none. */
export const SUBS: Record<HubId, readonly string[]> = {
  sky: [],
  weather: ["conditions", "sky", "radar"],
  session: ["now", "gallery", "flows"],
  rig: ["devices", "capture"],
  monitor: ["live", "log", "alerts"],
  settings: ["general", "users", "about"],
};

/** At most two sheets stack (Mount -> Polar). A third would put the thing the
 *  user is looking at three BACK presses from the screen it belongs to. */
export const MAX_SHEETS = 2;

function isHub(v: string): v is HubId {
  return (HUB_IDS as readonly string[]).includes(v);
}

/** "#/rig/devices?x=1" -> "/rig/devices?x=1"; "" -> "/". */
function stripHash(hash: string): string {
  let h = hash ?? "";
  if (h.startsWith("#")) h = h.slice(1);
  if (!h.startsWith("/")) h = "/" + h;
  return h;
}

// ------------------------------------------------------------------- classic

/** True for the legacy root's hash: `#/classic` and `#/classic/<view>`.
 *  `main.tsx` mounts `App` instead of `NextApp` while this holds. */
export function isClassicHash(hash: string): boolean {
  const p = stripHash(hash).split("?")[0];
  return p === "/classic" || p === "/classic/" || p.startsWith("/classic/");
}

/** The path of a hash with the query stripped: "#/rig/devices?x=1" -> "/rig/devices".
 *  Exported for `rootChoice.ts`, which has to classify a hash this module
 *  deliberately knows nothing about (a legacy view name). */
export function hashPath(hash: string): string {
  return stripHash(hash).split("?")[0];
}

/** The first path segment, decoded; "" for the bare root (`#`, `#/`, ""). */
export function firstSegment(hash: string): string {
  const segs = hashPath(hash).split("/").filter(Boolean);
  return segs.length > 0 ? decodeURIComponent(segs[0]) : "";
}

/** True when the hash names one of THIS UI's six hubs. Note what it is not:
 *  `parseHash` FALLS BACK to sky for an unrecognised first segment, which is
 *  the right answer once you have decided to render the new UI and the wrong
 *  one for deciding whether to render it at all - under `DEFAULT_ROOT` =
 *  "classic", `#/atlas` must reach the classic Atlas, not sky. */
export function isHubHash(hash: string): boolean {
  return isHub(firstSegment(hash));
}

/** The alias that always means "the new UI's home", whichever root is default,
 *  and the path it lands on. The classic root's counterpart is `#/classic`. */
export const NEXT_ROOT_ALIAS = "next";
export const NEXT_HOME = "/sky";

/** The `<view>` in `#/classic/<view>`, or null. `main.tsx` hands it to the
 *  store's `setView` once before rendering the legacy root; it is NOT validated
 *  here (this module has no business importing the legacy `ViewName` union -
 *  the caller checks membership). */
export function classicView(hash: string): string | null {
  if (!isClassicHash(hash)) return null;
  const segs = stripHash(hash).split("?")[0].split("/").filter(Boolean);
  return segs.length > 1 ? decodeURIComponent(segs[1]) : null;
}

// --------------------------------------------------------------- parse/build

export function parseHash(hash: string): Route {
  const raw = stripHash(hash);
  const qi = raw.indexOf("?");
  const path = qi >= 0 ? raw.slice(0, qi) : raw;
  const query = qi >= 0 ? raw.slice(qi + 1) : "";

  const params: Record<string, string> = {};
  if (query) {
    const sp = new URLSearchParams(query);
    sp.forEach((v, k) => { params[k] = v; });
  }

  const segs = path.split("/").filter(Boolean).map((s) => decodeURIComponent(s));

  // An unrecognised hub drops the WHOLE path, not just its first segment: the
  // segments after it were addressed to a hub this build does not have, so
  // reading them as sky's sheets would open a sheet nobody asked for. The
  // params survive - a deep link's ?target=M31 is still meaningful on the sky.
  if (segs.length === 0 || !isHub(segs[0])) {
    return { hub: "sky", sub: SUBS.sky[0] ?? "", sheets: [], params };
  }

  const hub = segs[0];
  const subs = SUBS[hub];
  let i = 1;
  let sub = subs[0] ?? "";
  if (segs.length > 1 && subs.includes(segs[1])) {
    sub = segs[1];
    i = 2;
  }
  const sheets = segs.slice(i, i + MAX_SHEETS);
  return { hub, sub, sheets, params };
}

/** The canonical hash for a route: hub, then the sub whenever the hub has any
 *  (including the default, so one screen has exactly one URL), then the sheets,
 *  then the params sorted by key. `parseHash(buildHash(r))` round-trips. */
export function buildHash(r: Route): string {
  const subs = SUBS[r.hub] ?? [];
  const parts: string[] = [encodeURIComponent(r.hub)];
  if (subs.length > 0) {
    const sub = subs.includes(r.sub) ? r.sub : subs[0];
    parts.push(encodeURIComponent(sub));
  }
  for (const s of (r.sheets ?? []).slice(0, MAX_SHEETS)) parts.push(encodeURIComponent(s));

  const keys = Object.keys(r.params ?? {}).sort();
  let q = "";
  if (keys.length > 0) {
    const sp = new URLSearchParams();
    for (const k of keys) sp.append(k, r.params[k]);
    q = "?" + sp.toString();
  }
  return "#/" + parts.join("/") + q;
}

/** Canonicalise a path a caller typed ("/rig/devices/camera?x=1"). */
function canonical(path: string): string {
  return buildHash(parseHash(path));
}

// ------------------------------------------------------------ live subscription
//
// ONE module-level listener and ONE parsed value, per section 3's "useRoute
// must be cheap". Every React subscriber shares the cached Route OBJECT, which
// is also what `useSyncExternalStore` demands: a snapshot that keeps its
// identity until something actually changes, or it re-renders forever.

type Listener = () => void;
const listeners = new Set<Listener>();

const NO_HASH = "\0";      // a sentinel no real hash can equal
let cachedHash: string = NO_HASH;
let cached: Route = { hub: "sky", sub: "", sheets: [], params: {} };

function locationHash(): string {
  if (typeof window === "undefined" || !window.location) return "";
  return window.location.hash || "";
}

/** The current route. Reparsed only when the hash text actually changed. */
export function currentRoute(): Route {
  const h = locationHash();
  if (h !== cachedHash) {
    cachedHash = h;
    cached = parseHash(h);
  }
  return cached;
}

function emit(): void {
  // Invalidate first: a listener that reads currentRoute() must see the NEW
  // value, and replaceState fires no hashchange to do it for us.
  cachedHash = NO_HASH;
  for (const l of Array.from(listeners)) l();
}

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("hashchange", emit);
}

function subscribe(l: Listener): () => void {
  listeners.add(l);
  return () => { listeners.delete(l); };
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, currentRoute, currentRoute);
}

// ------------------------------------------------------------------------ nav

function pushHash(h: string): void {
  if (typeof window === "undefined" || !window.location) return;
  if (window.location.hash === h) return;   // no entry for a tap on where you are
  window.location.hash = h;
  // `hashchange` is QUEUED, not synchronous: assigning the hash returns before
  // any listener runs. Emitting here as well makes a tap on a tab repaint in
  // the same turn as the press instead of one task later, which on a phone is
  // the difference between a button that responds and one that feels stuck.
  // The queued event then arrives and re-emits over an unchanged hash, which
  // reparses to the same route and costs one render at most.
  emit();
}

function replaceHash(h: string): void {
  if (typeof window === "undefined" || !window.location) return;
  const href = String(window.location.href || "").split("#")[0];
  const hist = window.history;
  if (hist && typeof hist.replaceState === "function") {
    hist.replaceState(hist.state, "", href + h);
    emit();                                  // replaceState fires no hashchange
    return;
  }
  pushHash(h);
}

export const nav = {
  /** Push a path: "/rig/devices/camera?x=1". Canonicalised on the way in, so
   *  every screen has one URL no matter which shorthand the caller used. */
  go(path: string): void { pushHash(canonical(path)); },

  /** Same, without a history entry. Sub-nav changes use this. */
  replace(path: string): void { replaceHash(canonical(path)); },

  /** Switch hubs. Clears sheets and params: a hub never opens on top of
   *  another hub's state, and a stale ?target= from the sky is not an argument
   *  the rig understands. */
  hub(id: HubId, sub?: string): void {
    const subs = SUBS[id] ?? [];
    const s = sub && subs.includes(sub) ? sub : (subs[0] ?? "");
    nav.go(buildHash({ hub: id, sub: s, sheets: [], params: {} }));
  },

  /** Push a sheet onto the stack. A third sheet REPLACES the second rather
   *  than being dropped silently - the user pressed something and it must
   *  open; what it may not do is bury the screen three deep. */
  sheet(name: string, params?: Record<string, string>): void {
    const r = currentRoute();
    const stack = r.sheets.slice(0, MAX_SHEETS);
    const sheets = stack.length >= MAX_SHEETS
      ? [...stack.slice(0, MAX_SHEETS - 1), name]
      : [...stack, name];
    nav.go(buildHash({ hub: r.hub, sub: r.sub, sheets, params: params ?? {} }));
  },

  /** Pop one sheet. REPLACES rather than pushes: closing is undoing the open,
   *  and a Back stack of open/close/open/close is a trap, not a history. */
  closeSheet(): void {
    const r = currentRoute();
    if (r.sheets.length === 0) return;
    const sheets = r.sheets.slice(0, -1);
    nav.replace(buildHash({ hub: r.hub, sub: r.sub, sheets, params: sheets.length ? r.params : {} }));
  },

  /** The BACK affordance: close a sheet if one is open, otherwise leave. */
  back(): void {
    const r = currentRoute();
    if (r.sheets.length > 0) { nav.closeSheet(); return; }
    if (typeof window !== "undefined" && window.history && typeof window.history.back === "function") {
      window.history.back();
    }
  },
};

/** Test hatch: drop the cached parse so a test that assigns `location.hash`
 *  directly (rather than through `nav`) sees the new value. Production code
 *  never needs it - `hashchange` does the same job. */
export function resetRouterCacheForTests(): void {
  cachedHash = NO_HASH;
}
