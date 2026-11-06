// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w3_3PanelPickerSurfaces.test.tsx - the session-stack surfaces that had no
// panel picker (#781, #1113 part a): the Gallery's SessionStackPanel, the classic
// Monitor's SessionStack and the Rig inspect sheet's StackInspect.
//
//   Run directly:  npx tsx src/components/__tests__/w3_3PanelPickerSurfaces.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THE FIXTURE IS. A fake server that answers `GET /api/sequence/stack` the
// way the real one does: with no `?panel=` it describes the LATEST panel (the one
// with the highest `seq`), with `?panel=<key>` it describes that panel, and
// `panels` is the whole list either way. The same cases run on each surface:
//
//   1. FOLLOWING THE LATEST asks for the URLs this app has always asked for, and
//      draws one chip per panel.
//   2. PRESSING A CHIP asks the server for that panel's status and builds the
//      picture's URL from THAT panel's seq and key; pressing it again lets go.
//   3. One panel draws no row.
//   4. A pin on a panel the stack no longer holds is dropped, and the surface
//      asks for the latest.
//   5. The pin is ONE value: a panel pinned on one surface is the panel the next
//      surface to mount asks for.
//   6. (Gallery) a Reset press under a pin asks for the pinned panel again
//      instead of showing the latest panel's counts beside its picture.
//
// Mutants (a byte backup in the scratch directory, restored byte-identically):
// see the WP 3.3 report.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "Image", "requestAnimationFrame", "cancelAnimationFrame",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------- the fake stack server
const asks: string[] = [];
// The surfaces poll on a timer. Recording the callbacks lets a test run "the next
// poll" without waiting ten seconds for it.
const ticks: Array<() => void> = [];
g.setInterval = (fn: () => void, ms?: number) => {
  if ((ms ?? 0) >= 1000) ticks.push(fn);
  return 0 as never;
};
g.clearInterval = () => {};

interface FakePanel { key: string; target: string; frames: number; integrated_s: number; seq: number }
let PANELS: FakePanel[] = [];

const BACKFILL = {
  running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0,
  channel: "", error: "", started_ts: null, finished_ts: null, available: 0,
};

/** The status the real route returns: one panel's numbers, the whole list. */
function statusFor(key: string | null): any {
  const latest = PANELS.reduce<FakePanel | null>((b, p) => (b === null || p.seq > b.seq ? p : b), null);
  const hit = key ? PANELS.find((p) => p.key === key) ?? null : latest;
  const panels = PANELS.map((p) => ({ ...p }));
  if (hit === null) {
    return {
      enabled: true, target: key ?? "", seq: 0, channels: [], frames: 0,
      integrated_s: 0, rejected: 0, mode: null, downsample: 0, has_image: false,
      render_age_s: null, backfill: BACKFILL, panels, evicted: [],
    };
  }
  return {
    enabled: true, target: hit.target, seq: hit.seq,
    channels: [{ channel: "Ha", frames: hit.frames, integrated_s: hit.integrated_s, rejected: 0 }],
    frames: hit.frames, integrated_s: hit.integrated_s, rejected: 0,
    mode: "narrowband", downsample: 2, has_image: true, render_age_s: 3,
    backfill: BACKFILL, panels, evicted: [],
  };
}

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push(`${method} ${u}`);
  let body: any = { ok: true };
  if (u.includes("/api/sequence/stack")) {
    // Every route answers with the LATEST panel unless a panel is asked for,
    // POSTs included: that is the behaviour a pin has to survive.
    const m = /[?&]panel=([^&]*)/.exec(u);
    body = statusFor(m ? decodeURIComponent(m[1]) : null);
  } else if (u.includes("/api/sessions")) {
    body = { sessions: [] };
  }
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => "",
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { SessionStackPanel } = await import("../../next/hubs/session/gallery/frames/SessionStackPanel");
const SessionStack = (await import("../preview/SessionStack")).default;
const { LiveStack } = await import("../../next/hubs/session/now/LiveStack");
const { StackInspect } = await import("../../next/hubs/rig/sheets/inspectStack");
const { PanelPicker } = await import("../PanelPicker");
const { resetStackViewForTests, resetSessionStackStateForTests, useStackView } =
  await import("../../next/hubs/session/now/stackView");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const click = async (id: string) => {
  const el = byId(id);
  assert(el != null, `nothing to press: ${id}`);
  await act(async () => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true })); });
  await settle();
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture"],
};
useStore.setState({
  principal: OPERATOR, equipConnected: true, wsPhase: "up", wsLastEvent: Date.now(),
  status: { connected: { camera: true }, looping: false },
  sequence: {
    state: "running", target: "Beta", plan_name: "Mosaic", target_index: 0,
    progress: { frames_done: 6, frames_total: 40, percent: 15, elapsed_s: 600, rejected: 0 },
  },
} as never);

