// tileEngineAllFailing.test.tsx - when the tile engine calls a blank view
// "all failing" (#493; #404 for the degraded state that call feeds).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/atlas/__tests__/tileEngineAllFailing.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WAS WRONG. TileEngine reported `onAllFailing` only after 8 consecutive
// failures on a blank view. A failed tile is negative-cached for 45 s and
// asked again only when the shared wake timer fires, so a view that needs n <
// 8 tiles fails n per pass and needs ceil(8 / n) passes: CompassSurvey opens
// 55 deg wide, where the view is three tiles, and on a rig with no survey
// source (no pack, online fetch off) it said LOADING for about 90 s before it
// said there was no source. The threshold only a large enough view could reach
// in one pass.
//
// WHAT HOLDS NOW. A blank view is all-failing when its consecutive failures
// reach min(8, the tiles the view needs), where "needs" is the frame's fetch
// plan: every visible tile and the coarser ancestors that could still be
// upsampled into it. Once every one of those has failed, nothing in flight can
// draw the view. 8 stays the cap for a big view, as before.
//
// HOW THIS DRIVES IT. The engine is mounted on its own in jsdom with a WebGL
// context whose every method is a no-op (as framingSkyDegraded.test.tsx hands
// SkyCanvas), so its rAF loop plans, draws nothing and fetches exactly as in a
// browser. The canvas has no layout, so it takes the engine's 360 px fallback
// width, and every view's plan below is COMPUTED from the same pure functions
// the engine calls (tileOrderFor, visibleTiles, planFetches) and asserted, so
// a change in the tile math breaks the fixture loudly instead of quietly
// grading a different view. The network answers per tile: 404, or never (a
// request still out, which an abort rejects as fetch does).
//
// Every mutant below was run in a private scratch copy of ui/ (scratchpad
// H4-UFRAME-mut), never in the shared tree (#254), and the failure it
// produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
// A WebGL context that accepts every call and answers every query with a
// truthy object: enough for initTileGL to compile, link and draw.
const GL_STUB: any = new Proxy({}, { get: () => () => ({}) });
win.HTMLCanvasElement.prototype.getContext = function (kind: string) {
  return kind === "webgl" || kind === "experimental-webgl" ? GL_STUB : null;
};

/** What the network does with one tile: fail it (404) or hold it (a request
 *  still out). Decided per request by the case's rule, and every request is
 *  kept, so a case can say exactly which tiles were asked and how often. */
type Answer = "404" | "hold";
const net = {
  rule: (_key: string, _n: number): Answer => "404",
  asked: [] as string[],
};
win.fetch = (url: string, init?: { signal?: AbortSignal }) => {
  const m = /\/api\/survey\/tile\/[^/]+\/(\d+)\/(\d+)\.jpg$/.exec(String(url));
  if (!m) return Promise.reject(new Error(`no network in this fixture: ${url}`));
  const key = `${m[1]}/${m[2]}`;
  net.asked.push(key);
  const answer = net.rule(key, net.asked.length);
  if (answer === "404") return Promise.resolve({ ok: false, status: 404, blob: async () => ({}) });
  // Held: never answers, but an abort rejects it as a real fetch does, so a
  // request the engine cancels settles instead of dangling.
  return new Promise((_res, rej) => {
    init?.signal?.addEventListener("abort", () => {
      const e = new Error("aborted");
      e.name = "AbortError";
      rej(e);
    });
  });
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLCanvasElement", "Element", "Node",
  "Event", "getComputedStyle", "matchMedia", "requestAnimationFrame", "cancelAnimationFrame",
  "fetch", "location",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { TileEngine } = await import("../TileEngine");
const { tileOrderFor, visibleTiles } = await import("../../../lib/tileView");
const { planFetches } = await import("../../../lib/tileCache");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

/** Let real time pass inside act(), so the rAF loop and the fetches run. */
async function settle(ms: number): Promise<void> {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
  }
}

// The engine's fallback width when the canvas has no layout (jsdom), and the
// ancestor walk it plans with: both read from TileEngine.tsx, not chosen here.
const VIEW_PX = 360;
const PARENT_WALK = 5;
const SLUG = "dss2color";

interface View { raDeg: number; decDeg: number; fovDeg: number }
/** The view's fetch plan as the engine will make it on a blank canvas, as
 *  "order/npix" keys, and how many of them are visible tiles. */
