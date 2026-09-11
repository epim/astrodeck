// quickOscDom.test.tsx - the quick-session sheet on a rig with NO FILTER WHEEL.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/quickOscDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// `quickSessionDom.test.tsx` mounts this sheet over a five-slot wheel and only
// ever over a five-slot wheel. Every rig without one - a one-shot-colour camera
// on a small refractor, a mono camera with the wheel unplugged, a wheel with a
// single clear slot - went through a branch nothing exercised.
//
// THE SIX THINGS THIS FILE IS FOR:
//
//  1. A PRECONDITION MARKER. The sheet is on screen AND it is showing the
//     one-channel card, not an empty div. Without that every "no filter rows"
//     assertion below would pass over a blank page, which is the trap this repo
//     already has a name for.
//  2. NO PHANTOM CYCLE. No FILTER CYCLE header, no filter rows, no "0 filters"
//     on the button, no assumed-seven note. A rig with no wheel has no cycle,
//     and a screen that draws one is describing a different night.
//  3. THE ARITHMETIC IS THE ENGINE'S. 6 h at 120 s is 180 subs; press the
//     exposure and 6 h at 180 s is 120. Both numbers are read off the screen.
//  4. THE EXACT POSTED BODY. `filters: []` (there is nothing to command, and an
//     invented filter name is what would land in the FITS header), the OSC
//     exposure, and the sub count the card is showing.
//  5. NEVER "RGB". A mono camera with no wheel shoots luminance. With neither
//     the status bus nor a captured frame naming a colour, the card says ONE
//     CHANNEL and the word RGB is nowhere.
//  6. THE STATUS BUS ANSWERS TOO (ruling Q7), and does so without ever needing
//     a captured frame. `quick.tsx` resolves the colour through
//     `resolveColour(status.camera, preview.bayer_pattern)`, so a camera that
//     has told the status bus it is one-shot colour is described as such from
//     the moment it connects - section 0 mounts exactly that rig, with no
//     frame ever captured, and reads the answer off the screen. The frame
//     remains the FALLBACK rung for an engine that publishes no colour fields
//     at all, and `oscLabel` still coerces a bare string or a `null` at its
//     top rather than trusting the call site, because a crashed sheet is a
//     worse defect than a stale label.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `quick.tsx` reaches into `session/flows/create`, which imports `create.css`.
// Node has no idea what a `.css` file is, so a synchronous load hook answers
// with an empty module. It has to run BEFORE any import that reaches one,
// which is why every import in this file is dynamic and below this block
// (copied verbatim from `hubs/settings/__tests__/filesSheetsDom.test.tsx:44-61`).
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

/** The persisted per-phone default, written BEFORE a render: the sheet reads it
 *  in a lazy initialiser. Six hours is the window every count below is over,
 *  and clearing `exp` matters - a press in one case must not set the exposure
 *  the next case then asserts. */
function seedPrefs(): void {
  win.localStorage.setItem("astrodeck-next-sky-quick", JSON.stringify({ hours: 6 }));
}
seedPrefs();

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
    tagline: "", graph: { nodes: [], edges: [] },
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
const { ASSUMED_WHEEL_NOTE, OSC_FOOTER, ONE_CHANNEL_FOOTER } = await import("../quickCopy");
const { oscLabel, resolveColour } = await import("../quickModel");

// ------------------------------------------------------------------- harness
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
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const text = (): string => String(container.textContent ?? "");

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount", "control.guide"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

const FRAME_SETTINGS = {
  capture: { exposure_s: 60, gain: 100, offset: 30, binning: 1, filter: null },
  focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
  solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
  guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
};

/**
 * A rig with a camera and a mount and NO WHEEL.
 *
 * `filterwheel` is ABSENT, not an empty object: that is what `hub.poll_status`
 * publishes when no wheel is connected, and the absence is the whole signal -
 * a wheel that is attached and merely names nothing is a different rig and gets
 * a different sentence.
 */
const OSC_STATUS = {
  connected: {
    camera: { connected: true, name: "sim" },
    telescope: { connected: true, name: "sim" },
  },
  looping: false,
  camera: { temperature: -10, can_cool: true, width: 6248, height: 4176, max_gain: 300, max_bin: 4 },
  busy_lanes: [] as string[],
};

/** The FALLBACK colour signal: a bayer pattern off a captured frame
 *  (`PreviewInfo.bayer_pattern`), which is all an engine older than the
 *  camera-colour status fields ever sends. */
