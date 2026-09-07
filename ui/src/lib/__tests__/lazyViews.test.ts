// lazyViews.test.ts — guards the invariants that make route-level code splitting
// safe on a telescope box (see lib/lazyViews.ts).
//
// Run directly:  npx tsx src/lib/__tests__/lazyViews.test.ts
//
// The failure this exists to prevent: someone adds a destination to the ViewName
// union, wires it into NAV, and forgets the loader entry. Before splitting, the
// VIEWS record was a total `Record<ViewName, …>` and TypeScript caught that. The
// split table is necessarily `Partial<…>` (eager and lazy each cover a subset), so
// the compiler can no longer see the hole — App would render an EMPTY PANE for the
// new tab. This test restores that guarantee by checking the two tables union to
// exactly the ViewName type, and it does so by reading the SOURCE (types.ts and
// App.tsx) so it cannot drift from what actually ships.

// Same dependency-free node:fs access idiom as src/__tests__/cssClasses.test.ts —
// the project installs no @types/node, and `tsc -b` type-checks everything under
// src/, so fs is reached through a locally-typed dynamic import.
interface NodeFsLike {
  readFileSync(path: string, encoding: string): string;
  existsSync(path: string): boolean;
}
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;

const pathOf = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");

const typesSrc = fs.readFileSync(pathOf("../../types.ts"), "utf8");
const appSrc = fs.readFileSync(pathOf("../../App.tsx"), "utf8");
const lazySrc = fs.readFileSync(pathOf("../lazyViews.ts"), "utf8");

