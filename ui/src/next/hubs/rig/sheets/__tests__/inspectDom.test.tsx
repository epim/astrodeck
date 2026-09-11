// inspectDom.test.tsx - the INSPECT sheet, MOUNTED, with the rebuilt toolbar,
// histogram and readouts (wave R7, T-R7-19; plan G.4, T-CAP-2 row).
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/inspectDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT THIS IS ABOUT. Inspect is a picture, a plot and a lot of judgements about
// what the rig can and cannot be asked for. The judgements are the part worth a
// test:
//
//   1. The instruments are on screen. The precondition is not "something
//      rendered" but "the toolbar, the histogram, the statistics and the
//      filmstrip are all here" - every later assertion is trivially true of a
//      sheet that rendered nothing (astrodeck-ui-probe-traps: a broken probe
//      reads as a passing app).
//   2. FIT and 100% move the STAGE, and ask the rig for nothing. A toolbar whose
//      percentage steps while the picture does not is the toolbar lying.
//   3. A toggle the frame cannot support is honest-disabled WITH ITS REASON and
//      fires nothing - not hidden, and not a native `disabled`, which strips the
//      element and the reason out of the accessibility tree together.
//   4. Advanced reveals black / mid / white; it is not a permanent three-handle
//      rail that most users never need.
//   5. A pre-stretched frame LOCKS those three with the one sentence that says
//      why, and Auto and Brightness keep working - they are a display-domain
//      nudge that costs nothing on that path.
//   6. A statistic the rig did not send prints `--`. `Number(null)` is 0, which
//      is finite, plausible and wrong.
//   7. NO native `disabled` attribute anywhere in the document.
//   8. The FITS row goes to /api/preview/{id}/fits when the frame was saved on
//      the rig, and an operator - who cannot hold `view.media` - is TOLD so
//      before pressing it, rather than being handed a 403.
//   9. `src=stack` shows the composite and says out loud that it has no
//      per-frame statistics, instead of drawing a histogram of a JPEG.
//
// WHAT THIS CANNOT DO: jsdom lays nothing out and decodes no images, so the
// stage measures 0x0 and no <img> ever loads. What is real is which node carries
// which attribute, which URL a link points at, and which store action a press
// fires.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `hubs/rig/inspect/index.ts` is the area root and imports `inspect.css` - one
// import site, by contract, so the cascade order cannot depend on module
// resolution order. Node has no idea what a `.css` file is, so a synchronous
// load hook answers with an empty module. This is the ONLY way to keep both
// facts true at once: the area has one style entry point, and that entry point
// is still mountable in a test.
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
// jsdom lays nothing out, so every element measures 0x0 - and a 0x0 stage makes
// the fit scale exactly 1, which is also what 100% produces. The two zoom
// primaries would then be indistinguishable and the test would pass while one of
// them did nothing. Give the stage a real box: 700x400 against a 1400x935 frame
// puts fit at about 0.43 and 100% at 1, which is the difference the test is for.
for (const [prop, value] of [["clientWidth", 700], ["clientHeight", 400]] as const) {
  Object.defineProperty(win.HTMLElement.prototype, prop, {
    get() { return value; }, configurable: true,
  });
}
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
// Every request the mounted tree makes, in order. Three tests read it: the zoom
// primaries assert they moved the stage and asked the rig for NOTHING, the stack
// variant asserts the status route it depends on WAS asked for, and the viewer
// asserts that a read-only render asks for nothing at all.
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
const { NINA_LOCK_REASON, statText, toggleRows } = await import("../../inspect");
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

/** Advanced CLOSED by default, because that is the shipped default and the
 *  disclosure test has to be able to open it. */