const OSC_PREVIEW = {
  id: 1, ts: 0, exposure_s: 120, gain: 100, binning: 1,
  data_width: 6248, data_height: 4176, display_width: 1400, display_height: 936,
  mime: "image/jpeg", source: "native", is_stretched: false, data_is_linear: true,
  has_lossless: false, full_well: null, bayer_pattern: "RGGB",
  auto_levels: { black: 0, mid: 0.5, white: 1 },
};

function seed(opts: {
  principal?: unknown;
  preview?: unknown;
  filterwheel?: unknown;
  /** False = nothing plugged in: a laptop planner, not a one-channel rig. */
  camera?: boolean;
  /** The camera's OWN colour self-report on the status bus (`camera.is_color`
   *  / `camera.bayer_pattern`), which needs no captured frame. */
  cameraColour?: { is_color?: boolean | null; bayer_pattern?: string | null };
} = {}): void {
  const camera = opts.camera !== false;
  const status = {
    ...OSC_STATUS,
    camera: { ...OSC_STATUS.camera, ...(opts.cameraColour ?? {}) },
    connected: camera ? OSC_STATUS.connected : {},
    ...(opts.filterwheel != null ? { filterwheel: opts.filterwheel } : {}),
  };
  useStore.setState({
    principal: opts.principal ?? OPERATOR,
    equipConnected: camera,
    wsPhase: "up",
    site: {
      name: "Back lawn", latitude: 47.6, longitude: -122.3, elevation_m: 50,
      is_default: false, horizon_min_deg: 20,
    },
    status,
    preview: opts.preview ?? null,
    weather: null,
    frameSettings: FRAME_SETTINGS,
  } as never);
}

/** Mount the sheet fresh, with the persisted prefs reset first. */
async function mount(opts: Parameters<typeof seed>[0] = {}): Promise<{ unmount(): Promise<void> }> {
  seedPrefs();
  seed(opts);
  const root = createRoot(container);
  await act(async () => {
    root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
  });
  await settle();
  return { unmount: async () => { await act(async () => { root.unmount(); }); } };
}

// ============================================== 0. status alone, no frame ever
//
// The camera says it is one-shot colour on the status bus and NO frame has
// ever been captured (`preview: null`). This is the state every OSC rig is in
// for the first several minutes of every session, and the only rung that can
// answer for it is the camera's own self-report - so it is asserted on the
// mounted card, not just against the two pure functions behind it.
test("the model rung: is_color alone is a colour claim and names no matrix", () => {
  const colour = resolveColour({ is_color: true, bayer_pattern: null }, null);
  eq(colour.source, "status", "the status bus answered - no frame involved at all");
  eq(colour.pattern, null, "is_color alone names no matrix");
  const label = oscLabel(colour);
  eq(label.title, "ONE-SHOT COLOUR - NO WHEEL", "the title for a camera that said colour");
  assert(!/[A-Z]{4}/.test(label.sub), "no matrix is named when only is_color spoke");
});

await testAsync("a camera that has said is_color, with no frame ever captured, renders as one-shot colour", async () => {
  const m = await mount({ preview: null, cameraColour: { is_color: true, bayer_pattern: null } });
  try {
    assert(q('[data-testid="quick-osc-exposure"]') != null,
      "precondition: the one-channel card is not on screen, so nothing below is about it");
    eq(q('[data-testid="quick-osc-title"]')?.textContent, "ONE-SHOT COLOUR - NO WHEEL",
      "the status bus said colour and the card did not");
    assert(/the camera reports one-shot colour/.test(q('[data-testid="quick-osc-sub"]')?.textContent ?? ""),
      "the sub line must say WHO said it - a frame and a driver are different confidences");
    assert(text().includes(OSC_FOOTER),
      "the colour footer belongs under a camera that claimed colour");
  } finally { await m.unmount(); }
});

// ============================================================ 1. one-shot colour
let live = await mount({ preview: OSC_PREVIEW });

// ------------------------------------------------------------ 1. the marker
test("the sheet rendered, and it is showing the one-channel card", () => {
  assert(q('[data-testid="sky-quick"]') != null,
    "no sky-quick marker: the fixture is wrong, not the component");
  assert(/ONE-SHOT COLOUR|ONE CHANNEL/.test(text()),
    `no one-channel card - nothing below this asserts anything: ${text().slice(0, 200)}`);
  assert(q('[data-testid="quick-osc-exposure"]') != null,
    "no EXPOSURE control: the sheet has no way to choose a sub length");
  eq(q('[data-testid="quick-osc-title"]')?.textContent, "ONE-SHOT COLOUR - NO WHEEL",
    "a frame reported RGGB, so the card may say colour");
  assert(/RGGB/.test(q('[data-testid="quick-osc-sub"]')?.textContent ?? ""),
    "the sub must name the matrix the rig reported, not assert a colour of its own");
});