import {
  LAZY_VIEWS, isLazyView, getLazyView, preloadView, isViewPoisoned, runPreloadSweep,
  LINK_WAIT_MS, SWEEP_RETRY_MS, MAX_SWEEPS, type SweepDeps,
} from "../lazyViews";
import type { ViewName } from "../../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
// Async tests are QUEUED and run strictly sequentially. They share module-level
// state (the poisoned set, the lazy cache), so overlapping them would make them
// interfere — an earlier draft did exactly that and produced two false failures.
const queue: (() => Promise<void>)[] = [];
function atest(name: string, fn: () => Promise<void>): void {
  queue.push(async () => {
    try {
      await fn();
      passed++;
    } catch (e) {
      failed++;
      failures.push(`✗ ${name}: ${(e as Error).message}`);
    }
  });
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ---------------------------------------------------------------- parsing
/** Every member of the `export type ViewName = "a" | "b" | …;` union in types.ts. */
function parseViewNames(): string[] {
  const at = typesSrc.search(/export type ViewName\s*=/);
  if (at < 0) throw new Error("could not locate `export type ViewName` in types.ts");
  // Strip the (heavy) `//` annotations BEFORE looking for the terminating `;` —
  // one of those comments contains a semicolon, which would otherwise cut the
  // union short and silently pass this file's coverage checks.
  const tail = typesSrc.slice(at).replace(/\/\/[^\n]*/g, "");
  const end = tail.indexOf(";");
  if (end < 0) throw new Error("unterminated ViewName union in types.ts");
  return [...tail.slice(0, end).matchAll(/"([a-z0-9_]+)"/gi)].map((x) => x[1]);
}

/** Keys of the EAGER_VIEWS record in App.tsx. */
function parseEagerViews(): string[] {
  // The annotation contains `=>`, so match the first `= {` rather than `[^=]*=`.
  const m = appSrc.match(/const EAGER_VIEWS[\s\S]*?=\s*\{([\s\S]*?)\n\};/);
  if (!m) throw new Error("could not locate `const EAGER_VIEWS = {` in App.tsx");
  const body = m[1].replace(/\/\/[^\n]*/g, "");
  return [...body.matchAll(/^\s*([a-z0-9_]+)\s*:/gim)].map((x) => x[1]);
}

const VIEW_NAMES = parseViewNames();
const EAGER = parseEagerViews();

// ---------------------------------------------------------------- tests
test("sanity: the ViewName union and EAGER_VIEWS parsed", () => {
  assert(VIEW_NAMES.length >= 10, `parsed only ${VIEW_NAMES.length} ViewName members`);
  assert(EAGER.length >= 1, "parsed no EAGER_VIEWS entries");
});

test("every ViewName is routed — no destination renders an empty pane", () => {
  const routed = new Set([...EAGER, ...LAZY_VIEWS]);
  const orphans = VIEW_NAMES.filter((v) => !routed.has(v as ViewName));
  assert(
    orphans.length === 0,
    `ViewName member(s) with neither an EAGER_VIEWS entry nor a lazy loader: ${orphans.join(", ")} ` +
      `— App would render nothing for these. Add a loader in lib/lazyViews.ts.`,
  );
});

test("no route is BOTH eager and lazy (that would ship the code twice)", () => {
  const dupes = EAGER.filter((v) => (LAZY_VIEWS as string[]).includes(v));
  assert(dupes.length === 0, `routed both eagerly and lazily: ${dupes.join(", ")}`);
});

test("no loader table entry is a stale/unknown ViewName", () => {
  const known = new Set(VIEW_NAMES);
  const unknown = (LAZY_VIEWS as string[]).filter((v) => !known.has(v));
  assert(unknown.length === 0, `loaders for names not in the ViewName union: ${unknown.join(", ")}`);
});

test("the landing view is eager — first paint must never Suspend", () => {
  // store.ts initialises `view: "connect"` on every cold start. Splitting it would
  // guarantee a loading flash on the one screen every session begins on.
  const initial = appSrc.includes("EAGER_VIEWS") ? "connect" : "";
  assert(EAGER.includes("connect"), "the default landing view 'connect' must be eagerly bundled");
  assert(initial === "connect", "unreachable — kept so the intent above is explicit");
  assert(!isLazyView("connect"), "isLazyView('connect') should be false");
});

test("every lazy loader points at a module that exists on disk", () => {
  const specs = [...lazySrc.matchAll(/import\("(\.\.\/[^"]+)"\)/g)].map((m) => m[1]);
  assert(
    specs.length === LAZY_VIEWS.length,
    `found ${specs.length} dynamic import()s but ${LAZY_VIEWS.length} lazy views`,
  );
  // Specifiers are relative to src/lib/lazyViews.ts; this file lives one level
  // deeper (src/lib/__tests__/), hence the extra "../".
  const missing = specs.filter(
    (s) => !fs.existsSync(pathOf(`../${s}.tsx`)) && !fs.existsSync(pathOf(`../${s}.ts`)),
  );
  assert(missing.length === 0, `dynamic import target(s) not found: ${missing.join(", ")}`);
});

test("isLazyView agrees with the loader table", () => {
  for (const v of LAZY_VIEWS) assert(isLazyView(v), `isLazyView('${v}') should be true`);
  for (const v of EAGER) assert(!isLazyView(v as ViewName), `isLazyView('${v}') should be false`);
});

test("getLazyView memoises per view", () => {
  const a = getLazyView("guide");
  const b = getLazyView("guide");
  assert(a !== null, "getLazyView('guide') returned null");
  assert(a === b, "getLazyView should return a stable memoised component");
});

test("getLazyView returns null for eager views", () => {
  assert(getLazyView("connect") === null, "eager views must not resolve to a lazy component");
});

test("preloadView never throws for any name (incl. eager / bogus)", () => {
  preloadView("connect"); // eager: no-op
  preloadView("nope" as ViewName); // unknown: no-op, must not throw
  assert(!isViewPoisoned("connect"), "an eager view can never be poisoned");
});

test("every split chunk is scheduled for background preload", () => {
  // The safety contract: splitting is only acceptable because everything is warmed
  // after first paint. preloadAllViews iterates LAZY_VIEWS, so assert the source
  // really does iterate that list rather than some hand-maintained subset.
  assert(
    /views: ViewName\[\] = LAZY_VIEWS/.test(lazySrc),
    "the sweep must default to LAZY_VIEWS so no split chunk is left un-warmed",
  );
  assert(/preloadAllViews\(\)/.test(appSrc), "App must call preloadAllViews() after mount");
});

// --- the module-map facts (measured, see lib/lazyViews.ts header) -----------
// A dynamic import() whose fetch fails is cached as a failure in the document's
// module map forever; later import()s of that URL never touch the network again.
// The three tests below pin the design decisions that follow from that, because
// each one looks like a "missing" resilience feature to anyone who has not
// measured it, and is therefore likely to get helpfully re-added.

test("no retry loop around import() — it provably cannot reach the network", () => {
  assert(
    !/RETRY_DELAYS|for\s*\([^)]*attempt|while[^\n]*attempt/i.test(lazySrc),
    "lazyViews looks like it retries import(). A failed dynamic import is cached in " +
      "the module map for the life of the document, so retrying it makes ZERO network " +
      "requests and only delays honest feedback. Measured: 0 refetches over 10s.",
  );
});

// --- sweep behaviour, exercised for real via injected deps ------------------
// runPreloadSweep takes its clock/loader/link-probe as parameters precisely so
// these can be behaviour tests rather than assertions about source text.

/** A sweep harness with a virtual clock: `sleep` resolves instantly but is logged. */
function harness(opts: {
  linkUpAfterMs?: number;
  failOn?: ViewName[];
  views?: ViewName[];
} = {}) {
  const views = opts.views ?? LAZY_VIEWS;
  const failOn = new Set<string>(opts.failOn ?? []);
  let now = 0; // virtual clock; advances only via the injected sleep
  const loaded: ViewName[] = [];
  const attempted: ViewName[] = [];
  const poisonedHere: ViewName[] = [];
  const sleeps: number[] = [];
  const deps: SweepDeps = {
    isLinkUp: () => now >= (opts.linkUpAfterMs ?? 0),
    load: async (v) => {
      attempted.push(v);
      if (failOn.has(v)) throw new Error("chunk unreachable");
      loaded.push(v);
    },
    sleep: async (ms) => { sleeps.push(ms); now += ms; },
    onPoisoned: (v) => poisonedHere.push(v),
  };
  const run = () => runPreloadSweep(deps, views);
  return { run, loaded, attempted, poisonedHere, sleeps, views, now: () => now };
}

atest("healthy link: every split view is warmed, one at a time, in order", async () => {
  const h = harness();
  await h.run();
  assert(h.loaded.length === h.views.length,
    `warmed ${h.loaded.length}/${h.views.length}: missing ${h.views.filter((v) => !h.loaded.includes(v)).join(", ")}`);
  assert(h.loaded.join(",") === h.views.join(","), "views should be warmed in declaration order");
  assert(h.sleeps.length === 0, `a healthy sweep should not need to back off, slept ${h.sleeps.join(",")}`);
});

atest("the sweep HOLDS while the link is down, then runs", async () => {
  // Spending a view's single import() attempt before the box is answering is how
  // you poison a screen for the whole session.
  const h = harness({ linkUpAfterMs: 4_000 });
  await h.run();
  assert(h.sleeps.length > 0, "the sweep must wait rather than charging at a dead link");
  assert(h.now() >= 4_000, `sweep started at t=${h.now()}ms, before the link was up`);
  assert(h.loaded.length === h.views.length, "everything should still be warmed once the link came up");
});

atest("the link wait FAILS OPEN — a wedged link never means zero preloading", async () => {
  const h = harness({ linkUpAfterMs: Number.MAX_SAFE_INTEGER });
  await h.run();
  assert(h.now() >= LINK_WAIT_MS, `gave up waiting at t=${h.now()}ms, before LINK_WAIT_MS=${LINK_WAIT_MS}`);
  assert(h.now() < LINK_WAIT_MS + 5_000, `waited far too long: t=${h.now()}ms`);
  assert(h.loaded.length === h.views.length, "must preload anyway after the fail-open deadline");
});

atest("ONE dead chunk does not burn the other twelve views' single attempts", async () => {
  // The failure this rule exists for: a browser caches a FAILED dynamic import
  // permanently, so if the sweep kept going on a dead link it would spend every
  // remaining view's one attempt and break the whole app for the session.
  const dead = LAZY_VIEWS[2];
  const h = harness({ failOn: [dead] });
  await h.run();
  assert(h.poisonedHere.join(",") === dead, `expected only '${dead}' poisoned, got: ${h.poisonedHere.join(",")}`);
  const others = h.views.filter((v) => v !== dead);
  const missed = others.filter((v) => !h.loaded.includes(v));
  assert(missed.length === 0, `these views were never warmed after the failure: ${missed.join(", ")}`);
  assert(h.sleeps.includes(SWEEP_RETRY_MS), "the sweep must pause before resuming after a failure");
});

atest("the sweep aborts immediately rather than marching through a dead link", async () => {
  // Everything fails: the sweep must not attempt all 13 in one pass.
  const h = harness({ failOn: [...LAZY_VIEWS] });
  await h.run();
  assert(h.attempted.length <= MAX_SWEEPS,
    `attempted ${h.attempted.length} loads on a fully dead link; must be <= MAX_SWEEPS (${MAX_SWEEPS}) ` +
      `— one per sweep, not one per view`);
  assert(h.attempted.length >= 1, "should still try at least once");
});

atest("a fully dead link terminates instead of spinning forever", async () => {
  const h = harness({ failOn: [...LAZY_VIEWS] });
  await h.run(); // would hang the test run if unbounded
  assert(h.poisonedHere.length <= MAX_SWEEPS, `poisoned ${h.poisonedHere.length} views`);
});

test("sweep constants are sane", () => {
  assert(MAX_SWEEPS >= 2 && MAX_SWEEPS <= 10, `MAX_SWEEPS out of range: ${MAX_SWEEPS}`);
  assert(SWEEP_RETRY_MS >= 1_000, `SWEEP_RETRY_MS too eager: ${SWEEP_RETRY_MS}`);
  assert(LINK_WAIT_MS > 0 && LINK_WAIT_MS <= 30_000, `LINK_WAIT_MS out of range: ${LINK_WAIT_MS}`);
  // App must pass a REAL probe, not the permissive default.
  assert(/preloadAllViews\(\(\) => useStore\.getState\(\)\.wsPhase === "up"\)/.test(appSrc),
    "App must gate the preload sweep on the websocket being up");
});

test("the chunk-load failure pane offers a reload, and does NOT offer a fake retry", () => {
  const vbRaw = fs.readFileSync(pathOf("../../components/ViewBoundary.tsx"), "utf8");
  // Strip comments: the file EXPLAINS at length why ViewLoadFailed has no "Try
  // again", and that prose must not trip the check that the PANE has no such
  // button. Scoped to ViewLoadFailed itself (not ViewRenderError, a few lines
  // below it) — a genuine render throw gets a real "Try again" now, because
  // unlike a poisoned module-map entry, re-mounting the subtree can actually
  // recover. See viewBoundaryDom.test.tsx for that pane's own coverage.
  const vb = vbRaw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/[^\n]*$/gm, "");
  const viewLoadFailedSrc = vb.slice(
    vb.indexOf("function ViewLoadFailed"), vb.indexOf("function ViewRenderError"));
  assert(viewLoadFailedSrc.length > 0, "could not locate ViewLoadFailed in the source — test is stale");
  assert(/window\.location\.reload\(\)/.test(viewLoadFailedSrc),
    "the chunk-failure pane must offer a reload — the only real recovery");
  assert(!/Try again/i.test(viewLoadFailedSrc),
    "a 'Try again' button on the CHUNK-failure pane cannot work: the failed import is cached " +
      "in the module map, so re-importing makes no network request. Offer a reload instead.");
  assert(/Couldn't load this screen/.test(viewLoadFailedSrc),
    "the chunk-failure pane must explain itself, never render blank");
  assert(/role="alert"/.test(viewLoadFailedSrc), "the chunk-failure pane should announce itself");
});

test("the render-error pane's Try again resets state locally, not via a reload", () => {
  const vbRaw = fs.readFileSync(pathOf("../../components/ViewBoundary.tsx"), "utf8");
  const vb = vbRaw.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/[^\n]*$/gm, "");
  const viewRenderErrorSrc = vb.slice(
    vb.indexOf("function ViewRenderError"), vb.indexOf("function LazyViewHost"));
  assert(viewRenderErrorSrc.length > 0, "could not locate ViewRenderError in the source — test is stale");
  assert(/This screen hit an error/.test(viewRenderErrorSrc),
    "a render throw must not reuse the chunk-download wording — see the NGC 604 incident");
  assert(/Try again/.test(viewRenderErrorSrc), "the render-error pane must offer a working retry");
  assert(/window\.location\.reload\(\)/.test(viewRenderErrorSrc),
    "the render-error pane must still offer Reload alongside Try again");
});

// ---------------------------------------------------------------- report
for (const t of queue) await t();
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nlazyViews.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
