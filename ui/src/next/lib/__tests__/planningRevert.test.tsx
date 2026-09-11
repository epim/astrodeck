// planningRevert.test.tsx - a write that FAILS must not revert a later write
// that SUCCEEDED (R4/R9 P1, `planning.ts:322,345,371`).
//
//   Run directly:  npx tsx src/next/lib/__tests__/planningRevert.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT WAS WRONG. Each of the three writers snapshots `const before = state`,
// publishes the optimistic value, and on rejection publishes `{...before}`.
// The SUCCESS half already consulted `writeSeq` before publishing; the
// rejection half did not. So with two writes in flight - a phone on a slow
// relay, a user moving two controls, or one control moved twice - an EARLIER
// write failing after a LATER one was acknowledged put the whole block back to
// where it stood two edits ago. The rig kept the newer value; the screen showed
// the older one; nothing said so.
//
// The ordering below is the one that produces it, and it is not exotic: write
// A goes out, write B goes out, B is acknowledged, A times out.
//
// SABOTAGE: delete `if (seq !== writeSeq) return;` from any of the three
// rejection handlers in `planning.ts` and the matching test here goes red -
// quick at :322 ("a failed earlier quick write..."), pool at :345, forget at
// :371 - each showing the older value back on screen.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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

// ------------------------------------------------------- a controllable PUT
//
// Every `PUT /api/planning` parks here until the test says how it ends, which
// is the only way to interleave two of them. The GET answers immediately: the
// store's one fetch is not what this file is about.

interface Parked {
  body: any;
  /** Answer 200 with the rig's block as it would be after this write. */
  ok(block: unknown): void;
  /** Answer 503, the shape a relay drop produces. */
  fail(): void;
}
const parked: Parked[] = [];
let block: any = null;

const okRes = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK",
  headers: { get: () => "application/json" },
  json: async () => data,
});

g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  if (u.includes("/api/planning")) {
    if (method === "GET") return okRes({ quick: { ...block.quick }, pool: [...block.pool] });
    const body = init?.body ? JSON.parse(init.body) : null;
    return new Promise((resolve) => {
      parked.push({
        body,
        ok: (b) => resolve(okRes(b)),
        fail: () => resolve({
          ok: false, status: 503, statusText: "Service Unavailable",
          headers: { get: () => "application/json" },
          json: async () => ({ detail: "the rig did not answer" }),
        }),
      });
    });
  }
  return okRes({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { usePlanning, resetPlanningForTests } = await import("../planning");

// ------------------------------------------------------------------ harness
let passed = 0, failed = 0; const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
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

async function open() {
  resetPlanningForTests();
  win.localStorage.clear();
  parked.length = 0;
  seen = null;
  block = LEARNED();
  useStore.setState({ principal: OPERATOR, wsPhase: "up", equipConnected: true } as never);
  const root = createRoot(host);
  act(() => { root.render(createElement(Probe)); });
  await settle();
  return root;
}

// =============================================== vacuity guard: it loaded
{
  const root = await open();
  await test("the probe is reading the RIG's block, so the reverts below are real", () => {
    eq(seen?.mode, "rig", "the store's mode after the GET:");
    eq(seen?.quick.hours, 4, "the rig's night length:");
    eq(parked.length, 0, "nothing was written just by mounting:");
  });
  act(() => { root.unmount(); });
}

// ====================================== 1. sendQuick (planning.ts:322)
{
  const root = await open();

  act(() => { seen?.putQuick({ hours: 3 }); });   // write A
  await settle();
  act(() => { seen?.putQuick({ hours: 9 }); });   // write B
  await settle();

  await test("two quick writes are both in flight", () => {
    eq(parked.length, 2, "PUTs parked:");
    eq(seen?.quick.hours, 9, "the optimistic value on screen is the newer one:");
  });

  // B lands first: the rig has hours 9.
  await act(async () => {
    parked[1].ok({ quick: { ...block.quick, hours: 9 }, pool: [...block.pool] });
  });
  await settle();

  await test("the newer write is acknowledged and published", () => {
    eq(seen?.quick.hours, 9, "after the rig answered B:");
    eq(seen?.savedOnce, true, "the rig has acknowledged a write this session:");
  });

  // ...and now A fails. Without the guard this republishes the snapshot taken
  // BEFORE A - hours 4 - and the screen silently disagrees with the rig.
  await act(async () => { parked[0].fail(); });
  await settle();

  await test("a failed earlier quick write does not revert the later one", () => {
    eq(seen?.quick.hours, 9, "the value the rig actually holds:");
    eq(seen?.error, null, "a stale failure is not news the user can act on:");
  });

  act(() => { root.unmount(); });
}

// ======================================= 2. sendPool (planning.ts:345)
{
  const root = await open();

  act(() => { seen?.putPool(["M31"]); });                       // write A
  await settle();
  act(() => { seen?.putPool(["M31", "M33", "NGC 7331"]); });     // write B
  await settle();

  await act(async () => {
    parked[1].ok({ quick: { ...block.quick }, pool: ["M31", "M33", "NGC 7331"] });
  });
  await settle();
  await act(async () => { parked[0].fail(); });
  await settle();

  await test("a failed earlier pool write does not revert the later shortlist", () => {
    eq(seen?.pool.join(","), "M31,M33,NGC 7331", "the shortlist the rig holds:");
    eq(seen?.error, null, "a stale failure is not news the user can act on:");
  });

  act(() => { root.unmount(); });
}

// ==================================== 3. forgetQuick (planning.ts:371)
//
// The other direction, and the one that loses the most: FORGET goes out, then
// the user sets a fresh value, the fresh value lands, and the forget fails. The
// unguarded revert would put `learned: false` and the cleared block back over
// the value the rig just accepted.
{
  const root = await open();

  act(() => { seen?.forgetQuick(); });            // write A: clear everything
  await settle();
  act(() => { seen?.putQuick({ hours: 6 }); });   // write B: a fresh value
  await settle();

  await act(async () => {
    parked[1].ok({ quick: { ...block.quick, hours: 6, learned: true }, pool: [...block.pool] });
  });
  await settle();
  await act(async () => { parked[0].fail(); });
  await settle();

  await test("a failed FORGET does not undo the write that landed after it", () => {
    eq(seen?.quick.hours, 6, "the value the rig holds:");
    eq(seen?.learned, true, "and its learned flag:");
    eq(seen?.error, null, "a stale failure is not news the user can act on:");
  });

  act(() => { root.unmount(); });
}

// ============================ 4. the guard does NOT swallow a live failure
{
  const root = await open();
  act(() => { seen?.putQuick({ hours: 3 }); });
  await settle();
  await act(async () => { parked[0].fail(); });
  await settle();

  await test("the ONLY write in flight failing still reverts and reports", () => {
    eq(seen?.quick.hours, 4, "back to what the rig last confirmed:");
    eq(typeof seen?.error, "string", "and the failure is recorded, not swallowed:");
  });

  act(() => { root.unmount(); });
}

console.log(`planningRevert.test: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export { passed, failed };
export const total = passed + failed;