let view: any = null;
function Probe(): null {
  view = useStackView();
  return null;
}

const root = createRoot(container);
async function mount(surface: () => any): Promise<void> {
  // UNMOUNT FIRST, THEN RESET, THEN MOUNT: see w16LiveStackPanels.test.tsx.
  await act(async () => { root.render(null); });
  resetStackViewForTests();
  resetSessionStackStateForTests();
  asks.length = 0;
  ticks.length = 0;
  await act(async () => {
    root.render(createElement(Fragment, null, createElement(Probe), surface()));
  });
  await settle();
}
/** Mount a second surface WITHOUT resetting the pin the first one left. */
async function remount(surface: () => any): Promise<void> {
  await act(async () => { root.render(null); });
  asks.length = 0;
  await act(async () => {
    root.render(createElement(Fragment, null, createElement(Probe), surface()));
  });
  await settle();
}

const TWO: FakePanel[] = [
  { key: "t-a", target: "Alpha", frames: 4, integrated_s: 240, seq: 12 },
  { key: "t-b", target: "Beta", frames: 2, integrated_s: 120, seq: 15 },
];
const LEGACY_SRC = "/api/sequence/stack/preview.jpg?size=1200&seq=15";
const ALPHA_SRC = "/api/sequence/stack/preview.jpg?size=1200&seq=12&panel=t-a";

interface Surface {
  name: string;
  make: () => any;
  picker: string;
  img: string;
}
const SURFACES: Surface[] = [
  {
    name: "gallery SessionStackPanel", make: () => createElement(SessionStackPanel),
    picker: "gallery-stack-panels", img: '[data-testid="gallery-stack-img"]',
  },
  {
    name: "classic SessionStack", make: () => createElement(SessionStack),
    picker: "session-stack-panels", img: "img",
  },
  {
    name: "Rig inspect StackInspect",
    make: () => createElement(StackInspect, { onLastSub: () => {} }),
    picker: "inspect-stack-panels", img: '[data-testid="inspect-stack-stage"] img',
  },
  {
    name: "Now LiveStack", make: () => createElement(LiveStack),
    picker: "live-stack-panels", img: '[data-testid="live-stack-image"]',
  },
];
const src = (s: Surface): string => container.querySelector(s.img)?.getAttribute("src") ?? "";
const chip = (key: string) => byId(`panel-chip-${key}`);

