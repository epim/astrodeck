// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w4_3AssembledPreview.test.tsx - the Gallery's "assembled" choice (#1113 part b,
// #779): the whole mosaic placed by WCS on one picture, beside the panel picker.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/components/__tests__/w4_3AssembledPreview.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS GRADED
//
//   1. THE CHOICE. Over two or more stacked panels the Gallery's picker offers
//      one more chip, ASSEMBLED. Pressing it swaps the panel's picture for
//      `/api/sequence/stack/mosaic.jpg`, lights it and unlights every panel
//      chip; pressing a panel chip leaves it; pressing it again lets go.
//   2. THE CACHE KEY. The assembled picture changes when ANY panel's frame
//      lands, so its URL carries the LATEST panel's seq, and the status poll
//      under it asks for the latest panel, never the pinned one, even when a
//      panel is pinned. The pin itself survives the visit.
//   3. NO ONE PANEL'S NUMBERS BESIDE THE WHOLE. The per-panel readouts are not
//      drawn under the assembled picture.
//   4. ONE PANEL has no row and so no choice, and asks for no mosaic. A mosaic
//      that falls back to one panel while the choice is on shows that panel.
//   5. ONLY THE GALLERY OFFERS IT. The other three stack surfaces share the
//      picker and must not grow the chip.
//
// Mutants (a byte backup in the scratch directory, restored byte-identically):
// see the WP 4.3 report.

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
const TWO: FakePanel[] = [
  { key: "t-a", target: "Alpha", frames: 4, integrated_s: 240, seq: 12 },
  { key: "t-b", target: "Beta", frames: 2, integrated_s: 120, seq: 15 },
];
const LEGACY_SRC = "/api/sequence/stack/preview.jpg?size=1200&seq=15";
const ALPHA_SRC = "/api/sequence/stack/preview.jpg?size=1200&seq=12&panel=t-a";
const MOSAIC_SRC = "/api/sequence/stack/mosaic.jpg?size=1200&seq=15";

const img = () => byId("gallery-stack-img");
const assembledImg = () => byId("gallery-stack-assembled-img");
const chip = (key: string) => byId(`panel-chip-${key}`);
const lit = (el: any) => el?.getAttribute("aria-pressed");
const attr = (el: any) => el?.getAttribute("src") ?? "";

// ------------------------------------------------------------- the choice
PANELS = TWO.map((p) => ({ ...p }));
await mount(() => createElement(SessionStackPanel));

await testAsync("two panels offer an assembled chip, and following the latest is unchanged", () => {
  assert(byId("panel-chip-assembled") != null, "no assembled chip over two panels");
  eq(lit(byId("panel-chip-assembled")), "false", "the assembled chip is lit before it was pressed:");
  eq(attr(img()), LEGACY_SRC, "the per-panel picture changed for a viewer who never pressed it:");
  assert(assembledImg() == null, "the assembled picture is drawn before it was asked for");
});

await testAsync("pressing assembled draws the mosaic at the latest seq and unlights the panels", async () => {
  await click("panel-chip-assembled");
  eq(attr(assembledImg()), MOSAIC_SRC, "the assembled picture URL (the latest panel's seq):");
  assert(img() == null, "the one-panel picture is still drawn beside the assembled one");
  eq(lit(byId("panel-chip-assembled")), "true", "the assembled chip is not lit:");
  eq(lit(chip("t-a")), "false", "a panel chip is lit under the assembled view (a):");
  eq(lit(chip("t-b")), "false", "a panel chip is lit under the assembled view (b):");
});

await testAsync("the assembled view carries no one panel's readouts", () => {
  const text = container.textContent as string;
  assert(!text.includes("INTEGRATED"), "a panel's integration time is drawn beside the whole mosaic");
  assert(!text.includes("BINNED"), "a panel's binning is drawn beside the whole mosaic");
});

await testAsync("pressing a panel chip leaves the assembled view for that panel", async () => {
  await click("panel-chip-t-a");
  assert(assembledImg() == null, "the assembled picture stayed after a panel was chosen");
  eq(attr(img()), ALPHA_SRC, "the chosen panel's own picture:");
  eq(lit(byId("panel-chip-assembled")), "false", "the assembled chip is still lit:");
  eq(lit(chip("t-a")), "true", "the chosen panel's chip is not lit:");
});

await testAsync("pressing assembled again lets go, back to the pinned panel", async () => {
  await click("panel-chip-assembled");
  eq(attr(assembledImg()), MOSAIC_SRC, "the assembled picture under a pin:");
  eq(view.panel, "t-a", "the pin was dropped by the visit to the assembled view:");
  await click("panel-chip-assembled");
  assert(assembledImg() == null, "letting go left the assembled picture");
  eq(attr(img()), ALPHA_SRC, "the pin did not survive the visit to the assembled view:");
});

