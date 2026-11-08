// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w4_1aQuickSheetChipsDom.test.tsx - the quick sheet's chips, MOUNTED and
// pressed: each one reaches the server (#820, #843; wave 4, WP 4.1a).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/sheets/__tests__/w4_1aQuickSheetChipsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE CLASS: a claim nothing keeps. The sheet persisted six automation chips
// and posted one of them (`guided`), so turning Autofocus, HFR watchdog or
// Dither off, or moving "Dither every N", changed nothing. Now the POSTED BODY
// carries every chip that shapes the graph and the dither N, the chips a quick
// flow cannot carry (Cloud hold, Live stack) are not offered, and the flats chip
// creates its block with the flat panel when one is connected and the twilight
// sky otherwise. The server half is `server/tests/test_w4_quick_chips.py`.
//
// NAMED MUTANT (from a byte backup, restored byte-identically with sha256
// compared): the payload drops the dither N (`quickPayload.ts` writing
// `dither_every: 3`): the dither case fails on the body.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/sky/quick?target=m31", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "localStorage", "sessionStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// The persisted per-phone default, written BEFORE the first render: the sheet
// reads it in a lazy initialiser, and six hours is the window the 51 passes are
// counted over.
win.localStorage.setItem("astrodeck-next-sky-quick", JSON.stringify({ hours: 6 }));
const PREFS_KEY = "astrodeck-next-sky-quick";

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
const M31 = {
  id: "m31", name: "Andromeda Galaxy", type: "Galaxy", kind: "dso",
  ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190,
  difficulty: "easy",
};
const QUICK_FLOW = {
  flow: {
    id: "f1", name: "Quick session: Andromeda Galaxy", folder: "My flows",
    tagline: "",
    // The lane the server generates, in the two nodes the chips' edits hang off.
    graph: {
      nodes: [
        { id: "n1", type: "dusk", x: 30, y: 60, params: {} },
        { id: "n2", type: "target", x: 100, y: 60, params: {} },
        { id: "n6", type: "report", x: 258, y: 60, params: {} },
      ],
      edges: [
        { id: "we0", from: "n1", fromPort: "window", to: "n2", toPort: "arm" },
        { id: "we1", from: "n2", fromPort: "target", to: "n6", toPort: "session" },
      ],
    },
    created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false,
  },
  started: false,
};

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });
  const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/catalog?q=")) return ok({ results: [M31], notes: [] });
  if (u.includes("/api/visibility")) return ok({});
  if (u.includes("/api/flows/quick")) return ok(QUICK_FLOW);
  if (u.includes("/api/flows/compile")) return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  if (u.includes("/api/flows/f1")) return ok(QUICK_FLOW.flow);
  return ok({});
};
const quickPosts = () => asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/quick"));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { QuickSessionSheet } = await import("../quick");
const { resetPlanningForTests } = await import("../../../../lib/planning");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount", "control.guide"],
};

function seed(principal: unknown = OPERATOR): void {
  useStore.setState({
    principal,
    equipConnected: true,
    wsPhase: "up",
    site: { name: "Back lawn", latitude: 47.6, longitude: -122.3, elevation_m: 50, is_default: false, horizon_min_deg: 20 },
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim" } },
      filterwheel: {
        position: 0,
        names: ["L", "R", "G", "B", "Ha", "Dark"],
        // The sixth slot holds NO GLASS. It must not reach the cycle.
        opaque: [false, false, false, false, false, true],
        narrowband: [false, false, false, false, true, false],
        exposures: [60, 60, 60, 60, 180, null],
      },
    },
    weather: null,
    frameSettings: {
      capture: { exposure_s: 60, gain: 100, offset: 30, binning: 1, filter: null },
      focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
      solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
      guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
    },
  } as never);
}


const chipLabels = () => qa('[data-testid="quick-auto"]').map((c) => String(c.textContent).trim());

