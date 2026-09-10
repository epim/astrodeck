// planningStore.test.tsx - the planning store: one fetch, two merge rules, one
// rename, and a fallback that cannot delete anything (D-FU-1, T-U7b-11).
//
//   Run directly:  npx tsx src/next/hubs/sky/__tests__/planningStore.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// Convention: jsdom by hand, createRoot + act, printed tally plus the
// `{ passed, failed, total }` export (shell-and-tests.md section 4). The store
// is a hook over module state, so it is graded through a probe component that
// renders nothing and reports what a real sheet would read.
//
// What each test guards, and what goes RED when the guard is removed:
//
//  1. THE PROBE SEES A RIG BLOCK AT ALL, and two mounted consumers cost ONE
//     `GET /api/planning`. Vacuity guard first: without it every "sent exactly"
//     assertion below could be passing over a store that never loaded.
//  2. `putQuick({hours: 3})` sends `{"quick":{"hours":3}}` and NOTHING else.
//     The server merges the quick block nested-partially
//     (`planning.py:122-153`); a whole-block send looks identical in a
//     round-trip and erases every per-filter exposure a narrower client never
//     knew about. Named sabotage: send the whole block and this goes red.
//  3. THE ONE RENAME. `ditherN` is `dither_n` on the wire, in one place, both
//     directions, and the mapping table covers every field of `QuickPrefs` -
//     so a field added to the browser shape and not to the table is caught
//     here rather than by a night that quietly did not dither.
//  4. `putPool` sends the WHOLE array, because the server replaces it whole.
//  5. A 404 FROM THE GET FALLS BACK TO THE BROWSER KEYS AND DELETES NOTHING.
//     This is the P0: an engine with no `/api/planning` must not cost anyone
//     their learned exposures. It also asserts NO migration flag was written,
//     because a flag is what would stop the retry on the day the rig is
//     upgraded.
//  6. The migration itself: a phone with a key and a rig that has learned
//     nothing sends the values up ONCE, carrying `learned: true` (without it
//     the settings sheet would say nothing had ever been learned the moment
//     after it moved), and the key is then gone.
//  7. A viewer reads the same block, is given the reason in words, and cannot
//     write it.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// Nothing here imports a stylesheet today, but the store is imported by sheets
// that do, and a `.css` reaching Node is an unhelpful crash rather than a
// failure. The same synchronous hook every DOM test in this tree installs.
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
/** The rig's block, and whether it has one at all. `null` answers 404 - the
 *  engine that predates wave S7. */