// ---------------------------------------------------- the cache key and poll
await testAsync("under a pin the assembled view asks the latest panel's status, and so its seq", async () => {
  PANELS = TWO.map((p) => ({ ...p }));
  await mount(() => createElement(SessionStackPanel));
  await click("panel-chip-t-a");
  asks.length = 0;
  await click("panel-chip-assembled");
  assert(asks.some((a) => a === "GET /api/sequence/stack"),
    `the status was not asked for the latest panel: ${asks.join(" | ")}`);
  eq(attr(assembledImg()), MOSAIC_SRC,
    "the assembled picture is keyed on the pinned panel's seq, so Beta's frames would not refetch it:");
  asks.length = 0;
  assert(ticks.length > 0, "the surface set no poll timer: the test cannot reach its refresh");
  await act(async () => { ticks[ticks.length - 1](); });
  await settle();
  assert(asks.length > 0 && asks.every((a) => !a.includes("panel=")),
    `the poll under the assembled view asked for a panel: ${asks.join(" | ")}`);
});

await testAsync("a frame landing on any panel moves the assembled picture", async () => {
  PANELS = TWO.map((p) => ({ ...p }));
  await mount(() => createElement(SessionStackPanel));
  await click("panel-chip-assembled");
  PANELS = [TWO[0], { ...TWO[1], frames: 3, seq: 21 }];
  await act(async () => { ticks[ticks.length - 1](); });
  await settle();
  eq(attr(assembledImg()), "/api/sequence/stack/mosaic.jpg?size=1200&seq=21",
    "the assembled picture did not move when a panel's frame landed:");
});

// ------------------------------------------------------------ one panel
await testAsync("one panel draws no row and no assembled chip, and asks for no mosaic", async () => {
  PANELS = [TWO[1]];
  await mount(() => createElement(SessionStackPanel));
  eq(byId("gallery-stack-panels"), null, "a chip row over ONE panel:");
  eq(byId("panel-chip-assembled"), null, "an assembled chip over one panel:");
  eq(attr(img()), LEGACY_SRC, "the one-panel picture is not the legacy URL:");
  assert(asks.every((a) => !a.includes("mosaic")), `a mosaic was asked for: ${asks.join(" | ")}`);
});

await testAsync("a mosaic that falls back to one panel while assembled shows that panel", async () => {
  PANELS = TWO.map((p) => ({ ...p }));
  await mount(() => createElement(SessionStackPanel));
  await click("panel-chip-assembled");
  assert(assembledImg() != null, "the assembled view did not open");
  PANELS = [TWO[1]];
  await act(async () => { ticks[ticks.length - 1](); });
  await settle();
  assert(assembledImg() == null, "an assembled picture of one panel was kept");
  eq(attr(img()), LEGACY_SRC, "the one panel's own picture is not shown:");
});

// ------------------------------------------------- only the gallery offers it
await testAsync("the other stack surfaces do not offer the assembled chip", async () => {
  PANELS = TWO.map((p) => ({ ...p }));
  const others: Array<[string, () => any]> = [
    ["classic SessionStack", () => createElement(SessionStack)],
    ["Rig inspect StackInspect", () => createElement(StackInspect, { onLastSub: () => {} })],
    ["Now LiveStack", () => createElement(LiveStack)],
  ];
  for (const [name, make] of others) {
    await mount(make);
    assert(chip("t-a") != null, `${name}: the picker is not there, so the check proves nothing`);
    eq(byId("panel-chip-assembled"), null, `${name} grew an assembled chip:`);
  }
});

// ------------------------------------------------------ the picker on its own
await testAsync("the picker draws the chip only when a surface gives it a handler, and lights it from its prop", async () => {
  const panels = [
    { key: "t-1", target: "M42", frames: 3, integrated_s: 1, seq: 1 },
    { key: "t-2", target: "M43", frames: 5, integrated_s: 1, seq: 2 },
  ];
  await act(async () => { root.render(null); });
  await act(async () => {
    root.render(createElement(PanelPicker, { panels, panel: null, onPick: () => {} }));
  });
  eq(byId("panel-chip-assembled"), null, "a surface with no handler got the chip:");
  let pressed = 0;
  await act(async () => {
    root.render(createElement(PanelPicker, {
      panels, panel: null, onPick: () => {}, assembled: true, onAssemble: () => { pressed++; },
    }));
  });
  const chipEl = byId("panel-chip-assembled");
  assert(chipEl != null, "no chip for a surface that gave a handler");
  eq(chipEl.textContent, "ASSEMBLED", "the chip's words:");
  eq(lit(chipEl), "true", "the chip is not lit from its prop:");
  eq(lit(chip("t-2")), "false", "the latest panel is lit while the assembled view is on:");
  await click("panel-chip-assembled");
  eq(pressed, 1, "pressing the chip did not call the handler:");
});

await act(async () => { root.unmount(); });

console.log(`w4_3AssembledPreview: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
