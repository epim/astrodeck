// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16LiveStackPanels.test.tsx - one live stack per mosaic panel, and the chip
// row that chooses between them (#172 part A, WP-121).
//
//   Run directly:  npx tsx src/next/hubs/session/now/__tests__/w16LiveStackPanels.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THE FIXTURE IS. A fake server that answers `GET /api/sequence/stack` the
// way the real one does: with no `?panel=` it describes the LATEST panel (the one
// with the highest `seq`), with `?panel=<key>` it describes that panel, and
// `panels` is the whole list either way. So an assertion about what the screen
// says under a pinned panel is an assertion about the status the screen ASKED
// for, which is the part a regression would break.
//
//   1. The vacuity guard: the chip row mounted, with the seeded panels.
//   2. FOLLOWING THE LATEST asks for exactly the URLs this app has always asked
//      for, and lights the chip of the panel the latest frame fed.
//   3. Pinning a panel changes the picture's URL AND the status the badge reads.
//   4. A panel that is no longer stacked drops its own pin.
//   5. The reply to a start/stop/reset press does not put the latest panel's
//      counts under a pinned panel's picture.
//   6. One panel draws no row at all. Same-named panels are told apart.
//   7. A released panel is said, once.
//
// Mutants. Each is a one-line change run from a byte backup inside the
// worktree, restored byte-identically (sha256 compared) and grepped gone. The
// failures, verbatim:
//   M21 LiveStack draws the row for `panels.length > 0` (10/11 pass):
//       "one panel draws no chip row, and the URL is the legacy one: a chip row
//        over ONE panel only repeats what the badge says: expected null, got
//        [object HTMLDivElement]"
//   M22 LiveStack leaves the pin off the image URL (9/11 pass):
//       "pinning Alpha changes the picture's URL and the status the badge
//        reads: the image URL does not ask the server for Alpha's stack:
//        /api/sequence/stack/preview.jpg?size=1200&seq=12"
//       and test 5 "the pin was lost across the press"
//   M23 refresh() keeps a pin whose panel is gone (10/11 pass):
//       "a pin on a panel that is no longer stacked is dropped, not left blank:
//        the pin on a panel the server does not have was kept: expected null,
//        got t-gone"
//   M24 act() publishes the POST reply under a pin (10/11 pass):
//       "a start/stop/reset reply does not put the latest panel's counts under
//        a pinned picture: the pinned panel was not asked for again after the
//        reply: POST /api/sequence/stack/start"
//   M25 the status poll ignores the pin (8/11 pass):
//       "pinning Alpha changes the picture's URL and the status the badge
//        reads: the cache key is not Alpha's own seq, so Beta's frames would
//        refetch it: /api/sequence/stack/preview.jpg?size=1200&seq=15&panel=t-a"

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
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------- the fake stack server
const asks: string[] = [];

interface FakePanel { key: string; target: string; frames: number; integrated_s: number; seq: number }
let PANELS: FakePanel[] = [];
let EVICTED: string[] = [];

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
      render_age_s: null, backfill: BACKFILL, panels, evicted: EVICTED,
    };
  }
  return {
    enabled: true, target: hit.target, seq: hit.seq,
    channels: [{ channel: "Ha", frames: hit.frames, integrated_s: hit.integrated_s, rejected: 0 }],
    frames: hit.frames, integrated_s: hit.integrated_s, rejected: 0,
    mode: "narrowband", downsample: 2, has_image: true, render_age_s: 3,
    backfill: BACKFILL, panels, evicted: EVICTED,
  };
}

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push(`${method} ${u}`);
  let body: any = { ok: true };
  if (u.includes("/api/sequence/stack")) {
    // Every route answers with the LATEST panel unless a panel is asked for,
    // POSTs included: that is the behaviour the pin has to survive.
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
const { useStore } = await import("../../../../../store");
const { LiveStack, evictedNote } = await import("../LiveStack");
const {
  resetStackViewForTests, resetSessionStackStateForTests, useStackView,
  useSessionStackStatus, panelLabels, latestPanel,
} = await import("../stackView");

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
const imgSrc = (): string => byId("live-stack-image")?.getAttribute("src") ?? "";
const badge = (): string => (byId("live-stack-badge")?.textContent as string) ?? "";
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
  principal: OPERATOR,
  equipConnected: true,
  wsPhase: "up",
  wsLastEvent: Date.now(),
  status: { connected: { camera: true }, looping: false },
  sequence: {
    state: "running", target: "Beta", plan_name: "Mosaic", target_index: 0,
    progress: { frames_done: 6, frames_total: 40, percent: 15, elapsed_s: 600, rejected: 0 },
  },
} as never);