async function mountWith(prefs: Record<string, unknown>, extraStatus: Record<string, unknown> = {}) {
  win.localStorage.setItem(PREFS_KEY, JSON.stringify({ hours: 6, ...prefs }));
  resetPlanningForTests();
  const r = createRoot(container);
  seed();
  if (Object.keys(extraStatus).length) {
    const st = useStore.getState().status as any;
    useStore.setState({ status: { ...st, ...extraStatus } } as never);
  }
  await act(async () => {
    r.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
  });
  await settle();
  return r;
}

async function generate(): Promise<any> {
  const before = quickPosts().length;
  await act(async () => {
    q('[data-testid="quick-generate"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();
  const posts = quickPosts();
  eq(posts.length - before, 1, "exactly one POST /api/flows/quick");
  return posts[posts.length - 1].body;
}

await testAsync("only the chips a quick flow carries are offered", async () => {
  const r = await mountWith({});
  const labels = chipLabels();
  eq(labels.length, 4, `four chips are offered, got ${JSON.stringify(labels)}`);
  for (const gone of ["Cloud hold", "Live stack"]) {
    assert(!labels.some((l) => l.includes(gone)), `${gone} is offered, but a quick flow reaches nothing for it`);
  }
  for (const kept of ["Autofocus", "Guide", "Dither", "HFR watchdog"]) {
    assert(labels.some((l) => l.includes(kept)), `${kept} is not offered`);
  }
  await act(async () => { r.unmount(); });
});

await testAsync("every chip on posts its defaults: autofocus, watchdog, dither every 3", async () => {
  const r = await mountWith({});
  const body = await generate();
  eq(body.autofocus, true, "autofocus");
  eq(body.watchdog, true, "watchdog");
  eq(body.dither_every, 3, "dither_every");
  eq(body.guided, true, "guided");
  await act(async () => { r.unmount(); });
});

await testAsync("Autofocus off and HFR watchdog off post false", async () => {
  const r = await mountWith({ extras: { af: false, hfr: false } });
  const body = await generate();
  eq(body.autofocus, false, "autofocus off");
  eq(body.watchdog, false, "watchdog off");
  eq(body.dither_every, 3, "an untouched dither");
  await act(async () => { r.unmount(); });
});

await testAsync("the Dither chip's N is posted, and off is 0", async () => {
  let r = await mountWith({ ditherN: 5 });
  assert(chipLabels().some((l) => l.includes("every 5")), `the chip does not say every 5: ${chipLabels()}`);
  eq((await generate()).dither_every, 5, "the sheet's N");
  await act(async () => { r.unmount(); });
  r = await mountWith({ ditherN: 5, extras: { dither: false } });
  eq((await generate()).dither_every, 0, "the chip off");
  await act(async () => { r.unmount(); });
});

async function savedFlatsMethod(extraStatus: Record<string, unknown>): Promise<string | null> {
  const r = await mountWith({ extras: { flats: true } }, extraStatus);
  const puts = () => asks.filter((a) => a.method === "PUT" && a.url.includes("/api/flows/f1"));
  const before = puts().length;
  await generate();
  const sent = puts();
  await act(async () => { r.unmount(); });
  if (sent.length === before) return null;
  const nodes = sent[sent.length - 1].body.flow.graph.nodes as any[];
  return nodes.find((n) => n.type === "duskflats")?.params.method ?? null;
}

await testAsync("the flats chip creates the twilight sky, or the flat panel when one is connected", async () => {
  eq(await savedFlatsMethod({}), "Twilight sky", "no panel connected");
  const withPanel = {
    connected: {
      camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim" },
      covercalibrator: { connected: true, name: "panel" },
    },
  };
  eq(await savedFlatsMethod(withPanel), "Flat panel", "a panel connected");
  const links = { backend_links: [{ role: "covercalibrator", ok: true, error: null, attempted: true, connected: false }] };
  eq(await savedFlatsMethod({ ...withPanel, ...links }), "Twilight sky",
    "a bridged rig's backend_links say the panel is not connected");
});

const total = passed + failed;
console.log(`w4_1aQuickSheetChipsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
