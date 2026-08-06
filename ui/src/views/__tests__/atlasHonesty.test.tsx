// atlasHonesty.test.tsx — the Atlas page MOUNTED, and asked the three questions
// the 2026-08-05 audit found it answering wrongly.
//
//   Run directly:  npx tsx src/views/__tests__/atlasHonesty.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// All three are state-over-time claims — "does the control still say that after
// the rig answers / after the user does something else" — so none of them can be
// asserted from a single rendered tree. Hence jsdom, a real root, and real
// re-renders driven by the same store writes the WebSocket makes.
//
//   #39/#81  "Go to this target" reported the REQUEST, not the rig: the POST
//            _spawns the "goto" lane and resolves in ~40 ms, so the button sat
//            live and re-pressable for the whole 30–90 s slew and the second tap
//            it invited came back as a 409 painted "Couldn't slew".
//   #41      "Match camera" kept reading ON after any other zoom, and turning it
//            off then threw away every zoom made since.
//   #40      A rejected optics PUT left the typed number in the box while the
//            frame on the sky was still drawn from the stored one.
//
// WHAT THIS FIXTURE IS NOT. The sky canvas is mounted but blind: jsdom has no
// WebGL (so the tile engine gates itself off), no canvas 2D, and the survey
// fetch below returns a stub. Nothing here asserts anything about the picture —
// only about the controls around it.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Before the store, the api client or the view are imported: api.ts reads
// window.location at module scope and several components read matchMedia while
// evaluating module-level constants.
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
win.WebSocket = class { close() {} addEventListener() {} send() {} };
// SkyCanvas measures itself with one; jsdom ships none, and a throw inside that
// effect would fail the commit and take the whole page down with it.
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
// jsdom's getContext is a not-implemented stub that noisily logs. Returning null
// is what a canvas-less environment reports anyway, and it is the answer
// tileGLSupported() reads to decide it cannot use the tile engine.
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};

// ------------------------------------------------------- the fake rig backend
// Routed by path so a test can make ONE endpoint fail without inventing a whole
// offline mode. `puts` records what was actually sent, which is how the optics
// test tells a revert from a save.
const puts: { path: string; body: any }[] = [];
let putStatus = 200;
/** What /api/visibility answers about the framed point. Flipped by the #42 test
 *  so the page has a below-limit verdict worth (wrongly) repeating. */
let nightBelow = false;
function respond(body: any, status = 200): any {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 403 ? "Forbidden" : "OK",
    headers: { get: () => null },
    json: async () => body,
    blob: async () => ({}),
  };
}
const posts: string[] = [];
/** How long /api/sequence/preflight takes to answer — the window #81 lives in. */
let preflightDelayMs = 0;
win.fetch = async (url: any, init: any = {}) => {
  const path = String(url);
  const method = (init.method ?? "GET").toUpperCase();
  if (method === "POST") posts.push(path);
  if (path.includes("/api/sequence/preflight") && preflightDelayMs) {
    await new Promise((r) => setTimeout(r, preflightDelayMs));
  }
  if (method === "PUT") {
    puts.push({ path, body: init.body ? JSON.parse(init.body) : null });
    return respond(putStatus === 200 ? { ok: true } : { detail: "read-only" }, putStatus);
  }
  if (path.includes("/api/visibility")) {
    return respond({ ...NIGHT, never_rises_above_limit: nightBelow });
  }
  if (path.includes("/api/config")) return respond(CONFIG);
  return respond({});
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "Image", "URL", "fetch", "Blob",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const AtlasView = (await import("../AtlasView")).default;

// ------------------------------------------------------------------ fixtures
const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 4144,
  sensor_height_px: 2822,
  guide_focal_length_mm: null,
};
const CONFIG: any = {
  version: 7,
  optics: OPTICS,
  optics_computed: OPTICS,
  survey: { online_fetch: false },
};
const NIGHT: any = {
  alt_limit_deg: 30, never_rises_above_limit: false,
  transit_unix: 1_800_000_000, transit_alt: 72, transit_in_daylight: false,
  dark_start_unix: 1_799_990_000, dark_end_unix: 1_800_020_000,
  darkness_kind: "astronomical", best_window: null, samples: [],
  hours_above_limit: 6,
  moon: {
    illumination: 0.2, phase_name: "Waning Crescent", alt: -20, az: 90,
    separation_deg: 70, rise_unix: null, set_unix: null,
  },
};

