// hubs/index.ts - the hub registry: what each tab is called, what it draws in
// the tab bar and the rail, what its sub-nav chips say, and which component
// renders its body.
//
// All six bodies are real screens; the T0.1 placeholders are gone.
//
// THE BODIES ARE CODE-SPLIT (review #43). Nothing under `next/` was, which
// reversed the house pattern - every legacy view is lazy (`lib/lazyViews.ts`)
// and the entry chunk had grown to 1,719 kB raw / 546 kB gzip, all of it paid
// for before first paint over a field link or the relay.
//
// THE BROWSER FACT THAT SHAPES THE PRELOAD BELOW, quoted from that file because
// getting it wrong poisons a screen for a whole session: a dynamic import()
// whose fetch FAILS is recorded as a failure in the document's module map for
// the lifetime of the document, and every later import() of that URL reuses the
// stored failure WITHOUT touching the network. Measured against this build.
// Two consequences, both obeyed here:
//
//   * retrying import() is worthless - only a reload gets a fresh module map,
//     which is why `shell/HubBoundary.tsx` offers RELOAD and no retry for a
//     chunk failure;
//   * the ONE attempt each hub gets must be spent at the safest moment. So the
//     sweep waits for the socket to be UP, loads one hub at a time, and STOPS
//     on the first failure rather than burning the remaining five against the
//     same dead link.
//
// The SHEETS stay static, deliberately. A sheet is opened by name from the
// hash, and `SHEETS[name]` has to answer synchronously for `SheetHost` to know
// whether the name exists at all - the alternative is a "not built yet" pane
// that is really "not downloaded yet", which is the honest-message failure this
// registry's duplicate check exists to prevent.

import { lazy, type ComponentType } from "react";
import type { NxIconName } from "../icons";
import type { SubNavItem, Tone } from "../ui";
import type { HubId } from "../router";
import { SUBS } from "../router";

import { sheets as skySheets } from "./sky/sheets";
import { sheets as weatherSheets } from "./weather/sheets";
import { sheets as sessionSheets } from "./session/sheets";
import { sheets as rigSheets } from "./rig/sheets";
import { sheets as monitorSheets } from "./monitor/sheets";
import { sheets as settingsSheets } from "./settings/sheets";

export type { SheetComponent, SheetProps } from "./sheets";

/** What a hub's sub-nav is allowed to know. Deliberately a small explicit bag
 *  rather than the whole store: `subs()` stays a pure function of it, so the
 *  chips can be unit-tested and the shell can read each field with one narrow
 *  selector instead of subscribing the sub-nav to every store write. Hub tasks
 *  ADD fields here; nothing reads the store from inside `subs`. */
export interface SubContext {
  /** Saved flows in the library, or null while it has never been read - which
   *  is not the same claim as zero. */
  flowCount: number | null;
  /** The top incident's severity tone, for the dot on Session - Now and on
   *  Monitor - Live. */
  incidentTone: Tone | null;
  /** How many incidents are live. `NOW 2` says there is a second card under the
   *  one on screen; the dot alone would say only that something is wrong. */
  incidentCount: number;
  /** Alerts the dispatcher has not delivered (`GET /api/alerts/health`), or
   *  null while it has never been read. */
  alertsUndelivered: number | null;
  /** Devices currently connected, or null before the first status arrives. */
  rigDeviceCount: number | null;
  /** DEVICES dot: bad when a role the rig was told to bring up did not, warn
   *  when one came up at boot and has since dropped. */
  rigLinkTone: Tone | null;
  /** WEATHER dot: warn while a cloud alert stands un-overridden, dim while the
   *  feed is stale - an absence of information, not good news. */
  weatherDot: Tone | null;
  /** Error lines the store counted while the log was CLOSED (`store.ts:2072`).
   *  It had no reader anywhere in this UI (review #12), so an error whose toast
   *  had been dismissed and which had scrolled past the 200-line ring was
   *  announced nowhere at all. Zero renders no count, never a "0". */
  unseenError: number;
}

export interface HubMeta {
  id: HubId;
  label: string;
  icon: NxIconName;
  subs: (ctx: SubContext) => SubNavItem[];
}

/** A sub-nav item for every entry in `SUBS[hub]`, uppercase, no counts. The
 *  hubs that have something to count override it. */
function plainSubs(hub: HubId): (ctx: SubContext) => SubNavItem[] {
  const ids = SUBS[hub];
  return () => ids.map((id) => ({ id, label: id.toUpperCase() }));
}