// -------------------------------------------------------- 2. no phantom cycle
test("a rig with no wheel is offered no filter cycle", () => {
  eq(qa("[data-filter-row]").length, 0,
    "filter rows on a rig with no wheel - none of them can reach the payload");
  assert(!/FILTER CYCLE/.test(text()), "a FILTER CYCLE header over a rig that has no cycle");
  assert(!/\d+ of \d+ from the wheel/.test(text()), "a wheel count over a rig with no wheel");
  assert(!text().includes(ASSUMED_WHEEL_NOTE),
    "the assumed seven belong to a rig we cannot ask, not to one that answered");
  const cta = q('[data-testid="quick-generate"]');
  assert(!/\bfilters\b/.test(cta.textContent),
    `the CTA counts filters on a rig with none: ${cta.textContent}`);
  assert(!/0 filters/.test(text()), "and never zero of them");
});

test("the footer describes one exposure repeated, not a cycle of passes", () => {
  assert(text().includes(OSC_FOOTER),
    "the one-channel footer is missing - FILTER_FOOTER's 'one sub per checked filter' "
    + "promises a balanced set this night cannot contain");
  assert(!/one sub per checked filter/.test(text()), "the cycle footer must not be here");
});

// ---------------------------------------------------------- 3. the arithmetic
test("6 hours at 120 s is 180 subs, on the card and on the button", () => {
  eq(q('[data-testid="quick-osc-exposure"]')?.textContent, "120 s", "the default sub length");
  eq(q("[data-osc-count]")?.getAttribute("data-osc-count"), "180", "21600 / 120");
  eq(q('[data-testid="quick-osc-count"]')?.textContent, "×180", "and it is on screen");
  const cta = q('[data-testid="quick-generate"]').textContent as string;
  assert(/^GENERATE FLOW · 120s × 180 · 6h 00m$/.test(cta),
    `the CTA does not describe the night: ${cta}`);
  assert(!/[—–]/.test(cta), "em-dashes are not house style");
  assert(cta.includes("6h 00m"), "the window the count was taken over");
});