function planOf(v: View): { keys: string[]; order: number; visible: number } {
  const order = tileOrderFor(v.fovDeg, VIEW_PX);
  const tiles = visibleTiles(v.raDeg, v.decDeg, v.fovDeg, VIEW_PX, order);
  const plan = planFetches(tiles, order, v.raDeg, v.decDeg, () => false, PARENT_WALK);
  return { keys: plan.map((j) => `${j.order}/${j.npix}`), order, visible: tiles.length };
}

/** Mount one engine on a view with `rule` deciding each tile's answer; let
 *  it run for `ms`, then unmount it. Answers how often it reported the view
 *  all-failing, and what it asked. The whole window is always run, so "once"
 *  and "each tile asked once" are graded over all of it. */
async function run(v: View, rule: (key: string, n: number) => Answer,
                   ms: number): Promise<{ allFailing: number; asked: string[] }> {
  net.rule = rule;
  net.asked = [];
  let allFailing = 0;
  const container = win.document.getElementById("root");
  const root = createRoot(container);
  try {
    act(() => {
      root.render(createElement(TileEngine, {
        centerRaDeg: v.raDeg, centerDecDeg: v.decDeg, fovDeg: v.fovDeg, slug: SLUG,
        onlineFetch: false, brightness: 1,
        onFirstTile: () => {}, onAllFailing: () => { allFailing++; },
      }));
    });
    await settle(ms);
    return { allFailing, asked: [...net.asked] };
  } finally {
    act(() => { root.unmount(); });
  }
}

// The two widths the compass sky and a framed field open at, on one centre
// (M31's catalogued position, as a plain number).
const WIDE: View = { raDeg: 10.68, decDeg: 41.269, fovDeg: 55 };
const FIELD: View = { raDeg: 10.68, decDeg: 41.269, fovDeg: 4 };
/** A view of three visible tiles whose plan is twelve: each tile brings its
 *  own ancestor chain down to order 0. */
const DEEP: View = { raDeg: 90, decDeg: 41.269, fovDeg: 6 };

// ======================================================================

// Mutant "fixed 8" (the threshold back to `consecFail.current >= 8`, as
// before this change): 3/4 passed, this case red:
//   x a 3-tile view with every tile 404 reports onAllFailing in one pass: the
//     view's 3 tiles all answered 404 (asked ["0/0","0/3","0/4"]) and the
//     engine did not call it all-failing within 1500 ms: it waits for 8
//     consecutive failures, which a 3-tile view reaches only after the 45 s
//     negative cache has let it ask twice more
await test("a 3-tile view with every tile 404 reports onAllFailing in one pass", async () => {
  const p = planOf(WIDE);
  assert(p.order === 0 && p.visible === 3 && p.keys.length === 3,
    `fixture: the 55 deg view is not 3 order-0 tiles with no ancestors (order ${p.order}, ${p.visible} visible, plan ${JSON.stringify(p.keys)})`);
  const r = await run(WIDE, () => "404", 1500);
  assert(r.allFailing === 1,
    `the view's 3 tiles all answered 404 (asked ${JSON.stringify(r.asked)}) and the engine did not call it all-failing within 1500 ms: ` +
    "it waits for 8 consecutive failures, which a 3-tile view reaches only after the 45 s negative cache has let it ask twice more");
  // One pass: each tile the view needs was asked exactly once.
  const sorted = [...r.asked].sort();
  assert(JSON.stringify(sorted) === JSON.stringify([...p.keys].sort()),
    `one pass is each of ${JSON.stringify(p.keys)} asked once; the engine asked ${JSON.stringify(r.asked)}`);
});

// A view that needs more than 8 tiles is all-failing at its 8th failure, as it
// always was: the cap is what keeps a big view from waiting for every tile.
//
// Mutant "no cap" (the threshold is every tile the view needs,
// `Math.max(1, need)`, with no min(8, ...)): 3/4 passed, this case red:
//   x a 12-tile view is all-failing at its 8th failure, with 4 tiles still
//     out: after 8 404s of the view's 12 tiles, with 4 still out, the engine
//     did not call it all-failing (asked 12:
//     ["4/169","3/42","2/10","1/2","0/0","4/166","3/41","4/163","3/40","4/172","3/43","4/168"])
await test("a 12-tile view is all-failing at its 8th failure, with 4 tiles still out", async () => {
  const p = planOf(FIELD);
  assert(p.keys.length === 12, `fixture: the 4 deg view does not need 12 tiles (plan ${JSON.stringify(p.keys)})`);
  const r = await run(FIELD, (_k, n) => (n <= 8 ? "404" : "hold"), 1500);
  assert(r.asked.length === 12, `precondition: the engine asked ${r.asked.length} of the view's 12 tiles`);
  assert(r.allFailing === 1,
    `after 8 404s of the view's 12 tiles, with 4 still out, the engine did not call it all-failing (asked ${r.asked.length}: ${JSON.stringify(r.asked)})`);
});