const STRETCH = {
  auto: true, black: 0, mid: 0.5, white: 1, brightness: 0, contrast: 0, advancedOpen: false,
};

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      previews: [frame()],
      livePreviewId: 5,
      selectedPreviewId: null,
      viewport: { scale: 1, x: 0, y: 0, fit: true },
      stretch: { ...STRETCH },
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
/** Drag a range input: React installs its own value tracker, so assigning
 *  `el.value` alone leaves it thinking nothing changed. */
function slide(node: any, value: string): void {
  act(() => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(node, value);
    node.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}
const q = (sel: string) => container.querySelector(sel) as any;
/** The download panel is portalled out of the sheet, so it is found on the
 *  DOCUMENT, not inside the container. */
const qd = (sel: string) => win.document.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;
/** The value a labelled readout tile is showing. */
function tileValue(gridTestid: string, label: string): string | null {
  const grid = q(`[data-testid="${gridTestid}"]`);
  if (!grid) return null;
  for (const tile of [...grid.querySelectorAll(".nx-readout")]) {
    const l = (tile as any).querySelector(".nx-readout-label");
    if ((l?.textContent || "").trim() === label) {
      return ((tile as any).querySelector(".nx-readout-value")?.textContent || "").trim();
    }
  }
  return null;
}
const vp = () => (useStore.getState() as any).viewport;
/** Put the stage somewhere the zoom primaries can visibly move it away from.
 *  The stage snaps to Fit on mount, so both primaries would be no-ops from
 *  there and the test would pass while doing nothing. */
function zoomedIn(): void {
  act(() => { useStore.setState({ viewport: { scale: 2, x: -40, y: -20, fit: false } } as never); });
}

// ========================================================== src=preview
seed();
mount({});

test("the sheet mounted, with the rebuilt instruments on it", () => {
  // The anti-blank-page guard. Every assertion below is about one control on
  // this sheet, and "no control has the wrong attribute" is trivially true of a
  // sheet that rendered nothing. So name five different components first.
  assert(q('[data-testid="rig-inspect"]') != null, "no sheet marker - the sheet did not render at all");
  assert(q('[data-testid="preview-toolbar"]') != null, "no toolbar - the sheet mounted a shell");
  assert(q('[data-testid="preview-histogram"]') != null, "no histogram panel");
  assert(q('[data-testid="frame-stats"]') != null, "no frame statistics");
  assert(q('[data-testid="frame-filmstrip"]') != null, "no filmstrip");
  assert(q('[data-testid="preview-meta"]') != null, "no frame meta readout");
  assert(q('[data-testid="focus-verdict"]') != null, "no focus verdict");
  // The five zoom primaries are always visible, never folded into an overflow.
  for (const id of ["preview-zoom-out", "preview-zoom", "preview-zoom-in", "preview-zoom-fit", "preview-zoom-hundred"]) {
    assert(q(`[data-testid="${id}"]`) != null, `the zoom cluster is missing ${id}`);
  }
  // A percentage, not a dash: the readout is the one control in the cluster that
  // ASSERTS something, and it reads "-" only while the stage has nothing to move.
  assert(/^\d+%$/.test(q('[data-testid="preview-zoom"]').textContent || ""),
    `the zoom readout is not reporting the stage's scale - got `
    + JSON.stringify(q('[data-testid="preview-zoom"]').textContent));
  // Every overlay row carries its WORD, not a colour alone.
  assert(/STARS/.test(text()) && /RETICLE/.test(text()) && /CENTER/.test(text()),
    "the overlay toggles are not labelled with their words");
});

test("the frame meta and statistics read from this frame, not from a template", () => {
  eq(tileValue("preview-meta", "SIZE"), "6252x4176", "the size tile is not this frame's size");
  eq(tileValue("preview-meta", "GAIN"), "125", "the gain tile is not this frame's gain");
  eq(tileValue("frame-stats", "MEDIAN"), "810", "the median tile is not this frame's median");
  eq(tileValue("frame-stats", "STARS"), "420", "the stars tile is not this frame's star count");
  // The clip verdict is `full_well`-derived: 60000 >= 51000 on a linear frame.
  assert(/full well 51000 ADU/.test(text()),
    "a frame whose max reaches full well does not name the well depth it reached");
});

let fitScale = 0;

test("FIT moves the stage and asks the rig for nothing", () => {
  zoomedIn();
  asked.length = 0;
  click(q('[data-testid="preview-zoom-fit"]'));
  fitScale = vp().scale;
  assert(fitScale > 0 && fitScale < 1,
    `FIT did not scale the 1400x935 frame down into the 700x400 stage - got ${fitScale}`);
  eq(vp().x, 0, "FIT did not re-centre the stage");
  eq(vp().fit, true, "FIT did not put the stage back into fit mode");
  eq(asked.length, 0, `FIT fired ${asked.length} request(s): ${JSON.stringify(asked)}`);
  assert(/%$/.test(q('[data-testid="preview-zoom"]').textContent || ""),
    "the zoom readout did not follow the stage");
});

test("100% moves the stage to a DIFFERENT transform, and asks the rig for nothing", () => {
  // Straight from the fit the test above just established, so the two are
  // compared rather than each being asserted against a constant. If 100% were
  // wired to the same handler as FIT this test is the one that says so.
  asked.length = 0;
  click(q('[data-testid="preview-zoom-hundred"]'));
  eq(vp().scale, 1, "100% did not set the stage to one display pixel per CSS pixel");
  assert(vp().scale !== fitScale, "100% produced the same transform FIT did");
  eq(vp().fit, false, "100% left the stage claiming it is still fitted");
  assert(vp().x < 0, "100% did not keep the centred point under the stage centre");
  eq(asked.length, 0, `100% fired ${asked.length} request(s): ${JSON.stringify(asked)}`);
});

test("a toggle whose data is missing is locked with its reason, and fires nothing", () => {
  // A frame with no star list: the stage draws no rings, so the control must not
  // claim it can. Not hidden (the row exists on every frame) and not a native
  // `disabled` (that takes the reason out of the accessibility tree with it).
  seed({ previews: [frame({ star_list: undefined, stars: undefined })] });
  mount({});
  const stars = q('[data-testid="preview-toggle-stars"]');
  assert(stars != null, "the STARS toggle vanished on a frame with no star list, instead of saying why");
  eq(stars.getAttribute("aria-disabled"), "true", "the STARS toggle is still live on a frame with no star list");
  eq(stars.hasAttribute("disabled"), false, "the STARS toggle uses the native disabled attribute");
  assert(/no per-star measurements/.test(stars.getAttribute("title") || ""),
    `the STARS toggle is locked with no stated reason - got ${JSON.stringify(stars.getAttribute("title"))}`);
  eq(stars.getAttribute("aria-pressed"), "false",
    "a toggle the frame cannot support still reads engaged, over a stage drawing nothing");

  const before = JSON.stringify((useStore.getState() as any).overlays);
  const toastsBefore = (useStore.getState() as any).toasts.length;
  click(stars);
  eq(JSON.stringify((useStore.getState() as any).overlays), before,
    "pressing a locked toggle wrote to the overlay preferences anyway");
  assert((useStore.getState() as any).toasts.length > toastsBefore,
    "pressing a locked toggle was silent - the reason never reached a fingertip");
});

test("every overlay's copy, and every locked row's reason, is reachable by tap", () => {
  // The legacy toolbar put this copy in `title=`, which never fires on a touch
  // screen - so on the tablet at the scope the words STARS and CLIP had no
  // explanation anywhere. ONE control, not one per button: seven more 44 px
  // triggers would be rows of pure help on a bar whose job is the primaries.
  seed({ previews: [frame({ star_list: undefined, stars: undefined })] });
  mount({});
  click(q('[data-testid="preview-overlays-help"]'));
  const help = qd('[data-testid="preview-overlays-help-menu"]');
  assert(help != null, "there is no tap path to what the overlays actually draw");
  const t = help.textContent || "";
  assert(/a ring per detected star/.test(t), "the STARS overlay is a word with no explanation");
  assert(/blown highlights/.test(t), "the CLIP overlay is a word with no explanation");
  assert(/downscaled to 1400 px/.test(t),
    "100% does not say 100% of WHAT, next to a magnifier that means something stricter");
  assert(/no per-star measurements/.test(t),
    "the locked STARS row's reason is not repeated where the copy for it lives");
});

test("a live toggle still writes, so the lock above is a lock and not a dead control", () => {
  seed();
  mount({});
  const reticle = q('[data-testid="preview-toggle-reticle"]');
  eq((useStore.getState() as any).overlays.reticle, false, "precondition: the reticle is off");
  click(reticle);
  eq((useStore.getState() as any).overlays.reticle, true,
    "pressing an unlocked toggle did not write the overlay preference");
});

test("the Advanced disclosure is what reveals black, mid and white", () => {
  seed();
  mount({});
  eq(container.querySelectorAll('[role="slider"]').length, 0,
    "the three level handles are on screen before Advanced was opened");
  const head = q('[data-testid="preview-stretch-advanced"] button[aria-expanded]');
  assert(head != null, "there is no Advanced disclosure at all");
  eq(head.getAttribute("aria-expanded"), "false", "the disclosure starts open");
  click(head);
  eq((useStore.getState() as any).stretch.advancedOpen, true,
    "opening Advanced did not persist through the stretch state, so it forgets on the next frame");
  const handles = [...container.querySelectorAll('[role="slider"]')];
  eq(handles.length, 3, "opening Advanced did not reveal the black, mid and white handles");
  eq(handles.map((h: any) => h.getAttribute("data-handle")).join(","), "black,mid,white",
    "the three revealed handles are not black, mid and white");
});

test("a pre-stretched frame locks the levels with the sentence, and keeps Auto and Brightness", () => {
  // The ONE genuine impossibility on this sheet: there is no linear data behind
  // a frame that arrived already stretched, so black/mid/white have nothing to
  // be re-derived FROM. It is still not a native `disabled`.
  seed({
    previews: [frame({ id: 5, is_stretched: true, data_is_linear: false, has_lossless: false })],
    stretch: { ...STRETCH, advancedOpen: true },
  });
  mount({});
  const handles = [...container.querySelectorAll('[role="slider"]')];
  eq(handles.length, 3, "the three level handles are not on screen, so this proves nothing");
  for (const h of handles) {
    eq(h.getAttribute("aria-disabled"), "true",
      `the ${h.getAttribute("data-handle")} handle is still live on a pre-stretched frame`);
    eq(h.hasAttribute("disabled"), false,
      `the ${h.getAttribute("data-handle")} handle uses the native disabled attribute`);
    assert((h.getAttribute("aria-label") || "").includes(NINA_LOCK_REASON),
      `the ${h.getAttribute("data-handle")} handle is locked with no stated reason - `
      + `got ${JSON.stringify(h.getAttribute("aria-label"))}`);
    // Still focusable: a keyboard user has to be able to land on it and hear why.
    eq(h.getAttribute("tabindex"), "0",
      "a locked handle was taken out of the tab order, which hides the reason from the one user who needs it");
  }
  const note = q('[data-testid="preview-levels-lock"]');
  assert(note != null, "the lock reason is nowhere a sighted touch user can read it");
  eq((note.textContent || "").trim(), `Read-only - ${NINA_LOCK_REASON}`,
    "the on-screen sentence is not the one the handles carry");

  // ... and the two controls that DO work on this path still work.
  const auto = q('[data-testid="preview-stretch-auto"]');
  eq((useStore.getState() as any).stretch.auto, true, "precondition: auto is on");
  click(auto);
  eq((useStore.getState() as any).stretch.auto, false,
    "the Auto switch is dead on a pre-stretched frame, though it costs nothing there");
  const bright = q('[data-testid="preview-stretch-brightness"]');
  assert(bright != null, "there is no Brightness control on a pre-stretched frame");
  slide(bright, "0.5");
  eq((useStore.getState() as any).stretch.brightness, 0.5,
    "Brightness is dead on a pre-stretched frame, which is the one path it is FOR");
  assert(q('[data-testid="preview-stretch-contrast"]') != null,
    "the display-only Contrast control is missing on the path that has one");
});

test("a statistic the rig did not send reads as two dashes, never as a number", () => {
  // `Number(null)` is 0 - finite, plausible and wrong. A median of 0 ADU says
  // the sensor read nothing, and an operator would act on that.
  seed({
    previews: [frame({
      stats: { min: 120, max: 60000, mean: 900, median: null as any, std: undefined as any },
      hfr: undefined, stars: undefined,
    })],
  });
  mount({});
  eq(tileValue("frame-stats", "MEDIAN"), "--",
    "a missing median printed a number this component made up");
  eq(tileValue("frame-stats", "SIGMA"), "--",
    "a missing sigma printed a number this component made up");
  eq(tileValue("frame-stats", "MIN"), "120", "a statistic that WAS sent is not shown");
  eq(statText(null), "--", "the dash is hand-written at the call site rather than one rule");
  eq(statText(0), "0", "a real zero was turned into a dash, which loses a measurement");
});

test("no control anywhere in the document uses the native disabled attribute", () => {
  // ARCHITECTURE section 6 / non-negotiable 6, swept over the whole document so
  // the portalled download panel is included.
  //
  // The frame is chosen to LOCK things: pre-stretched (so the three levels, the
  // magnifier and the full-res export are impossible), no lossless base (so the
  // PNG export is), no star list (so STARS is), no tilt (so TILT is), and saved
  // on the host (so FITS is). A sweep over a screen with nothing locked on it
  // proves nothing at all, so the count of locked controls is asserted first.
  seed({
    previews: [frame({
      is_stretched: true, data_is_linear: false, has_lossless: false,
      saved_local: false, star_list: undefined, tilt: undefined,
    })],
    stretch: { ...STRETCH, advancedOpen: true },
  });
  mount({});
  click(q('[data-testid="preview-download"]'));
  const locked = [...win.document.body.querySelectorAll('[aria-disabled="true"]')];
  assert(locked.length >= 6,
    `only ${locked.length} controls are locked on a frame that cannot support most of them, `
    + "so this sweep would pass over a screen with nothing to check");
  const natives = [...win.document.body.querySelectorAll("[disabled]")];
  eq(natives.length, 0,
    `${natives.length} native disabled attribute(s): `
    + JSON.stringify(natives.map((n: any) => n.getAttribute("data-testid") || n.tagName)));
});

test("the FITS row points at this frame's own FITS route", () => {
  seed();
  mount({});
  click(q('[data-testid="preview-download"]'));
  const menu = qd('[data-testid="preview-download-menu"]');
  assert(menu != null, "the Download disclosure did not open");
  const fits = qd('[data-testid="preview-dl-fits"]');
  assert(fits != null && fits.tagName === "A",
    "no live FITS link for a frame with saved_local:true - the raw sub is unreachable from Inspect");
  eq(fits.getAttribute("href"), "/api/preview/5/fits",
    "the FITS link does not point at this frame's FITS route");
  const hrefs = [...menu.querySelectorAll("a")].map((a: any) => a.getAttribute("href"));
  assert(hrefs.some((h: string) => /\/render\.png/.test(h || "")),
    `no full-res PNG export offered - got ${JSON.stringify(hrefs)}`);
  assert(hrefs.some((h: string) => h === "/api/preview/5/lossless.png"),
    `no lossless PNG export offered - got ${JSON.stringify(hrefs)}`);
  assert(hrefs.some((h: string) => (h || "").startsWith("/api/preview/5/share.jpg")),
    `no share.jpg export offered - got ${JSON.stringify(hrefs)}`);
});

test("the download panel cannot outlive the trigger that offered it", () => {
  // `dlLock` flips the moment the link drops or the shown frame falls out of the
  // rig's ring, and the panel below the trigger used to keep rendering live
  // `<a download>` rows under a control that had just withdrawn them. An
  // `<a download>` that 404s reports nothing at all: the tap simply does nothing.
  seed({ previews: [frame({ id: 1 }), frame({ id: 5 })], selectedPreviewId: 1, livePreviewId: 5 });
  mount({});
  click(q('[data-testid="preview-download"]'));
  assert(qd('[data-testid="preview-download-menu"]') != null,
    "precondition: the panel is open on a frame four behind the newest");
  // Twenty-nine more frames land. The pinned one is now past the rig's retention
  // window, so every row in the open panel would 404.
  act(() => {
    useStore.setState({ previews: [frame({ id: 1 }), frame({ id: 30 })], livePreviewId: 30 } as never);
  });
  eq(qd('[data-testid="preview-download-menu"]'), null,
    "the open download panel went on offering rows the rig had already freed");
  eq(q('[data-testid="preview-download"]').getAttribute("aria-disabled"), "true",
    "the Download trigger did not lock when its frame left the rig's ring");
});

test("a frame the rig has already trimmed offers no download at all, and says why", () => {
  // Every capability flag is stamped at CAPTURE and never revised, so a pinned
  // frame goes on advertising exports whose bytes the rig freed minutes ago -
  // and an `<a download>` that 404s reports nothing whatsoever.
  seed({
    previews: [frame({ id: 1 }), frame({ id: 30 })],
    selectedPreviewId: 1,
    livePreviewId: 30,
  });
  mount({ id: "1" });
  const dl = q('[data-testid="preview-download"]');
  eq(dl.getAttribute("aria-disabled"), "true",
    "a frame 29 behind the newest still offers downloads that would 404");
  assert(/no longer on the rig/.test(dl.getAttribute("title") || ""),
    `the locked Download control does not say why - got ${JSON.stringify(dl.getAttribute("title"))}`);
  click(dl);
  eq(qd('[data-testid="preview-download-menu"]'), null,
    "the download panel opened underneath a trigger that had just withdrawn every row in it");
});

test("an operator is told who may take a FITS, before pressing anything", () => {
  // `view.media` is syncer + admin only (lib/caps.ts ROLE_CAPS). The toolbar
  // gates FITS on `saved_local` alone, because the server enforces the
  // capability with a 403 and the toolbar has no capability prop - so the sheet
  // says it, once, underneath.
  seed();
  mount({});
  const note = q('[data-testid="inspect-media-note"]');
  assert(note != null, "an operator sees no note about FITS access - the 403 is the first they hear of it");
  eq((note.textContent || "").trim(), "FITS downloads need syncer or admin access.",
    "the FITS note does not name the roles the server actually accepts");
  eq(fitsAccessNote(), "FITS downloads need syncer or admin access.",
    "the note is hand-written rather than composed from the role table");
});

test("the sheet's own thumb-reachable share link carries the run's caption", () => {
  const share = q('[data-testid="inspect-share"]');
  assert(share != null, "no share control at the foot of the sheet");
  eq(share.getAttribute("href"), "/api/preview/5/share.jpg?target=NGC+6946&subs=12",
    "the share link does not carry the target and sub count the caption needs");
});

test("picking a frame off the filmstrip pins it through the store", () => {
  // The strip is the one control here that writes shared state, and it must go
  // through `selectPreview` - a local copy would leave the classic UI, the stage
  // and this sheet each believing a different frame is on screen.
  seed({ previews: [frame({ id: 4 }), frame({ id: 5 })] });
  mount({});
  eq(useStore.getState().selectedPreviewId, null, "precondition: nothing is pinned yet");
  const tiles = [...container.querySelectorAll('[data-testid^="frame-tile-"]')];
  eq(tiles.length, 2, "the filmstrip is not showing both frames");
  eq(tiles[1].getAttribute("aria-pressed"), "true",
    "the strip does not say which frame the stage is painting");
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
  eq(useStore.getState().selectedPreviewId, 4, "opening Inspect with ?id=4 did not pin frame 4");
  assert(/frame #4/.test(text()), "the sheet is not showing the frame the deep link named");
});

test("a live-stacked sub names its rejected pixels instead of just counting them", () => {
  // The only place the rig publishes this is livestack.clipped - bright outliers
  // the stacker threw out. The bare number reads as damage; it is the stack
  // working.
  seed({
    previews: [frame({
      id: 5,
      livestack: { frames: 12, integrated_s: 720, rejected: 1, accepted: true, reason: "weak_align", clipped: 843 },
    })],
  });
  mount({});
  assert(q('[data-testid="livestack-readout"]') != null,
    "a sub carrying a live-stack block shows no live-stack readout");
  assert(/843 px clipped/.test(text()), "the rejected-outlier pixel count is not shown");
  assert(/satellite trail, an aircraft or a cosmic ray/.test(text()),
    "the outlier count is shown with no statement of what those pixels are");
  const badge = q('[data-testid="livestack-align"]');
  assert(badge != null && /WEAK ALIGN/.test(badge.textContent || ""),
    "a stack riding on one star shows the same reassuring frame count and no warning");
  assert(!/—/.test(text()), "an em-dash reached the screen from a shared logic module");
});

test("the focus verdict leads with the word and carries the numbers", () => {
  seed();
  mount({});
  const v = q('[data-testid="focus-verdict"]');
  assert(v != null, "there is no focus verdict");
  assert(/GOOD/.test(v.textContent || ""),
    `HFR 3.1 against good 3.5 / warn 5 should read GOOD - got ${JSON.stringify(v.textContent)}`);
  assert(/median HFR 3\.10 px/.test(v.textContent || ""),
    "the verdict word is not backed by the measurement it was derived from");
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
  eq(q('[data-testid="frame-stats"]'), null,
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
  assert(q('[data-testid="frame-filmstrip"]') != null,
    "the viewer's filmstrip is missing - a viewer sees the same screen an operator does");
  assert(q('[data-testid="preview-toolbar"]') != null, "the viewer's toolbar is missing");
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

// ------------------------------------------------------------ pure model rows
test("the overlay rows that need data only exist when the frame carries it", () => {
  // A permanently locked OBJECTS row on a rig that has never plate-solved a
  // light reads as a broken feature rather than an absent measurement.
  const bare = toggleRows(frame({ tilt: undefined, bahtinov: undefined, field: undefined }),
    { starsAvailable: true, clipAvailable: true });
  const ids = bare.map((r) => r.id);
  eq(ids.join(","), "stars,clip,reticle,centerMark,tilt",
    `a row exists for data this frame does not carry - got ${JSON.stringify(ids)}`);
  eq(bare.find((r) => r.id === "tilt")!.lockedReason != null, true,
    "the TILT row is live on a frame with no tilt measurement");
  const rich = toggleRows(
    frame({
      tilt: { zones: [], corner_ratio: 1 } as any,
      bahtinov: { geom: {} } as any,
      field: { objects: [{ id: "M 31" }] } as any,
    }),
    { starsAvailable: true, clipAvailable: true },
  );
  eq(rich.map((r) => r.id).join(","), "stars,clip,reticle,centerMark,tilt,bahtinov,objects",
    "a frame carrying tilt, spikes and objects does not offer those overlays");
});

act(() => { if (rootRef) rootRef.unmount(); });

const total = passed + failed;
console.log(`inspectDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