await testAsync("pressing the exposure moves the count with it", async () => {
  await act(async () => {
    q('[data-testid="quick-osc-exposure"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();
  eq(q('[data-testid="quick-osc-exposure"]')?.textContent, "180 s", "the next stop in EXPOSURES");
  eq(q("[data-osc-count]")?.getAttribute("data-osc-count"), "120", "21600 / 180");
  assert(/120s × 120 · 6h 00m/.test(q('[data-testid="quick-generate"]').textContent) === false,
    "the CTA still carries the old exposure");
  assert(/180s × 120 · 6h 00m/.test(q('[data-testid="quick-generate"]').textContent),
    `the CTA did not follow the card: ${q('[data-testid="quick-generate"]').textContent}`);
});

// -------------------------------------------------------- 4. the posted body
await testAsync("GENERATE FLOW posts an empty filter list and the OSC count", async () => {
  const before = quickPosts().length;
  await act(async () => {
    q('[data-testid="quick-generate"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();
  const posts = quickPosts();
  eq(posts.length - before, 1, "exactly one POST /api/flows/quick");
  const body = posts[posts.length - 1].body;
  // There is nothing to command and no name that would be true, so the list is
  // empty: an invented filter name is what would land in the FITS header.
  eq(Array.isArray(body.filters), true, "`filters` must still be a list");
  eq(body.filters.length, 0, "a rig with no wheel sends no filter names");
  eq(body.exposures.OSC, 180, "the sub length the card is showing");
  eq(Object.keys(body.exposures).join(","), "OSC", "one channel, one exposure");
  // The assertion between the screen and a night of the wrong length.
  eq(body.subs, 120, "`subs` is the count on the card, not the pass count of a cycle");
  eq(body.target.name, "Andromeda Galaxy", "");
  eq(body.run, false, "the sheet saves; the flow card runs");
});

await live.unmount();

// =============================================================== 5. never RGB
await testAsync("a mono camera with no wheel is never called RGB", async () => {
  // Same rig, no frame has come back, so nothing has said the sensor is colour.
  live = await mount({ preview: null });
  assert(q('[data-testid="sky-quick"]') != null, "the sheet must be on screen to assert about it");
  eq(q('[data-testid="quick-osc-title"]')?.textContent, "ONE CHANNEL - NO WHEEL",
    "with no bayer pattern the card may not claim a colour");
  assert(!/RGB/.test(text()),
    `"RGB" is a claim this rig never made: ${text().slice(0, 300)}`);
  assert(text().includes(ONE_CHANNEL_FOOTER), "the footer without the colour clause");
  assert(!text().includes("sensor's own matrix"),
    "a mono stack has no colour in it, so the matrix sentence must not be printed");
  eq(qa("[data-filter-row]").length, 0, "still no cycle");
  await live.unmount();
});

// ========================================================= 6. one clear slot
await testAsync("a wheel with one clear slot prints the slot's own name", async () => {
  live = await mount({ filterwheel: { names: ["L"], opaque: [false], position: 0 } });
  assert(q('[data-testid="sky-quick"]') != null, "the sheet must be on screen");
  const title = q('[data-testid="quick-osc-title"]')?.textContent ?? "";
  assert(/\bL\b/.test(title), `the wheel's own name is missing from the card: ${title}`);
  assert(!/NO WHEEL/.test(title), "there IS a wheel - it just has one clear slot");
  eq(qa("[data-filter-row]").length, 0, "one slot is not a cycle");
  assert(!/FILTER CYCLE/.test(text()), "and gets no cycle header");

  // AND the payload names it. `filters: []` here would leave a carousel parked
  // on a blackout slot exactly where it is for the whole night.
  const before = quickPosts().length;
  await act(async () => {
    q('[data-testid="quick-generate"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();
  const posts = quickPosts();
  eq(posts.length - before, 1, "exactly one POST /api/flows/quick");
  const body = posts[posts.length - 1].body;
  eq(body.filters.join(","), "L", "the one slot is commanded by name");
  eq(body.exposures.L, 60, "and keeps its own sub length");
  eq(body.subs, 360, "21600 / 60");
  await live.unmount();
});

// ====================================================== 6b. no rig to ask at all
//
// The other half of the split. `ASSUMED_WHEEL_NOTE` was DEAD CODE: it rendered
// only in the checklist branch, and "not this rig's names" used to force the
// one-channel branch, so the one place it could appear was the one place that
// never ran. A planner with nothing plugged in now gets the assumed seven, and
// the note is what stops them reading it as a wheel the app found.
await testAsync("with nothing connected the seven are offered, and said to be assumed", async () => {
  live = await mount({ camera: false });
  assert(q('[data-testid="sky-quick"]') != null, "the sheet must be on screen");
  assert(/FILTER CYCLE/.test(text()), "a planner still gets a cycle to think about");
  eq(qa("[data-filter-row]").length, 7, "the assumed seven");
  assert(text().includes(ASSUMED_WHEEL_NOTE),
    `the seven must be labelled as assumed, not passed off as a wheel: ${text().slice(0, 400)}`);
  assert(q('[data-testid="quick-osc-exposure"]') == null,
    "no rig has said this one is one-channel, so it must not claim to be");
  assert(!/ONE-SHOT COLOUR|NO WHEEL/.test(text()),
    "a colour claim about a camera that is not there");
  await live.unmount();
});

// ============================================================== 7. the viewer
await testAsync("a viewer sees the whole card, honest-disabled, and fires nothing", async () => {
  live = await mount({ principal: VIEWER, preview: OSC_PREVIEW });

  // Positive control: the read-only screen is a WHOLE screen, not a stub.
  assert(/ONE-SHOT COLOUR/.test(text()), "a viewer must see the same card");
  assert(q('[data-testid="quick-osc-exposure"]') != null, "and the same controls");

  const cta = q('[data-testid="quick-generate"]');
  eq(cta.getAttribute("aria-disabled"), "true", "the CTA is refused for a viewer");
  assert(cta.getAttribute("disabled") == null,
    "never the native disabled attribute on a control a user could want to press");
  const why = cta.getAttribute("title") ?? "";
  assert(why.includes("needs operator or admin access"),
    `the reason must name the access level, got ${why}`);

  const before = quickPosts().length;
  await act(async () => {
    cta.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(quickPosts().length, before, "a viewer's press must not reach /api/flows/quick");
  await live.unmount();
});

const total = passed + failed;
console.log(`quickOscDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