// A probe that hands the test the same hooks the screen uses, so the test can
// press what a chip press would press without going through the DOM for it.
let view: any = null;
let read: any = null;
function Probe(): null {
  view = useStackView();
  read = useSessionStackStatus();
  return null;
}

const root = createRoot(container);
async function mount(): Promise<void> {
  // UNMOUNT FIRST, THEN RESET, THEN MOUNT. Re-rendering the same tree
  // reconciles instead of mounting, so the effects that subscribe and fetch
  // would not run again; and resetting before the old tree's cleanup has run
  // leaves the poller's mount counter at -1, so the new tree never sees
  // `mounted === 1` and never fetches. Either way the test would read a stale
  // or empty screen and fail for a reason that is not the product's.
  await act(async () => { root.render(null); });
  resetStackViewForTests();
  resetSessionStackStateForTests();
  asks.length = 0;
  await act(async () => {
    root.render(createElement(Fragment, null,
      createElement(Probe), createElement(LiveStack)));
  });
  await settle();
}

const TWO: FakePanel[] = [
  { key: "t-a", target: "Alpha", frames: 4, integrated_s: 240, seq: 12 },
  { key: "t-b", target: "Beta", frames: 2, integrated_s: 120, seq: 15 },
];

// --------------------------------------------------- 1. the vacuity guard first
PANELS = TWO.map((p) => ({ ...p }));
await mount();
await testAsync("the picture and the chip row mounted at all", () => {
  assert(byId("now-live-stack") != null, "no live stack: the fetch or the store seed is broken");
  assert(byId("live-stack-image") != null,
    "no <img>: has_image/frames in the fixture no longer render a picture");
  assert(byId("live-stack-panels") != null,
    "no chip row over two panels: the status `panels` never reached the screen");
  assert(byId("panel-chip-t-a") != null && byId("panel-chip-t-b") != null,
    "a panel has no chip");
});

// ------------------------------------------- 2. following the latest is today's
const LEGACY_SRC = "/api/sequence/stack/preview.jpg?size=1200&seq=15";
await testAsync("following the latest asks for exactly the URLs this app has always asked for", () => {
  eq(imgSrc(), LEGACY_SRC, "the picture URL changed for a screen that follows the latest panel:");
  assert(asks.every((a) => !a.includes("panel=")),
    `a panel parameter was sent while following: ${asks.join(" | ")}`);
  assert(asks.some((a) => a === "GET /api/sequence/stack"),
    "the plain status request was not made");
});

await testAsync("each chip carries its panel's name and sub count; the latest is lit", () => {
  const part = (id: string, cls: string): string =>
    (byId(id).querySelector(`.${cls}`)?.textContent as string) ?? "";
  eq(part("panel-chip-t-a", "nx-chip-text"), "Alpha", "the Alpha chip's name:");
  eq(part("panel-chip-t-a", "nx-chip-count"), "4", "the Alpha chip's sub count:");
  eq(part("panel-chip-t-b", "nx-chip-text"), "Beta", "the Beta chip's name:");
  eq(part("panel-chip-t-b", "nx-chip-count"), "2", "the Beta chip's sub count:");
  eq(byId("panel-chip-t-b").getAttribute("data-active"), "true",
    "the chip of the panel the latest frame fed is not the lit one:");
  eq(byId("panel-chip-t-a").getAttribute("data-active"), "false",
    "two chips are lit at once:");
  assert(/2 subs/.test(badge()), `the badge does not count the panel on screen: "${badge()}"`);
});

// ------------------------------------------------- 3. pinning changes the picture
await testAsync("pinning Alpha changes the picture's URL and the status the badge reads", async () => {
  asks.length = 0;
  await click("panel-chip-t-a");
  const src = imgSrc();
  assert(src.includes("panel=t-a"),
    `the image URL does not ask the server for Alpha's stack: "${src}"`);
  assert(src.includes("seq=12"),
    `the cache key is not Alpha's own seq, so Beta's frames would refetch it: "${src}"`);
  assert(asks.some((a) => a === "GET /api/sequence/stack?panel=t-a"),
    `the status was not asked for Alpha: ${asks.join(" | ")}`);
  assert(/4 subs/.test(badge()),
    `the badge still counts the other panel: "${badge()}"`);
  eq(byId("panel-chip-t-a").getAttribute("data-active"), "true", "the pinned chip is not lit:");
  eq(byId("panel-chip-t-b").getAttribute("data-active"), "false", "the latest chip stayed lit under a pin:");
});

await testAsync("pressing the pinned chip again follows the latest once more", async () => {
  await click("panel-chip-t-a");
  eq(imgSrc(), LEGACY_SRC, "releasing the pin did not restore the plain URL:");
  assert(/2 subs/.test(badge()), `the badge did not follow back: "${badge()}"`);
  eq(view.panel, null, "the pin was not released:");
});

