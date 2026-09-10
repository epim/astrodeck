// inspectDom.test.tsx - the INSPECT sheet, MOUNTED (plan G.4, T-CAP-2 row).
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/inspectDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT. Inspect is almost entirely other people's components, so
// the things worth a test are the four decisions this sheet actually makes:
//
//   1. It mounts the real instruments. The precondition assertion is not
//      "something rendered" but "the histogram, the stats and the filmstrip are
//      on screen" - a sheet that mounted an empty shell would satisfy every
//      later assertion vacuously (astrodeck-ui-probe-traps: a broken probe
//      reads as a passing app).
//   2. The FITS row goes to /api/preview/{id}/fits when the frame was saved on
//      the rig, and an operator - who cannot hold `view.media` - is TOLD so
//      before pressing it, rather than being handed a 403. The sentence is
//      composed from the role table (`accessPhrase`), so it cannot drift from
//      what the server enforces.
//   3. A NINA/pre-stretched frame's black/mid/white are TRULY disabled, with
//      `NINA_LOCK_REASON` in the accessible name. This is the one place in the
//      whole next UI where a real disabled state is right: the value is fixed
//      at the source, not withheld by permission.
//   4. `src=stack` shows the composite and says out loud that it has no
//      per-frame statistics, instead of drawing a histogram of a JPEG.
//
// WHAT THIS CANNOT DO: jsdom lays nothing out and decodes no images, so the
// stage measures 0x0 and no <img> ever loads. What is real is which node
// carries which attribute, which URL a link points at, and which store action a
// press fires.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/capture/inspect", pretendToBeVisual: true },
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
// The linear path draws through a 2D context jsdom does not implement. The
// component already bails on a null ctx; returning null keeps jsdom from
// printing a "not implemented" wall over the tally.
win.HTMLCanvasElement.prototype.getContext = () => null;

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "Image", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
  // NOT `performance`: this jsdom's delegates to the global one, so copying it
  // makes performance.now() recurse. Node's own is what React's scheduler and
  // the filmstrip's touch grace window read.
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
// Every request the mounted tree makes, in order. Two tests read it: the stack
// variant asserts the status route it depends on WAS asked for, and the viewer
// asserts that a read-only render asks for NOTHING.
const asked: string[] = [];
let stackBody: unknown = null;
g.fetch = async (url: string, init?: { method?: string }) => {
  asked.push(`${init?.method ?? "GET"} ${String(url)}`);
  if (String(url).includes("/api/sequence/stack")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => stackBody };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { InspectSheet, fitsAccessNote } = await import("../inspect");
const { NO_PER_FRAME_LINE } = await import("../inspectStack");
type PreviewInfo = import("../../../../../types").PreviewInfo;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
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
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

// ------------------------------------------------------------------ fixtures
function frame(over: Partial<PreviewInfo> = {}): PreviewInfo {
  return {
    id: 5,
    stats: { min: 120, max: 60000, mean: 900, median: 810, std: 220 },
    histogram: [1, 40, 900, 400, 90, 12, 3, 1],
    histogram_domain: "linear",
    exposure_s: 60,
    gain: 125,
    binning: 1,
    data_width: 6252,
    data_height: 4176,
    display_width: 1400,
    display_height: 935,
    mime: "image/jpeg",
    source: "alpaca",
    is_stretched: false,
    data_is_linear: true,
    has_lossless: true,
    full_well: 51000,
    pixel_scale_arcsec: 1.2,
    auto_levels: { black: 0, mid: 0.4, white: 1 },
    saved_local: true,
    hfr: 3.1,
    stars: 420,
    star_list: [{ x: 400, y: 300, hfr: 3.1 }],
    ts: Math.round(Date.now() / 1000),
    ...over,
  };
}

const OPERATOR = {
  role: "operator",
  email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };
const ADMIN = {
  role: "admin",
  email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather", "control.capture", "control.mount",
    "control.guide", "control.power"],
};

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
  useStore.setState({
    previews: [frame()],
    livePreviewId: 5,
    selectedPreviewId: null,
    viewport: { scale: 1, x: 0, y: 0, fit: true },
    // Advanced open: the B/M/W handles only exist inside the disclosure, and the
    // NINA lock is a property of those handles.
    stretch: { auto: true, black: 0, mid: 0.5, white: 1, brightness: 0, contrast: 0, advancedOpen: true },
    overlays: { stars: true, clip: true, reticle: false, centerMark: true, tilt: false, objects: true, bahtinov: true },
    hfrGood: 3.5,
    hfrWarn: 5,
    night: false,
    wsPhase: "up",
    principal: OPERATOR,
    config: { solve_saved_lights: true },
    sequence: { state: "running", target: "NGC 6946", progress: { frames_done: 12 } },
    captureTarget: "",
    toasts: [],
    ...over,
  } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(params: Record<string, string>): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(InspectSheet as any, { params, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;

// ========================================================== src=preview
seed();
mount({});

test("the sheet mounted, with the real instruments on it", () => {
  // The anti-blank-page guard. Every assertion below is about one control on
  // this sheet, and "no control has the wrong attribute" is trivially true of a
  // sheet that rendered nothing. So name three different components first.
  assert(q('[data-testid="rig-inspect"]') != null, "no sheet marker - the sheet did not render at all");
  assert(q('[role="listbox"][aria-label="Frame history"]') != null,
    "no filmstrip - the sheet mounted a shell, not the preview stack");
  assert(container.querySelectorAll('[role="slider"]').length === 3,
    "the histogram's black/mid/white handles are not on screen - the stretch controls are missing");
  assert(/median/.test(text()) && /6252/.test(text()),
    "no frame statistics and no frame meta line - the stats and PreviewMeta did not mount");
  // GAP-ANALYSIS section 3 asks for the CLIPPED flag by name. It hangs off the
  // white handle, and only when the frame really rails (max >= full_well) - a
  // tag that is always up is not a flag.
  assert(/CLIPPED/.test(text()),
    "a frame whose max reaches full well is not flagged as clipped");
});

test("an operator is told who may take a FITS, before pressing anything", () => {
  // `view.media` is syncer + admin only (lib/caps.ts ROLE_CAPS). The toolbar
  // itself gates FITS on `saved_local` alone, because the server enforces the
  // capability with a 403 and the toolbar has no capability prop - so the sheet
  // says it, once, underneath.
  const note = q('[data-testid="inspect-media-note"]');
  assert(note != null, "an operator sees no note about FITS access - the 403 is the first they hear of it");
  eq((note.textContent || "").trim(), "FITS downloads need syncer or admin access.",
    "the FITS note does not name the roles the server actually accepts");
  eq(fitsAccessNote(), "FITS downloads need syncer or admin access.",
    "the note is hand-written rather than composed from the role table");
});

test("the FITS row points at this frame's own FITS route", () => {
  const dl = [...container.querySelectorAll("button")]
    .find((b: any) => /Download/.test(b.textContent || ""));
  assert(dl != null, "the Download disclosure is locked on a live, saved frame");
  click(dl);
  // BY ITS LABEL, not by role alone: PreviewStage's own root is also a
  // role="group" (the keyboard pan/zoom surface) and it comes first in the
  // document, so a bare [role="group"] query silently reads the picture.
  const menu = q('[role="group"][aria-label^="Download"]');
  assert(menu != null, "the Download disclosure did not open");
  const fits = [...menu.querySelectorAll("a")]
    .find((a: any) => (a.textContent || "").trim() === "FITS");
  assert(fits != null,
    "no live FITS link for a frame with saved_local:true - the raw sub is unreachable from Inspect");
  eq(fits.getAttribute("href"), "/api/preview/5/fits",
    "the FITS link does not point at this frame's FITS route");
  // The other exports the GAP-ANALYSIS asks for, in the same disclosure.
  const hrefs = [...menu.querySelectorAll("a")].map((a: any) => a.getAttribute("href"));
  assert(hrefs.some((h: string) => /\/render\.png/.test(h || "")),
    `no full-res PNG export offered - got ${JSON.stringify(hrefs)}`);
  assert(hrefs.some((h: string) => h === "/api/preview/5/lossless.png"),
    `no lossless PNG export offered - got ${JSON.stringify(hrefs)}`);
  assert(hrefs.some((h: string) => (h || "").startsWith("/api/preview/5/share.jpg")),
    `no share.jpg export offered - got ${JSON.stringify(hrefs)}`);
});

test("the sheet's own thumb-reachable share link carries the run's caption", () => {
  const share = q('[data-testid="inspect-share"]');
  assert(share != null, "no share control at the foot of the sheet");
  eq(share.getAttribute("href"), "/api/preview/5/share.jpg?target=NGC+6946&subs=12",
    "the share link does not carry the target and sub count the caption needs");
});

test("picking a frame off the filmstrip pins it through the store", () => {
  // The strip is the one control here that writes shared state, and it must go
  // through `selectPreview` - a local copy would leave the classic UI, the
  // stage and this sheet each believing a different frame is on screen.
  act(() => { useStore.setState({ previews: [frame({ id: 4 }), frame({ id: 5 })] } as never); });
  eq(useStore.getState().selectedPreviewId, null, "precondition: nothing is pinned yet");
  const tiles = [...container.querySelectorAll('[role="option"]')];
  eq(tiles.length, 2, "the filmstrip is not showing both frames");
  click(tiles[0]);
  eq(useStore.getState().selectedPreviewId, 4,
    "tapping a filmstrip tile did not pin that frame through selectPreview");
  assert(/frame #4/.test(text()), "the sheet header does not say which frame it is now showing");
  act(() => { useStore.setState({ selectedPreviewId: null } as never); });
});

test("the ?id= deep link pins that frame, through the store", () => {
  // The param is an ENTRY POINT, not a second source of truth: it goes in via
  // selectPreview so the filmstrip, the stage and the classic UI all read one
  // answer, and a later "return to live" is not undone on the next render.
  seed({ previews: [frame({ id: 4 }), frame({ id: 5 })] });
  mount({ id: "4" });
  eq(useStore.getState().selectedPreviewId, 4,
    "opening Inspect with ?id=4 did not pin frame 4");
  assert(/frame #4/.test(text()), "the sheet is not showing the frame the deep link named");
});

test("a live-stacked sub names its rejected pixels instead of just counting them", () => {
  // GAP-ANALYSIS section 3's "satellite/aircraft flags". The only place the rig
  // publishes this is livestack.clipped - bright outliers the stacker threw out.
  // The bare number reads as damage; it is the stack working.
  seed({
    previews: [frame({
      id: 5,
      livestack: { frames: 12, integrated_s: 720, rejected: 1, accepted: true, reason: "", clipped: 843 },
    })],
  });
  mount({});
  assert(q('[data-testid="inspect-livestack"]') != null,
    "a sub carrying a live-stack block shows no live-stack readout");
  assert(/843 px clipped/.test(text()), "the rejected-outlier pixel count is not shown");
  assert(/satellite trail, an aircraft or a cosmic ray/.test(text()),
    "the outlier count is shown with no statement of what those pixels are");
});

test("a NINA frame's black/mid/white are truly disabled, and say why", () => {
  // The ONE place a native-ish disabled state is correct: the levels are fixed
  // at the SOURCE, not withheld by permission. The reason rides in the
  // accessible name because `title=` never fires on the tablet at the scope.
  seed({ previews: [frame({ id: 5, is_stretched: true, data_is_linear: false, has_lossless: false })] });
  mount({});
  const handles = [...container.querySelectorAll('[role="slider"]')];
  eq(handles.length, 3, "the three level handles are not on screen, so this proves nothing");
  for (const h of handles) {
    eq(h.getAttribute("aria-disabled"), "true",
      `the ${h.getAttribute("data-handle")} handle is still live on a pre-stretched frame`);
    assert(/fixed at the source/.test(h.getAttribute("aria-label") || ""),
      `the ${h.getAttribute("data-handle")} handle is disabled with no stated reason - ` +
      `got ${JSON.stringify(h.getAttribute("aria-label"))}`);
    // Still focusable: a keyboard user has to be able to land on it and hear why.
    eq(h.getAttribute("tabindex"), "0",
      "a locked handle was taken out of the tab order, which hides the reason from the one user who needs it");
  }
  assert(/NINA pre-stretched this frame/.test(text()),
    "the NINA lock reason is not written anywhere a sighted touch user can read it");
});

// ============================================================= src=stack
await testAsync("the composite renders, and says what it cannot tell you", async () => {
  stackBody = {
    enabled: true, target: "NGC 6946", seq: 7,
    channels: [
      { channel: "L", frames: 9, integrated_s: 5400, rejected: 1 },
      { channel: "R", frames: 4, integrated_s: 2400, rejected: 0 },
    ],
    frames: 13, integrated_s: 7800, rejected: 1, mode: "rgb", downsample: 2,
    has_image: true, render_age_s: 3,
    backfill: { running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0,
      channel: "", error: "", started_ts: null, finished_ts: null, available: 0 },
  };
  seed();
  asked.length = 0;
  mount({ src: "stack" });
  await settle();

  assert(q('[data-testid="rig-inspect"]') != null, "the stack variant did not render the sheet at all");
  assert(asked.some((a) => a === "GET /api/sequence/stack"),
    `the composite never asked the server for its status - got ${JSON.stringify(asked)}`);

  const img = q('[data-testid="inspect-stack-stage"] img');
  assert(img != null, "no composite image on the stack variant");
  eq(img.getAttribute("src"), "/api/sequence/stack/preview.jpg?size=1200&seq=7",
    "the composite is not asking for the run's own stack render at the cache key the server publishes");

  assert(text().includes(NO_PER_FRAME_LINE),
    "the composite does not say it has no per-frame statistics - a user reads the missing histogram as a bug");
  eq(container.querySelectorAll('[role="slider"]').length, 0,
    "a stretch histogram is drawn over the composite JPEG, which has no linear data behind it to stretch");
  assert(!/median/.test(text()),
    "per-frame statistics are shown for a composite that carries none");

  assert(/13 subs/.test(text()) && /2h 10m/.test(text()),
    "the LIVE STACK badge does not carry the sub count and integration time");
  assert(/colour · L·R/.test(text()),
    "the mode badge does not say how the composite was combined");
  assert(q('[data-testid="inspect-stack-channels"]') != null,
    "the per-channel breakdown is missing - which filter contributed what is not recoverable elsewhere");
  const lastSub = q('[data-testid="inspect-last-sub"]');
  assert(lastSub != null,
    "no way back to a single sub, so the line above names a place the user cannot reach");
  click(lastSub);
  assert(/src=preview/.test(String(win.location.hash)),
    `LAST SUB did not switch the sheet's source - hash is ${win.location.hash}`);
  assert(/\/inspect/.test(String(win.location.hash)) && !/inspect\/inspect/.test(String(win.location.hash)),
    `LAST SUB opened a second sheet instead of changing this one - hash is ${win.location.hash}`);
});

// ================================================================== viewer
await testAsync("a viewer sees the whole sheet, read-only, and asks for nothing", async () => {
  seed({ principal: VIEWER });
  asked.length = 0;
  mount({});
  await settle();

  assert(q('[data-testid="rig-inspect"]') != null, "the viewer got no sheet at all - nothing is hidden from a viewer");
  assert(q('[role="listbox"][aria-label="Frame history"]') != null,
    "the viewer's filmstrip is missing - a viewer sees the same screen an operator does");
  const note = q('[data-testid="inspect-media-note"]');
  assert(note != null, "a viewer is not told why the FITS row will refuse them");
  assert(/syncer or admin access/.test(note.textContent || ""),
    `the lock reason does not name the roles - got ${JSON.stringify(note.textContent)}`);
  eq(asked.length, 0,
    `a read-only render fired ${asked.length} request(s): ${JSON.stringify(asked)}`);
});

test("an admin, who does hold view.media, is not nagged about it", () => {
  // The other half of the gate: a note that is always on screen is chrome, and
  // chrome is what teaches people to stop reading the notes.
  seed({ principal: ADMIN });
  mount({});
  assert(q('[data-testid="inspect-media-note"]') == null,
    "the FITS access note is shown to a role that holds view.media");
});

test("the plate-solve toggle is read-only here and points at its one home", () => {
  seed();
  win.location.hash = "#/rig/capture/inspect";
  mount({});
  const row = q('[data-testid="inspect-solve-row"]');
  assert(row != null, "the plate-solve-into-the-file setting is not surfaced on Inspect at all");
  assert(/ON/.test(row.textContent || ""), "the row does not report the value the rig actually holds");
  click(row);
  assert(/\/optics/.test(String(win.location.hash)),
    `the row does not lead to Settings > Optics, where the toggle lives - hash is ${win.location.hash}`);
});

act(() => { if (rootRef) rootRef.unmount(); });

const total = passed + failed;
console.log(`inspectDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