/** A framed target, a connected mount, and an operator who may drive it — the
 *  only state in which the Go-to button is a button at all (otherwise the page
 *  renders a LockedChip and every assertion below would be about the wrong
 *  element). `busyLanes` is the rig's own answer, the thing under test. */
function seed(busyLanes: string[]): void {
  useStore.setState({
    status: {
      connected: { mount: true }, looping: false, mode: "sim",
      mount: {
        ra_hours: 0.712, dec_deg: 41.27, ra_str: "00h42m", dec_str: "+41°16'",
        alt: 62, az: 105, tracking: true, parked: false, slewing: false,
      },
      busy_lanes: busyLanes,
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
    config: CONFIG,
    site: { horizon_min_deg: 30, is_default: false },
  } as never);
}

function seedFraming(): void {
  useStore.setState({
    framing: {
      target: {
        id: "M 31", name: "Andromeda Galaxy", type: "Galaxy",
        ra_hours: 0.712, dec_deg: 41.27, size_arcmin: 190,
      },
      center: { ra_hours: 0.712, dec_deg: 41.27 },
      rotation_deg: 0,
      survey: "CDS/P/DSS2/color",
      stretch: "linear",
      fovZoomDeg: 2,
      mosaic: { rows: 1, cols: 1, overlap: 0.25 },
      panels: [],
    },
  } as never);
}

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

seed([]);
seedFraming();
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
await act(async () => { root.render(createElement(AtlasView)); });

/** The 2 s status frame — the same store write the WebSocket makes. */
async function statusFrame(busyLanes: string[]): Promise<void> {
  await act(async () => { seed(busyLanes); });
}
function byText(re: RegExp): any {
  return [...container.querySelectorAll("button")]
    .find((b: any) => re.test(b.textContent || ""));
}
const gotoBtn = () => byText(/Go to this target|Slewing|Checking altitude/);
const matchToggle = () =>
  container.querySelector('[role="switch"][aria-label*="field of view"]');
const zoomUp = () => container.querySelector('[aria-label="Increase FOV (zoom)"]');
const pixelField = () =>
  container.querySelector('input[aria-label^="Camera pixel size"]');
const opticsGear = () =>
  [...container.querySelectorAll("button")]
    .find((b: any) => (b.getAttribute("aria-label") || "").startsWith("Camera and telescope specs"));

function click(node: any): void {
  node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
}
/** Let a PUT → catch → setState chain settle. Several microtask turns, inside
 *  act, so React commits whatever the rejection produced. */
async function flush(): Promise<void> {
  await act(async () => {
    for (let i = 0; i < 5; i++) await Promise.resolve();
    await new Promise((r) => setTimeout(r, 0));
  });
}

// -------------------------------------------------------------- preconditions
await test("the Atlas page actually mounted, framed, with a live Go-to button", () => {
  // The anti-blank-page guard. Half the assertions below are "X no longer says
  // Y", which a page that never rendered would pass without trying.
  assert(/Andromeda Galaxy/.test(container.textContent || ""),
    "the framed target's name is not on the page — the fixture never rendered");
  const b = gotoBtn();
  assert(b != null, "no Go-to button — the mount/caps fixture is wrong, so this " +
    "is the LockedChip branch and the busy tests below would assert nothing");
  assert(b.disabled === false, "the Go-to button starts disabled with no lane busy");
});

// ------------------------------------------------------- #39/#81 the goto lane
await test("the rig reporting the goto lane busy disables Go-to and says why", async () => {
  await statusFrame(["goto"]);
  const b = gotoBtn();
  assert(b != null, "the Go-to button vanished when the lane went busy");
  assert(/Slewing/.test(b.textContent || ""),
    `Go-to still reads "${(b.textContent || "").trim()}" while the rig reports the ` +
    "goto lane in flight — it is describing the tap, not the mount");
  assert(b.disabled === true,
    "Go-to is still pressable during the slew; the second tap it invites hits " +
    "_spawn's 409 and is painted as 'Couldn't slew'");
  assert(b.getAttribute("aria-busy") === "true", "no aria-busy during the slew");
});

await test("the dead button explains itself, and stops when the rig stops", async () => {
  const during = container.textContent || "";
  assert(/comes back when the rig reports the move finished/.test(during),
    "the button is disabled with no sentence saying why or when it returns");
  await statusFrame([]);
  const b = gotoBtn();
  assert(/Go to this target/.test(b.textContent || ""),
    "Go-to did not come back when the rig dropped the goto lane");
  assert(b.disabled === false, "Go-to stayed disabled after the lane cleared");
});

await test("an unrelated lane does not disable Go-to", async () => {
  // The reduced `status.busy` maps seven different operations onto four words;
  // reading it would have made an autofocus look like a slew.
  await statusFrame(["autofocus", "capture"]);
  assert(gotoBtn().disabled === false,
    "Go-to is disabled while some other lane is busy — it is reading the " +
    "coarse busy word, not its own lane");
  await statusFrame([]);
});

// --------------------------------------------------------- #41 "Match camera"
await test("Match camera turns itself off when the user zooms somewhere else", async () => {
  const t = matchToggle();
  assert(t != null, "no Match camera switch on the page");
  await act(async () => { click(t); });
  // POSITIVE CONTROL: without this, "it reads OFF afterwards" is what a switch
  // that never turned on says too.
  assert(matchToggle().getAttribute("aria-checked") === "true",
    "the Match camera switch did not take the click, so asserting it goes OFF " +
    "below would prove nothing");
  const matched = useStore.getState().framing!.fovZoomDeg;

  await act(async () => { click(zoomUp()); });
  assert(matchToggle().getAttribute("aria-checked") === "false",
    "Match camera still reads ON after the user zoomed somewhere else — the " +
    "switch is claiming the view matches the camera when it does not");
  const zoomed = useStore.getState().framing!.fovZoomDeg;
  assert(zoomed !== matched, "the zoom did not actually change; the test proved nothing");
});

await test("a manual zoom retires the restore point the match saved", async () => {
  // The other half of #41. `prev_zoom_deg` is what OFF restores; leaving it set
  // across a manual zoom is what made OFF discard everything since.
  if (matchToggle().getAttribute("aria-checked") === "true") {
    await act(async () => { click(matchToggle()); });
  }
  await act(async () => { click(matchToggle()); });   // ON
  const saved = useStore.getState().framing!.prev_zoom_deg;
  // PRECONDITION: without a restore point there is nothing to retire, and the
  // assertion below would hold for a build that never saved one.
  assert(saved != null,
    "turning Match camera on saved no zoom to restore, so 'the restore point is " +
    "gone afterwards' would prove nothing");

  await act(async () => { click(zoomUp()); });
  assert(useStore.getState().framing!.prev_zoom_deg === undefined,
    `the pre-match zoom ${saved}° is still saved after the user zoomed themselves — ` +
    "the next tap on that switch jumps the view back to it and throws their zoom away");
});

await test("match, zoom away, match, unmatch — you get YOUR zoom back", async () => {
  // The same defect stated as the user's round trip, end to end. Entering with
  // the switch OFF is itself part of the claim: the previous test left it after
  // a manual zoom, and a build that keeps it ON never reaches this state at all.
  assert(matchToggle().getAttribute("aria-checked") === "false",
    "the switch is still on after the previous test's manual zoom, so this round " +
    "trip would start somewhere the fixed build never starts");
  const mine = useStore.getState().framing!.fovZoomDeg;
  await act(async () => { click(matchToggle()); });   // match (saves `mine`)
  assert(matchToggle().getAttribute("aria-checked") === "true",
    "the switch did not go on, so the restore below is not the one under test");
  await act(async () => { click(matchToggle()); });   // unmatch
  assert(useStore.getState().framing!.fovZoomDeg === mine,
    `unmatching landed on ${useStore.getState().framing!.fovZoomDeg}°, not the ` +
    `${mine}° the user was looking at when they matched`);
});

// ------------------------------------------------------------ #40 optics revert
await test("a rejected optics save puts the stored number back in the box", async () => {
  await act(async () => { click(opticsGear()); });
  const f = pixelField();
  assert(f != null, "the optics drawer did not open — no pixel-size field to test");
  assert(f.value === "3.76", `pixel field seeded "${f.value}", expected the stored 3.76`);

  putStatus = 403;                      // a viewer, a read-only config, a 409…
  puts.length = 0;
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(f, "9.99");
    f.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  assert(pixelField().value === "9.99", "the typed value never reached the field");
  // React 18 delivers onBlur from the bubbling `focusout`, not `blur`.
  await act(async () => {
    pixelField().dispatchEvent(new win.Event("focusout", { bubbles: true }));
  });
  await flush();

  assert(puts.length === 1, `expected one PUT /api/optics, saw ${puts.length}`);
  assert(pixelField().value === "3.76",
    `the field still reads "${pixelField().value}" after the save was refused — ` +
    "the number on screen is not the number the frame is drawn from");
  putStatus = 200;
});

// -------------------------------------------------- #42 tonight, for THIS point
/** Move the framed centre the way a drag on the canvas does. */
async function moveCentre(ra: number, dec: number): Promise<void> {
  await act(async () => {
    useStore.getState().setFraming({ center: { ra_hours: ra, dec_deg: dec } });
  });
}
/** Past VisibilityPanel's 300 ms debounce and its round trip. */
async function settle(): Promise<void> {
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await flush();
}

await test("the below-limit verdict stops speaking the moment the frame moves", async () => {
  nightBelow = true;
  await moveCentre(3.0, 45.0);
  await settle();
  // PRECONDITION: there is a standing verdict on screen to be wrong about.
  assert(/stays below/.test(container.textContent || ""),
    "no below-limit banner after a below-limit night landed — the assertion " +
    "below would pass on a page that never shows one at all");

  await moveCentre(12.0, -20.0);   // the drag
  const during = container.textContent || "";
  assert(!/stays below/.test(during),
    "the below-limit banner is still up, naming the target that is now framed, " +
    "with the verdict computed for the point the frame has already left");
  assert(/Checking tonight’s altitude for the new centre/.test(during),
    "nothing on the mosaic panel says the altitude answer is being recomputed");
  assert(/Recomputing for the new centre/.test(during),
    "the Tonight chart keeps its transit / best-window / moon numbers up with " +
    "no sign they answer for a different patch of sky");

  nightBelow = false;
  await settle();
  const after = container.textContent || "";
  assert(!/Recomputing for the new centre/.test(after),
    "the chart is still flagged stale after the new night landed");
  assert(!/stays below/.test(after),
    "the below-limit banner came back for a night that is not below the limit");
});

// ------------------------------------------- #81 the gap before the lane exists
await test("a second tap during the altitude check never reaches the mount", async () => {
  // The guard used to be set AFTER the preflight GET and its dialog, so the tap
  // an unresponsive-looking button invites got all the way to `_spawn`, 409'd,
  // and printed a red "Couldn't slew" beside the first tap's green success.
  preflightDelayMs = 40;
  posts.length = 0;
  await act(async () => { click(gotoBtn()); });
  // PRECONDITION: the altitude check really is still in flight. Without this,
  // "only one POST" is what a build that dropped BOTH taps also reports.
  assert(/Checking altitude/.test(gotoBtn().textContent || ""),
    "the button is not in its preflight state, so the second tap below is not " +
    "landing in the window this test is about");
  assert(gotoBtn().disabled === true, "the button stayed live during the altitude check");

  await act(async () => { click(gotoBtn()); });   // the impatient second tap
  await settle();

  const gotos = posts.filter((p) => p.includes("/api/mount/goto"));
  assert(gotos.length === 1,
    `${gotos.length} goto POSTs reached the server from two taps — the second one ` +
    "gets _spawn's 409 and is painted as a failed slew");
  preflightDelayMs = 0;
});

// ------------------------------------------------------------------- report
await act(async () => { root.unmount(); });
const total = passed + failed;
console.log(`atlasHonesty.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