export const HUB_META: Record<HubId, HubMeta> = {
  sky: { id: "sky", label: "SKY", icon: "sky", subs: plainSubs("sky") },
  weather: {
    id: "weather",
    label: "WEATHER",
    icon: "weather",
    subs: (ctx) => [
      { id: "conditions", label: "CONDITIONS", dot: ctx.weatherDot ?? undefined },
      { id: "sky", label: "SKY" },
      { id: "radar", label: "RADAR" },
    ],
  },
  session: {
    id: "session",
    label: "SESSION",
    icon: "session",
    subs: (ctx) => [
      // The count is the SECOND card and beyond: one incident is already the
      // dot, and "NOW 1" beside a dot says the same thing twice. "NOW 2" says
      // something the dot cannot - that acting on the card on screen does not
      // clear the screen.
      {
        id: "now",
        label: "NOW",
        dot: ctx.incidentTone ?? undefined,
        count: ctx.incidentCount > 1 ? ctx.incidentCount : undefined,
      },
      { id: "gallery", label: "GALLERY" },
      { id: "flows", label: "FLOWS", count: ctx.flowCount ?? undefined },
    ],
  },
  rig: {
    id: "rig",
    label: "RIG",
    icon: "rig",
    subs: (ctx) => [
      {
        id: "devices",
        label: "DEVICES",
        count: ctx.rigDeviceCount ?? undefined,
        dot: ctx.rigLinkTone ?? undefined,
      },
      { id: "capture", label: "CAPTURE" },
    ],
  },
  monitor: {
    id: "monitor",
    label: "MONITOR",
    icon: "monitor",
    subs: (ctx) => [
      // Live wears the same incident tone the Session chip does: the Monitor's
      // LIVE screen is where the run is watched from, and a hold that shows on
      // one and not the other is two answers to one question.
      { id: "live", label: "LIVE", dot: ctx.incidentTone ?? undefined },
      // The unseen-error count. Legacy carried it on the header log button
      // (`HeaderControls.tsx:133`) and the bottom nav (`BottomNav.tsx:144`);
      // this chip and the MONITOR tab badge are its two homes now. Entering
      // the screen clears it - `LogScreen` calls `openLog()` on mount.
      { id: "log", label: "LOG", count: ctx.unseenError || undefined },
      // Undelivered, not configured: how many sinks exist is a settings fact,
      // how many messages did not get out is news.
      { id: "alerts", label: "ALERTS", count: ctx.alertsUndelivered || undefined },
    ],
  },
  settings: { id: "settings", label: "SETTINGS", icon: "settings", subs: plainSubs("settings") },
};

/** The tab-bar / rail order, which is also the sub-nav-free reading order of
 *  the app: find something, check the sky, watch the run, drive the rig, read
 *  the log, change a setting. */
export const HUB_ORDER: readonly HubId[] = ["sky", "weather", "session", "rig", "monitor", "settings"];

// ------------------------------------------------------------ the hub bodies

type HubModule = { default: ComponentType };

/** One loader per hub. Exported so a test can WARM the modules before mounting
 *  the shell (a lazy body that has to hit the filesystem mid-`act()` is a
 *  flaky test, not a real assertion) and so the preload sweep below has one
 *  list rather than a second copy of these specifiers. */
export const HUB_LOADERS: Record<HubId, () => Promise<HubModule>> = {
  sky: () => import("./sky/SkyHub").then((m) => ({ default: m.SkyHub })),
  weather: () => import("./weather/WeatherHub").then((m) => ({ default: m.WeatherHub })),
  session: () => import("./session/SessionHub").then((m) => ({ default: m.SessionHub })),
  rig: () => import("./rig/RigHub").then((m) => ({ default: m.RigHub })),
  monitor: () => import("./monitor/MonitorHub").then((m) => ({ default: m.MonitorHub })),
  settings: () => import("./settings/SettingsHub").then((m) => ({ default: m.SettingsHub })),
};

export const HUBS: Record<HubId, ComponentType> = {
  sky: lazy(HUB_LOADERS.sky),
  weather: lazy(HUB_LOADERS.weather),
  session: lazy(HUB_LOADERS.session),
  rig: lazy(HUB_LOADERS.rig),
  monitor: lazy(HUB_LOADERS.monitor),
  settings: lazy(HUB_LOADERS.settings),
};

// ------------------------------------------------------------ preload sweep

