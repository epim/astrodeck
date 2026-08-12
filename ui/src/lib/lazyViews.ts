// lazyViews.ts — route-level code splitting for the view modules, built for a
// TELESCOPE IN A DARK FIELD, not for a Lighthouse score.
//
// WHY SPLIT AT ALL
// ----------------
// The UI shipped as one chunk (792 kB raw / 237 kB gzip). Almost none of that is
// vendor code — react+react-dom+scheduler+zustand are ~140 kB raw of it and the
// fonts are already separate woff2 files. The bulk is OUR views and the panels
// they exclusively own (settings/*, sequence/*, atlas/*, preview/*, monitor.tsx,
// weather/*). The user always lands on Equipment, so every other view's code was
// paid for at first paint and used later, if ever. Splitting takes the blocking
// entry chunk to 302 kB raw / 96 kB gzip.
//
// WHY THIS FILE IS PARANOID ABOUT IT
// ----------------------------------
// The deployment reality inverts the usual web tradeoff. This app runs on a small
// box at the scope and is driven from a tablet or phone over local WiFi (sometimes
// a hotspot), all night. A slightly slower first load is cheap. A chunk that fails
// to arrive at 2am when the user taps "Guide" is a RUINED NIGHT.
//
// THE BROWSER FACT THAT SHAPES EVERYTHING BELOW — READ THIS BEFORE EDITING
// -----------------------------------------------------------------------
// A dynamic import() whose fetch fails is recorded as a FAILURE IN THE MODULE MAP,
// permanently, for the lifetime of the document. Every later import() of that same
// URL reuses the stored failure and DOES NOT TOUCH THE NETWORK. This is spec'd
// behaviour (HTML "fetch a single module script" → module map), not a Chrome quirk,
// and it was verified here against the real build: after blocking GuideView's
// chunk once, ten seconds and several retries produced exactly ZERO further
// network requests for it.
//
// Two hard consequences:
//
//   1. RETRYING import() IS WORTHLESS. A retry loop around import() can never
//      reach the network again; it only makes the user stare at a spinner for
//      seconds before being told the truth. An earlier draft of this file had
//      exactly such a loop. It was deleted after measurement. Do not add it back —
//      resilience has to come before the first import(), or from a reload.
//   2. THE ONLY IN-PAGE RECOVERY IS A RELOAD. A fresh document gets a fresh module
//      map. That genuinely works (also verified), which is why ViewBoundary's
//      failure state offers a reload rather than a fake "try again".
//
// So the safety of this split rests on making that one-and-only import() attempt
// happen at the SAFEST POSSIBLE MOMENT, and on never wasting it:
//
//   * PRELOAD EVERYTHING right after first paint (preloadAllViews), while the user
//     is still reading the Equipment screen, standing next to their gear, hours
//     before it matters. By the time anything is tapped the app is whole again and
//     view switching is a pure cache hit — measured: zero chunk fetches across a
//     full click-through of every view.
//   * WAIT FOR A HEALTHY LINK first. Spending the one-shot import on a link that
//     has not come up yet is how you poison a view for a whole session, so the
//     sweep holds until the websocket is up (with a fail-open timeout, because
//     never preloading is worse than preloading pessimistically).
//   * STOP THE SWEEP ON THE FIRST FAILURE. If the link dies mid-sweep, marching on
//     would burn the remaining views' single attempts against a dead network and
//     break TWELVE screens instead of one. The sweep drops that view, pauses, and
//     resumes with the rest later.
//
// Deliberately NOT done here: no service worker, no offline manifest (out of
// scope, and a stale SW is its own 2am failure mode).
import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { ViewName } from "../types";

type ViewModule = { default: ComponentType };
type Loader = () => Promise<ViewModule>;

// The split set. `connect` (Equipment) is deliberately ABSENT: it is the landing
// view on every cold start (store.view defaults to "connect"), so lazy-loading it
// would only trade the entry chunk for a guaranteed Suspense flash on first paint.
// Login is likewise eager — it is the auth gate itself, and it is small.
const LOADERS: Partial<Record<ViewName, Loader>> = {
  capture: () => import("../views/CaptureView"),
  focus: () => import("../views/FocusView"),
  mount: () => import("../views/MountView"),
  polar: () => import("../views/PolarView"),
  guide: () => import("../views/GuideView"),
  sequence: () => import("../views/SequenceView"),
  power: () => import("../views/PowerView"),
  monitor: () => import("../views/MonitorView"),
  settings: () => import("../components/settings/SettingsView"),
  atlas: () => import("../views/AtlasView"),
  tonight: () => import("../views/TonightView"),
  report: () => import("../views/ReportView"),
  help: () => import("../views/HelpView"),
  gallery: () => import("../views/GalleryView"),
  // Flows lives under components/, not views/ — the same shape as `settings`
  // above. The contract's §A.1 pencilled it in at `views/FlowsView.tsx`, but the
  // whole surface is one directory of ~25 co-located files and a one-line
  // re-export in views/ would exist only to satisfy a path convention that
  // `settings` already breaks. `lazyViews.test.ts` fs.existsSync-checks this
  // specifier, so it is the real module or nothing.
  flows: () => import("../components/flows/FlowsView"),
};

/** Views this build code-splits, in preload order. */
export const LAZY_VIEWS = Object.keys(LOADERS) as ViewName[];

export function isLazyView(v: ViewName): boolean {
  return v in LOADERS;
}