let block: any = null;
const ok = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK",
  headers: { get: () => "application/json" },
  json: async () => data,
});
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });
  if (u.includes("/api/planning")) {
    if (block == null) {
      return {
        ok: false, status: 404, statusText: "Not Found",
        headers: { get: () => "application/json" },
        json: async () => ({ detail: "Not Found" }),
      };
    }
    if (method === "PUT") {
      // The server's own merge: `quick` nested-partially, `pool` whole.
      if (body?.quick) block.quick = { ...block.quick, ...body.quick };
      if (body?.pool) block.pool = body.pool;
    }
    return ok({ quick: { ...block.quick }, pool: [...block.pool] });
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const {
  usePlanning, resetPlanningForTests, quickToWire, quickFromWire,
} = await import("../../../lib/planning");
const { DEFAULT_QUICK, LEGACY_RIG_KEYS } = await import("../finder/prefs");
const { migrationFlagKey } = await import("../../../lib/storageMigration");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const OPERATOR = {
  role: "operator", email: "op@example.test",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: "guest@example.test", caps: ["view.status"] };

const LEARNED = () => ({
  quick: {
    hours: 4, dawn: false, on: { L: true, Ha: false }, exp: { L: 60, Ha: 300 },
    extras: { af: true }, dither_n: 7, learned: true,
  },
  pool: ["M31", "NGC 7331"],
});

let seen: ReturnType<typeof usePlanning> | null = null;
function Probe(): null { seen = usePlanning(); return null; }

const host = win.document.getElementById("root") as any;

/** A fresh app session: no module memory, no keys, no recorded requests. */
async function open(principal: unknown, consumers = 1) {
  resetPlanningForTests();
  win.localStorage.clear();
  asks.length = 0;
  seen = null;
  useStore.setState({ principal, wsPhase: "up", equipConnected: true } as never);
  const root = createRoot(host);
  act(() => {
    root.render(createElement(
      "div", null,
      ...Array.from({ length: consumers }, (_, i) => createElement(Probe, { key: i })),
    ));
  });
  await settle();
  return root;
}
const planningAsks = (method: string) =>
  asks.filter((a) => a.method === method && a.url.includes("/api/planning"));

// ============================================ 1. one fetch, two consumers
{
  block = LEARNED();
  const root = await open(OPERATOR, 2);

  await test("two mounted consumers share ONE GET, and both see the rig's block", () => {
    eq(planningAsks("GET").length, 1, "GET /api/planning fired:");
    eq(seen?.mode, "rig", "the mode after the rig answered:");
    eq(seen?.quick.hours, 4, "the rig's night length:");
    eq(seen?.pool.join(","), "M31,NGC 7331", "the rig's shortlist, in its order:");
    eq(seen?.learned, true, "the rig's learned flag:");
    eq(seen?.lockedReason, null, "an operator's lock reason:");
  });

  await test("the wire's dither_n arrives as the browser's ditherN", () => {
    eq(seen?.quick.ditherN, 7, "dither_n 7 read back as ditherN:");
  });

  act(() => { root.unmount(); });
}

// ==================================== 2. the nested partial, key by key
{
  block = LEARNED();
  const root = await open(OPERATOR);
  act(() => { seen?.putQuick({ hours: 3 }); });
  await settle();

  await test("putQuick sends exactly the changed key, so learned exposures survive", () => {
    const puts = planningAsks("PUT");
    eq(puts.length, 1, "writes sent:");
    eq(JSON.stringify(puts[0].body), JSON.stringify({ quick: { hours: 3 } }),
      "the body of PUT /api/planning:");
    eq(seen?.quick.exp.Ha, 300, "the exposure nobody touched:");
    eq(seen?.quick.hours, 3, "and the hours that moved:");
  });

  act(() => { seen?.putQuick({ ditherN: 5 }); });
  await settle();

  await test("the ONE rename happens at the boundary and nowhere else", () => {
    const last = planningAsks("PUT").slice(-1)[0];
    eq(JSON.stringify(last.body), JSON.stringify({ quick: { dither_n: 5 } }),
      "ditherN on the wire:");
    eq(seen?.quick.ditherN, 5, "and back off it:");
  });

  await test("the mapping table covers every field of the browser shape", () => {
    eq(Object.keys(DEFAULT_QUICK).sort().join(","), "dawn,ditherN,exp,extras,hours,on",
      "the browser's own fields:");
    eq(Object.keys(quickToWire(DEFAULT_QUICK)).sort().join(","),
      "dawn,dither_n,exp,extras,hours,on",
      "the fields that cross to the wire (a field missing from the table is dropped):");
    eq(quickFromWire(null).ditherN, DEFAULT_QUICK.ditherN,
      "a rig with no block at all falls back to the shipped default:");
  });

  act(() => { root.unmount(); });
}

// ============================================== 3. the pool, replaced whole
{
  block = LEARNED();
  const root = await open(OPERATOR);
  act(() => { seen?.putPool(["M31", "M33", "NGC 7331"]); });
  await settle();

  await test("putPool sends the whole array, in the user's order", () => {
    const puts = planningAsks("PUT");
    eq(puts.length, 1, "writes sent:");
    eq(JSON.stringify(puts[0].body),
      JSON.stringify({ pool: ["M31", "M33", "NGC 7331"] }), "the body:");
    eq(seen?.pool.join(","), "M31,M33,NGC 7331", "the shortlist the Sky hub renders:");
  });

  act(() => { root.unmount(); });
}

// =========================== 4. an engine with no planning block (the P0)
{
  block = null;
  resetPlanningForTests();
  win.localStorage.clear();
  win.localStorage.setItem(LEGACY_RIG_KEYS.quick, JSON.stringify({ hours: 6, ditherN: 2 }));
  win.localStorage.setItem(LEGACY_RIG_KEYS.pool, JSON.stringify(["M42"]));
  asks.length = 0;
  useStore.setState({ principal: OPERATOR, wsPhase: "up" } as never);
  const root = createRoot(host);
  act(() => { root.render(createElement(Probe)); });
  await settle();

  await test("a 404 falls back to the browser keys and deletes nothing", () => {
    eq(seen?.mode, "local", "the mode on an engine that has no planning block:");
    eq(seen?.quick.hours, 6, "the hours this phone remembers:");
    eq(seen?.pool.join(","), "M42", "the shortlist this phone remembers:");
    eq(planningAsks("PUT").length, 0, "writes to a rig that cannot store them:");
    assert(win.localStorage.getItem(LEGACY_RIG_KEYS.quick) != null,
      "the quick key was deleted on a rig with nowhere to put it");
    assert(win.localStorage.getItem(LEGACY_RIG_KEYS.pool) != null,
      "the pool key was deleted on a rig with nowhere to put it");
    eq(win.localStorage.getItem(migrationFlagKey(LEGACY_RIG_KEYS.quick)), null,
      "a migration flag that would stop the retry after an upgrade:");
    eq(seen?.error, null, "an older engine is not an error to report:");
    eq(seen?.lockedReason, null, "and nothing is locked - the phone still remembers:");
  });

  await test("a write in local mode still persists, exactly as it shipped", () => {
    act(() => { seen?.putQuick({ hours: 5 }); });
    eq(seen?.quick.hours, 5, "the store:");
    const raw = JSON.parse(win.localStorage.getItem(LEGACY_RIG_KEYS.quick) ?? "{}");
    eq(raw.hours, 5, "and the browser key behind it:");
    eq(planningAsks("PUT").length, 0, "requests sent to an engine that has no route:");
  });

  act(() => { root.unmount(); });
}

// ================================== 5. the migration, on a rig that can store
{
  block = { quick: { ...LEARNED().quick, learned: false, on: {}, exp: {} }, pool: [] };
  resetPlanningForTests();
  win.localStorage.clear();
  win.localStorage.setItem(LEGACY_RIG_KEYS.quick,
    JSON.stringify({ hours: 6, ditherN: 2, on: { L: true }, exp: { L: 90 } }));
  win.localStorage.setItem(LEGACY_RIG_KEYS.pool, JSON.stringify(["M42"]));
  win.localStorage.setItem(LEGACY_RIG_KEYS.site, "loc-3");
  asks.length = 0;
  useStore.setState({ principal: OPERATOR, wsPhase: "up" } as never);
  const root = createRoot(host);
  act(() => { root.render(createElement(Probe)); });
  await settle();

  await test("the phone's copy moves up once, carrying the learned flag, and the keys go", () => {
    const puts = planningAsks("PUT");
    eq(puts.length, 2, "writes (one per key that had a value):");
    eq(puts[0].body.quick.hours, 6, "the hours that moved:");
    eq(puts[0].body.quick.dither_n, 2, "under the wire's spelling:");
    eq(puts[0].body.quick.learned, true,
      "the learned flag - without it the settings sheet says nothing was ever learned:");
    eq(JSON.stringify(puts[1].body), JSON.stringify({ pool: ["M42"] }), "the pool:");
    eq(win.localStorage.getItem(LEGACY_RIG_KEYS.quick), null, "the quick key afterwards:");
    eq(win.localStorage.getItem(LEGACY_RIG_KEYS.pool), null, "the pool key afterwards:");
    eq(win.localStorage.getItem(LEGACY_RIG_KEYS.site), null,
      "the orphan site key, which no UI has ever read back:");
    eq(seen?.quick.exp.L, 90, "and the store now reads the rig's copy of it:");
    eq(seen?.learned, true, "learned, as the settings sheet reads it:");
  });

  act(() => { root.unmount(); });
}

// =================================== 6. a rig that already knows better
{
  block = LEARNED();
  resetPlanningForTests();
  win.localStorage.clear();
  win.localStorage.setItem(LEGACY_RIG_KEYS.quick, JSON.stringify({ hours: 6 }));
  asks.length = 0;
  useStore.setState({ principal: OPERATOR, wsPhase: "up" } as never);
  const root = createRoot(host);
  act(() => { root.render(createElement(Probe)); });
  await settle();

  await test("the rig's own block wins and the phone's copy is dropped, not merged", () => {
    eq(planningAsks("PUT").length, 0, "writes over the top of a rig that has learned:");
    eq(seen?.quick.hours, 4, "the hours on screen are the rig's:");
    eq(win.localStorage.getItem(LEGACY_RIG_KEYS.quick), null, "the phone's copy:");
  });

  act(() => { root.unmount(); });
}

// ============================================================ 7. the viewer
{
  block = LEARNED();
  const root = await open(VIEWER);
  act(() => { seen?.putQuick({ hours: 3 }); });
  act(() => { seen?.putPool(["M31"]); });
  await settle();

  await test("a viewer reads the same block, is told why, and writes nothing", () => {
    eq(seen?.quick.hours, 4, "the plan a viewer sees:");
    eq(seen?.lockedReason, "needs operator or admin access", "the reason:");
    eq(planningAsks("PUT").length, 0, "writes from a role without control.capture:");
    eq(seen?.quick.hours, 4, "and the local copy did not move either:");
  });

  act(() => { root.unmount(); });
}

resetPlanningForTests();

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`planningStore.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);

export default { passed, failed, total };
export { passed, failed, total };