// ----------------------------------------------- the same cases on every surface
for (const s of SURFACES) {
  PANELS = TWO.map((p) => ({ ...p }));
  await mount(s.make);

  await testAsync(`${s.name}: following the latest asks for today's URLs and draws a chip per panel`, () => {
    assert(byId(s.picker) != null, "no chip row over two panels: the status `panels` never reached the surface");
    assert(chip("t-a") != null && chip("t-b") != null, "a panel has no chip");
    eq(src(s), LEGACY_SRC, "the picture URL changed for a surface that follows the latest panel:");
    assert(asks.every((a) => !a.includes("panel=")), `a panel parameter was sent while following: ${asks.join(" | ")}`);
    eq(chip("t-b").getAttribute("aria-pressed"), "true", "the latest panel's chip is not lit:");
    eq(chip("t-a").getAttribute("aria-pressed"), "false", "the older panel's chip is lit:");
  });

  await testAsync(`${s.name}: pressing a chip shows that panel, pressing it again lets go`, async () => {
    asks.length = 0;
    await click("panel-chip-t-a");
    assert(asks.some((a) => a === "GET /api/sequence/stack?panel=t-a"),
      `the status was not asked for Alpha: ${asks.join(" | ")}`);
    eq(src(s), ALPHA_SRC, "the picture is not Alpha's own (its seq and its key):");
    eq(chip("t-a").getAttribute("aria-pressed"), "true", "the pinned chip is not lit:");
    await click("panel-chip-t-a");
    eq(src(s), LEGACY_SRC, "letting go did not return to following the latest:");
  });

  await testAsync(`${s.name}: a pin on a panel the server later released is dropped by the next poll`, async () => {
    await click("panel-chip-t-a");
    eq(view.panel, "t-a", "the pin did not take:");
    // The server releases Alpha for memory between two polls. The surface's own
    // poll is the only thing that can notice: nothing else is mounted.
    PANELS = [TWO[1]];
    asks.length = 0;
    assert(ticks.length > 0, "the surface set no poll timer: the test cannot reach its refresh");
    await act(async () => { ticks[ticks.length - 1](); });
    await settle();
    eq(view.panel, null, "the pin on a panel the server does not have was kept:");
    eq(src(s), LEGACY_SRC, "the surface is left on a blank panel instead of the latest:");
    PANELS = TWO.map((p) => ({ ...p }));
  });

  await testAsync(`${s.name}: one panel draws no row`, async () => {
    PANELS = [TWO[1]];
    await mount(s.make);
    eq(byId(s.picker), null, "a chip row over ONE panel only repeats the badge:");
    eq(src(s), LEGACY_SRC, "the one-panel picture is not the legacy URL:");
    PANELS = TWO.map((p) => ({ ...p }));
  });
}

// ------------------------------------------------------- the pin is one value
await testAsync("a panel pinned on the gallery is the panel the inspect sheet asks for", async () => {
  PANELS = TWO.map((p) => ({ ...p }));
  await mount(SURFACES[0].make);
  await click("panel-chip-t-a");
  await remount(SURFACES[2].make);
  assert(asks.some((a) => a === "GET /api/sequence/stack?panel=t-a"),
    `the second surface did not ask for the pinned panel: ${asks.join(" | ")}`);
  eq(src(SURFACES[2]), ALPHA_SRC, "the second surface shows another panel:");
});

await testAsync("a Reset press under a pin asks for the pinned panel again", async () => {
  PANELS = TWO.map((p) => ({ ...p }));
  await mount(SURFACES[0].make);
  await click("panel-chip-t-a");
  asks.length = 0;
  await click("gallery-stack-reset");
  const post = asks.findIndex((a) => a === "POST /api/sequence/stack/reset");
  assert(post >= 0, `Reset was not posted: ${asks.join(" | ")}`);
  assert(asks.slice(post + 1).some((a) => a === "GET /api/sequence/stack?panel=t-a"),
    `the pinned panel was not asked for again after the reply: ${asks.join(" | ")}`);
});

await testAsync("a chip press on a surface with no shared poller makes one status request, not two", async () => {
  PANELS = TWO.map((p) => ({ ...p }));
  await mount(SURFACES[0].make);
  asks.length = 0;
  await click("panel-chip-t-a");
  const gets = asks.filter((a) => a === "GET /api/sequence/stack?panel=t-a");
  eq(gets.length, 1, "the press asked for the pinned status more than once (the shared store refetched for nobody):");
});

// ------------------------------------------------------ the picker on its own
await testAsync("same-named panels are told apart, and a label carries its sub count", async () => {
  await act(async () => { root.render(null); });
  await act(async () => {
    root.render(createElement(PanelPicker, {
      panels: [
        { key: "t-1", target: "M42", frames: 3, integrated_s: 1, seq: 1 },
        { key: "t-2", target: "M42", frames: 5, integrated_s: 1, seq: 2 },
      ],
      panel: null, onPick: () => {},
    }));
  });
  const row = byId("panel-picker");
  assert(row != null, "no row over two panels");
  eq(chip("t-1").textContent, "M42 13", "the first M42 chip (label + count):");
  eq(chip("t-2").textContent, "M42 25", "the second M42 chip (label + count):");
});

await act(async () => { root.unmount(); });

console.log(`w3_3PanelPickerSurfaces: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