// Cache of live lazy components, so switching away and back reuses the resolved
// module instead of rebuilding the wrapper.
const cache = new Map<ViewName, LazyExoticComponent<ComponentType>>();

/** The lazy component for a split view, or null if the view is bundled eagerly. */
export function getLazyView(v: ViewName): LazyExoticComponent<ComponentType> | null {
  const load = LOADERS[v];
  if (!load) return null;
  let c = cache.get(v);
  if (!c) {
    c = lazy(load);
    cache.set(v, c);
  }
  return c;
}

// --- background preload -----------------------------------------------------
// The whole justification for splitting. See the header for why this is not
// optional, and why it is careful about when it spends each view's one attempt.

/** Views whose single import() attempt has been spent and failed. */
const poisoned = new Set<ViewName>();

/** True once this view's chunk is known unreachable for this document. */
export function isViewPoisoned(v: ViewName): boolean {
  return poisoned.has(v);
}

let preloadStarted = false;

/** Test seam: forget preload/module state. Not used by app code. */
export function __resetPreloadForTests(): void {
  preloadStarted = false;
  cache.clear();
  poisoned.clear();
}

const scheduleIdle = (cb: () => void): void => {
  const ric = (globalThis as { requestIdleCallback?: (c: () => void, o?: { timeout: number }) => void })
    .requestIdleCallback;
  // Safari (i.e. every iPad in the field) has no requestIdleCallback; the timeout
  // fallback is not an optimisation detail, it is the difference between the
  // preload happening and not happening on the most likely client.
  if (typeof ric === "function") ric(cb, { timeout: 2000 });
  else setTimeout(cb, 200);
};

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

/** How long to wait for a healthy link before preloading anyway (fail-open). */
export const LINK_WAIT_MS = 10_000;
/** Pause after a failed chunk before resuming the sweep. */
export const SWEEP_RETRY_MS = 15_000;
/** Bounded, so a permanently dead link cannot spin forever. */
export const MAX_SWEEPS = 4;

export interface SweepDeps {
  /** Reports whether the app's websocket is up. */
  isLinkUp: () => boolean;
  /** Loads one view's chunk. */
  load: (v: ViewName) => Promise<unknown>;
  /** Injected so tests can run the schedule without real time passing. */
  sleep: (ms: number) => Promise<void>;
  /** Called when a view's single import() attempt has been spent and failed. */
  onPoisoned: (v: ViewName) => void;
}

/**
 * The preload schedule, as a pure-ish function of its dependencies so it can be
 * tested without a browser or real timers. Exported for tests; app code should
 * call preloadAllViews().
 *
 * Contract (each clause exists because of a specific failure — see the header):
 *   - waits for a healthy link, but never longer than LINK_WAIT_MS (fail open:
 *     never preloading is worse than preloading pessimistically);
 *   - loads one chunk at a time, so we never contend with the first screen's own
 *     API/WS traffic on a weak link;
 *   - on a failure, records the view as poisoned and ABANDONS the rest of this
 *     sweep — marching on would spend every remaining view's one and only
 *     import() attempt against the same dead link, breaking a dozen screens
 *     instead of one;
 *   - resumes the remainder after a pause, up to MAX_SWEEPS, so a brief outage
 *     still ends with the app whole.
 */
export async function runPreloadSweep(deps: SweepDeps, views: ViewName[] = LAZY_VIEWS): Promise<void> {
  // Budget the wait in terms of the injected sleep rather than a wall clock, so
  // the fail-open deadline is exact and deterministic (and testable without
  // patching a global).
  const LINK_POLL_MS = 400;
  for (let waited = 0; !deps.isLinkUp() && waited < LINK_WAIT_MS; waited += LINK_POLL_MS) {
    await deps.sleep(LINK_POLL_MS);
  }

  const remaining = [...views];
  for (let sweep = 0; sweep < MAX_SWEEPS && remaining.length > 0; sweep++) {
    if (sweep > 0) await deps.sleep(SWEEP_RETRY_MS);
    while (remaining.length > 0) {
      const v = remaining[0];
      try {
        await deps.load(v);
        remaining.shift();
      } catch {
        deps.onPoisoned(v);
        remaining.shift();
        break; // abandon this sweep; retry the rest after a pause
      }
    }
  }
}

/**
 * Warm every split chunk in the background. Idempotent.
 *
 * Failures are never surfaced from here — a view that fails to warm is loaded on
 * demand later, and if that fails too ViewBoundary explains it with a working
 * reload.
 *
 * @param isLinkUp reports whether the app's websocket is up. Injected rather than
 *   imported so this module stays free of the store (and trivially testable).
 */
export function preloadAllViews(isLinkUp: () => boolean = () => true): void {
  if (preloadStarted) return;
  preloadStarted = true;
  scheduleIdle(() => {
    void runPreloadSweep({
      isLinkUp,
      load: (v) => LOADERS[v]!(),
      sleep,
      onPoisoned: (v) => poisoned.add(v),
    });
  });
}

/**
 * Warm a single view immediately (e.g. on nav hover/pointerdown), closing the gap
 * between first paint and the background sweep. Safe to call repeatedly — the
 * module registry dedupes, and a failure is recorded rather than thrown.
 */
export function preloadView(v: ViewName): void {
  const load = LOADERS[v];
  if (!load) return;
  void load().catch(() => {
    poisoned.add(v);
  });
}