// Control: seven failures of a view that needs twelve, the other five still
// out, is a view still loading.
//
// Mutant "any failure" (a blank view is all-failing on its first failure,
// `consecFail.current >= 1`): 2/4 passed, both controls red:
//   x control: a 12-tile view with 7 failures is not all-failing: the engine
//     called a 12-tile view all-failing after 7 404s, with 5 tiles still out
//     (asked ["4/169","3/42","2/10","1/2","0/0","4/166","3/41","4/163","3/40","4/172","3/43","4/168"])
//   x control: a view of 3 visible tiles is not all-failing while the coarser
//     tiles that could still draw it are out: the engine called the view
//     all-failing when its 3 visible tiles and 3 of their ancestors had
//     failed, with 6 coarser tiles that could still draw it out (asked
//     ["3/21","2/5","1/1","0/0","3/106","2/26","1/6","0/1","3/383","2/95","1/23","0/5"])
// With this control, the case above pins the threshold of a big view at
// exactly 8: not before (here), and by then (above).
await test("control: a 12-tile view with 7 failures is not all-failing", async () => {
  const p = planOf(FIELD);
  assert(p.keys.length === 12, `fixture: the 4 deg view does not need 12 tiles (plan ${JSON.stringify(p.keys)})`);
  const r = await run(FIELD, (_k, n) => (n <= 7 ? "404" : "hold"), 1000);
  assert(r.asked.length === 12, `precondition: the engine asked ${r.asked.length} of the view's 12 tiles, not all of them`);
  assert(r.allFailing === 0,
    `the engine called a 12-tile view all-failing after 7 404s, with 5 tiles still out (asked ${JSON.stringify(r.asked)})`);
});

// Control: what a view needs is its PLAN, not its visible tiles. Three visible
// tiles failing while the coarser tiles that could be upsampled into them are
// still out is not a view with no source: any one of those can still draw it.
//
// Mutant "count visible tiles" (what the view needs is its visible tiles,
// `viewNeeds.current = tiles.length`, the ancestors left out): 2/4 passed,
// both controls red (the 4 deg view has 5 visible tiles, so its threshold
// fell to 5, under the 7 failures of the control above):
//   x control: a 12-tile view with 7 failures is not all-failing: the engine
//     called a 12-tile view all-failing after 7 404s, with 5 tiles still out
//     (asked ["4/169","3/42","2/10","1/2","0/0","4/166","3/41","4/163","3/40","4/172","3/43","4/168"])
//   x control: a view of 3 visible tiles is not all-failing while the coarser
//     tiles that could still draw it are out: the engine called the view
//     all-failing when its 3 visible tiles and 3 of their ancestors had
//     failed, with 6 coarser tiles that could still draw it out (asked
//     ["3/21","2/5","1/1","0/0","3/106","2/26","1/6","0/1","3/383","2/95","1/23","0/5"])
await test("control: a view of 3 visible tiles is not all-failing while the coarser tiles that could still draw it are out", async () => {
  const p = planOf(DEEP);
  assert(p.order === 3 && p.visible === 3 && p.keys.length === 12,
    `fixture: the 6 deg view is not 3 order-3 tiles with a 12-tile plan (order ${p.order}, ${p.visible} visible, plan ${JSON.stringify(p.keys)})`);
  // Orders 3 and 2 fail; orders 1 and 0, the coarsest fills, are still out.
  const rule = (key: string): Answer => (Number(key.split("/")[0]) >= 2 ? "404" : "hold");
  const r = await run(DEEP, rule, 1000);
  const failedKeys = r.asked.filter((k) => rule(k) === "404");
  const visibleFailed = failedKeys.filter((k) => k.startsWith(`${p.order}/`)).length;
  assert(visibleFailed === 3, `precondition: ${visibleFailed} of the view's 3 visible tiles were asked and failed (asked ${JSON.stringify(r.asked)})`);
  assert(failedKeys.length < 8, `precondition: ${failedKeys.length} failures, at or over the cap of 8, would be all-failing by the cap alone`);
  assert(r.allFailing === 0,
    `the engine called the view all-failing when its 3 visible tiles and ${failedKeys.length - 3} of their ancestors had failed, ` +
    `with ${r.asked.length - failedKeys.length} coarser tiles that could still draw it out (asked ${JSON.stringify(r.asked)})`);
});

// ------------------------------------------------------------------- report
dom.window.close();
const total = passed + failed;
console.log(`tileEngineAllFailing.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