// ----------------------------------------- 4. a vanished panel drops its own pin
await testAsync("a pin on a panel that is no longer stacked is dropped, not left blank", async () => {
  asks.length = 0;
  await act(async () => { view.setPanel("t-gone"); });
  await settle();
  eq(view.panel, null, "the pin on a panel the server does not have was kept:");
  assert(asks.some((a) => a === "GET /api/sequence/stack?panel=t-gone"),
    `the pinned panel was never asked about: ${asks.join(" | ")}`);
  eq(asks[asks.length - 1], "GET /api/sequence/stack",
    "after dropping the pin the latest was not asked for:");
  eq(imgSrc(), LEGACY_SRC, "the picture is not the latest after the pin was dropped:");
  assert(byId("live-stack-image") != null, "the picture went blank under a dead pin");
});

// ----------------------------- 5. a POST reply does not undo a pin's status
await testAsync("a start/stop/reset reply does not put the latest panel's counts under a pinned picture", async () => {
  await click("panel-chip-t-a");
  assert(/4 subs/.test(badge()), "setup: Alpha is not pinned");
  asks.length = 0;
  await act(async () => { read.start(false); });
  await settle();
  assert(asks.some((a) => a.startsWith("POST /api/sequence/stack/start")),
    `the press never reached the server: ${asks.join(" | ")}`);
  assert(asks.some((a) => a === "GET /api/sequence/stack?panel=t-a"),
    `the pinned panel was not asked for again after the reply: ${asks.join(" | ")}`);
  assert(/4 subs/.test(badge()),
    `the POST's latest-panel reply replaced the pinned panel's status: "${badge()}"`);
  assert(imgSrc().includes("panel=t-a"), "the pin was lost across the press");
  await click("panel-chip-t-a");
});

// ------------------------------------------- 6. one panel is a screen with no row
PANELS = [{ key: "t-solo", target: "NGC 7000", frames: 9, integrated_s: 540, seq: 9 }];
await mount();
await testAsync("one panel draws no chip row, and the URL is the legacy one", () => {
  assert(byId("live-stack-image") != null, "setup: no picture for the one panel");
  eq(byId("live-stack-panels"), null,
    "a chip row over ONE panel only repeats what the badge says:");
  eq(imgSrc(), "/api/sequence/stack/preview.jpg?size=1200&seq=9",
    "a one-target night's picture URL changed:");
  assert(/9 subs/.test(badge()), `the badge changed for a one-target night: "${badge()}"`);
});

await testAsync("a stack with no panels list at all (an older server) draws no row", async () => {
  const real = g.fetch;
  g.fetch = async (url: any, init: any) => {
    const r = await real(url, init);
    const j = await r.json();
    delete j.panels;
    delete j.evicted;
    return { ...r, json: async () => j };
  };
  await mount();
  g.fetch = real;
  eq(byId("live-stack-panels"), null, "a row was drawn with no panels list:");
  assert(byId("live-stack-image") != null, "the picture needs no panels list");
});

await testAsync("panels that share a name are told apart; a lone name is left alone", () => {
  const p = (key: string, target: string, seq: number) =>
    ({ key, target, frames: 1, integrated_s: 60, seq });
  eq(panelLabels([p("a", "M42", 1), p("b", "M42", 2), p("c", "M43", 3)]).join("|"),
    "M42 1|M42 2|M43", "two chips with one label cannot be told apart:");
  eq(panelLabels([p("a", "", 1)]).join("|"), "panel", "an unnamed panel has no label:");
  eq(latestPanel([p("a", "A", 3), p("b", "B", 9), p("c", "C", 5)])?.key, "b",
    "the latest is the highest seq, not the last in the list:");
  eq(latestPanel([]), null, "an empty list has no latest:");
});

// ----------------------------------------------------- 7. a released panel is said
await testAsync("a released panel is said once under the picture, and silence is silence", async () => {
  PANELS = TWO.map((p) => ({ ...p }));
  EVICTED = [];
  await mount();
  eq(byId("live-stack-evicted"), null, "a note was printed with nothing released:");
  EVICTED = ["Gamma"];
  await mount();
  const note = byId("live-stack-evicted");
  assert(note != null, "a panel was released and nothing said so");
  assert(/Gamma was released to stay under the memory budget/.test(note.textContent as string),
    `the note does not say what happened: "${note.textContent}"`);
  eq(evictedNote(undefined), null, "no list means no note:");
  eq(evictedNote(["A", "B"])?.startsWith("A, B were released"), true, "plural wording:");
  EVICTED = [];
});

await act(async () => { root.unmount(); });

console.log(`w16LiveStackPanels: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