let preloadStarted = false;

const scheduleIdle = (cb: () => void): void => {
  const ric = (globalThis as {
    requestIdleCallback?: (c: () => void, o?: { timeout: number }) => void;
  }).requestIdleCallback;
  // Safari - i.e. every iPad and iPhone in the field - has no
  // requestIdleCallback. The timeout fallback is not a nicety; it is the
  // difference between the sweep happening and not happening on the most
  // likely client.
  if (typeof ric === "function") ric(cb, { timeout: 2000 });
  else setTimeout(cb, 200);
};

/**
 * Warm the five hubs the user is not looking at, one at a time, right after
 * first paint. Idempotent - the first call wins and every later one returns.
 *
 * CALL IT ONLY ONCE THE SOCKET IS UP. `NextApp` gates on `wsPhase === "up"`
 * for the reason in the header: spending each hub's one and only import()
 * attempt on a link that has not come up yet is how six screens get poisoned
 * for a whole session instead of none.
 *
 * A failure is never surfaced from here. If the user later taps that tab, the
 * lazy component rejects again from the module map and `HubBoundary` explains
 * it with a working reload - so a "poisoned" list kept here would be state
 * nothing reads.
 */
export function preloadHubs(first?: HubId): void {
  if (preloadStarted) return;
  preloadStarted = true;
  // The hub already on screen leads the list only so the sweep does not queue
  // behind a fetch the render has already started; the module registry dedupes
  // it either way. The rest keep tab order, which is the order a first-time
  // user walks them in.
  const ids = Object.keys(HUB_LOADERS) as HubId[];
  const order = first && ids.includes(first)
    ? [first, ...ids.filter((id) => id !== first)]
    : ids;
  scheduleIdle(() => {
    void (async () => {
      for (const id of order) {
        try {
          await HUB_LOADERS[id]();
        } catch {
          // STOP. Marching on would spend every remaining hub's single attempt
          // against the same dead link and break five screens instead of one.
          return;
        }
      }
    })();
  });
}

/** The sheet registry, composed from each hub's own `sheets` export.
 *
 *  Names are GLOBAL, and one name is one screen: `sites` is shared by Sky and
 *  Settings and is ONE component registered from both, which is fine - the same
 *  function under the same name is the contract working. What is NOT fine is two
 *  DIFFERENT components under one name: the hash then names a screen that
 *  depends on which hub's module loaded last, so the same URL opens different
 *  things on different builds. That is checked at module load rather than at the
 *  tap that opens the wrong one, because by then it looks like a routing bug. */
const REGISTRIES: Record<string, Record<string, unknown>> = {
  sky: skySheets, weather: weatherSheets, session: sessionSheets,
  rig: rigSheets, monitor: monitorSheets, settings: settingsSheets,
};

/** True in dev and in the plain-Node test runner, false in a production build.
 *  `import.meta.env` is read AS A WHOLE: Vite substitutes the object, and Node
 *  has no `env` on `import.meta` at all, so reading `.PROD` off it directly
 *  would throw before the guard could run. No env means a test, and a test
 *  wants the throw. */
function isDevBuild(): boolean {
  const env = (import.meta as unknown as { env?: { PROD?: boolean } }).env;
  return !env || env.PROD !== true;
}

function composeSheets() {
  const seen: Record<string, string> = {};
  const out: Record<string, unknown> = {};
  const dev = isDevBuild();
  for (const [hub, reg] of Object.entries(REGISTRIES)) {
    for (const name of Object.keys(reg)) {
      if (seen[name] && out[name] !== reg[name]) {
        const msg =
          `next/hubs: "${name}" is registered by ${seen[name]} and ${hub} as two different ` +
          "components. Sheet names are global: share the one component, or rename one of them.";
        // THE THROW IS A DEV TOOL, NOT A RUNTIME POLICY (review #50). Thrown at
        // module load in a production build it is a WHITE SCREEN with the
        // explanation only in a console nobody at a telescope is reading - a
        // build-time mistake turned into a total outage at 2 a.m. In dev and
        // under the test runner it still throws, which is where it can be
        // acted on; in production the LAST registration wins and one sheet
        // opens the wrong screen, which is survivable and visible.
        if (dev) throw new Error(msg);
      }
      seen[name] = hub;
      out[name] = reg[name];
    }
  }
  return out;
}

export const SHEETS = composeSheets() as Record<string, import("./sheets").SheetComponent>;
