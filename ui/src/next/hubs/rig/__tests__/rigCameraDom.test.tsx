// rigCameraDom.test.tsx - the CAMERA device sheet, MOUNTED (plan hub-rig.md
// B.1, task T-RIG-2).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigCameraDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// FOUR THINGS WORTH A TEST, and each one is a specific way this sheet could lie
// while still rendering perfectly:
//
//   1. PRECONDITION. The ring and all four readout tiles are on screen. Without
//      this every assertion below could pass on a sheet that mounted nothing -
//      "no control has the wrong attribute" is trivially true of no controls.
//   2. THE WRITE GOES TO THE RIG. Tapping GAIN and scrubbing the dial must PUT
//      `/api/camera/frame-settings?scope=capture` with the new gain. A
//      store-only write would make every surface agree with every other surface
//      and with nothing on the camera - the exact failure `setFrameSettings`
//      was built to replace.
//   3. A VIEWER SEES THE SAME SCREEN, INERT. The cooler switch carries the
//      reason and `aria-disabled`, stays in the tree, and pressing it fires NO
//      request. Not hidden; not natively disabled.
//   4. NO READING IS NOT ZERO DEGREES. With `camera.temperature: null` the ring
//      must draw an EMPTY arc and read `--`. An arc at 0 C looks exactly like a
//      camera sitting at freezing, and that is the whole reason the null is on
//      the wire.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/camera", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
// jsdom implements neither, and the Dial calls both behind a guard.
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame",
  // NOT `performance`: this jsdom's delegates to the global one, so copying it
  // makes performance.now() recurse until the stack blows.
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// The config the server would answer with. Declared up here because the fetch
// mock below has to answer `/api/config` with it - `setCoolingConfig` is
// followed by `loadConfig()`.
const CONFIG = {
  cooling: { warm_ramp: true, warm_rate_c_per_min: 2, warm_ambient_c: null },
  optics_computed: {
    have_optics: true, source: "camera", focal_length_mm: 540,
    pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176,
    image_scale_arcsec_px: 1.46, fov_w_deg: 2.54, fov_h_deg: 1.7, fov_diag_deg: 3.05,
  },
};

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: unknown }
const asked: Asked[] = [];
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ method, url: String(url), body: init?.body ? JSON.parse(init.body) : null });
  // `setCoolingConfig` is followed by `loadConfig()`, so a bare {} here would
  // blank the config the rest of the sheet reads and the assertions after the
  // write would be testing a half-mounted screen.
  if (String(url).includes("/api/config")) {
    const cooling = method === "POST"
      ? (JSON.parse(init!.body as string) as { cooling: unknown }).cooling
      : CONFIG.cooling;
    return { ok: true, status: 200, statusText: "OK", json: async () => ({ ...CONFIG, cooling }) };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { CameraSheet, cameraLiveLine, cameraSpecLine, binOptions, dialStops } =
  await import("../sheets/camera");
const { RING_CIRCUMFERENCE } = await import("../lib/coolerCurve");
type RigStatus = import("../../../../types").RigStatus;

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
const OPERATOR = {
  role: "operator",
  email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function camStatus(over: Record<string, unknown> = {}): RigStatus {
  return {
    connected: { camera: { name: "ZWO ASI2600MM Pro", kind: "camera", connected: true } },
    looping: false,
    busy_lanes: [],
    backend_links: [{ role: "camera", connected: true, error: null }],
    camera: {
      temperature: -9.4,
      can_cool: true,
      has_dew_heater: true,
      width: 6248,
      height: 4176,
      max_gain: 300,
      max_bin: 4,
      egain: 0,
      egain_learned: {},
      cooler: { on: true, power: 62, target_c: -10, at_target: false, can_report_power: true },
      ...(over.camera as object ?? {}),
    },
    ...over,
  } as unknown as RigStatus;
}

const FRAMES = {
  capture: { exposure_s: 120, gain: 100, offset: 50, binning: 1, filter: null },
  focus: { exposure_s: 3, gain: 100, offset: 50, binning: 1, filter: null },
  solve: { exposure_s: 3, gain: 100, offset: 50, binning: 1, filter: null },
  guide: { exposure_s: 2, gain: 100, offset: 10, binning: 1, filter: null },
};

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: camStatus(),
      equipConnected: true,
      wsPhase: "up",
      principal: OPERATOR,
      frameSettings: FRAMES,
      config: CONFIG,
      sequence: { state: "idle" },
      egainLearn: null,
      preview: null,
      toasts: [],
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(CameraSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function pointer(node: any, type: string, clientX: number): void {
  const e = new win.MouseEvent(type, { bubbles: true, cancelable: true, clientX });
  act(() => { node.dispatchEvent(e); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const text = () => (container.textContent || "") as string;

// ====================================================== 1. the precondition
seed();
mount();

test("the sheet mounted, with the ring and all four tiles on it", () => {
  assert(q('[data-testid="rig-camera"]') != null,
    "no sheet marker - the sheet did not render at all");
  const ring = q('[data-testid="camera-ring"]');
  assert(ring != null, "no cooler ring - the card mounted a shell");
  eq(ring.getAttribute("data-temp"), "-9.4",
    "the ring is not carrying the sensor temperature it is drawing");
  const labels = qa(".nx-readout-label").map((n) => n.textContent);
  eq(labels.length, 4, `expected four readout tiles, found ${labels.length}`);
  assert(labels.join("|") === "SETPOINT|GAIN|OFFSET|E-GAIN",
    `the tiles are not the four this sheet owns (${labels.join("|")})`);
  // E2: the design's fourth tile is USB bandwidth. There is no such setting in
  // this server, so the word must not be on the sheet at all.
  assert(!/\bUSB\b/.test(text()), "the sheet offers a USB bandwidth control the engine does not have");
});

test("the live line carries numbers the body does not repeat verbatim", () => {
  const line = q(".nx-sheet-live").textContent as string;
  assert(/cooling/.test(line), `the live line does not say what the cooler is doing (${line})`);
  assert(/62% power/.test(line), `the live line dropped the cooler power (${line})`);
  assert(/gain 100/.test(line) && /bin 1x1/.test(line),
    `the live line dropped the frame defaults (${line})`);
});

test("BINNING offers 1/2/4 within max_bin, never the design's 3x3 (E1)", () => {
  const opts = qa('[data-testid="binning"] .nx-seg-label').map((n) => n.textContent);
  eq(opts.join("|"), "1x1|2x2|4x4", `the binning options are wrong (${opts.join("|")})`);
  eq(binOptions(2).join(","), "1,2", "a max_bin of 2 still offered 4x4");
  eq(binOptions(undefined).join(","), "1,2,4", "an absent max_bin lost the default ceiling of 4");
});

test("the spec line is built from what the driver reported, not from the design's fixture", () => {
  const spec = q('[data-testid="camera-spec"]').textContent as string;
  assert(/6248 x 4176/.test(spec), `the spec line dropped the sensor geometry (${spec})`);
  assert(/26\.1 MP/.test(spec), `the spec line dropped the derived megapixels (${spec})`);
  assert(/gain to 300/.test(spec), `the spec line dropped the gain ceiling (${spec})`);
  // The design's fixture names a sensor, a bit depth, a full well and a read
  // noise. None of those is on `status.camera`, so none may be printed.
  assert(!/IMX571|16-bit|full-well|read noise/i.test(spec),
    `the spec line printed a field no driver reported (${spec})`);
  // And a driver that reported nothing prints nothing rather than a template.
  eq(cameraSpecLine(undefined, undefined, null), "", "an absent camera still produced a spec line");
});

test("the defaults sentence points at where a per-shot override lives", () => {
  assert(/defaults every flow and quick session starts from/.test(text()),
    "the sheet does not say these are defaults");
  assert(/Manual Capture can override them per shot/.test(text()),
    "the sheet does not say where a per-shot override lives");
});

// ============================================== 2. the write reaches the rig
await testAsync("tapping GAIN and scrubbing the dial PUTs the frame settings", async () => {
  asked.length = 0;
  click(q('[data-testid="tile-gain"]'));
  const track = q('[data-testid="dial-gain"] .nx-dial-track');
  assert(track != null, "selecting GAIN did not arm the dial");
  // Scrub left by one 64 px stop: 100 -> 150 on the merged stop list.
  pointer(track, "pointerdown", 300);
  pointer(track, "pointermove", 236);
  await settle();
  const put = asked.find((a) => a.method === "PUT");
  assert(put != null, `no PUT was made - the dial only moved local state (${JSON.stringify(asked)})`);
  eq(put!.url, "/api/camera/frame-settings?scope=capture",
    "the gain went to the wrong endpoint");
  eq(JSON.stringify(put!.body), JSON.stringify({ gain: 150 }),
    `the PUT body is not the new gain (${JSON.stringify(put!.body)})`);
});

test("the dial cannot centre on a stop the camera is not holding", () => {
  // A gain of 120 against 0/50/100/150/... resolves to findIndex -1, and the
  // Dial then centres on the FIRST stop: a control reading 0 while the camera
  // holds 120. The live value is merged into the stops instead.
  eq(dialStops([0, 50, 100, 150, 200, 300], 120, 300).join(","), "0,50,100,120,150,200,300",
    "the live gain is not a stop on its own dial");
  eq(dialStops([0, 50, 100, 150, 200, 300], 100, 120).join(","), "0,50,100,120",
    "the stops are not clamped to the driver's own gain ceiling");
});

// =============================================== 3. a viewer, honest-disabled
await testAsync("a viewer sees the cooler switch, dimmed, with the reason, and pressing it asks nothing", async () => {
  seed({ principal: VIEWER });
  mount();
  await settle();
  asked.length = 0;

  const sw = q('[data-testid="cooler-switch"]');
  assert(sw != null, "the cooler switch is HIDDEN from a viewer - it must be shown, inert");
  eq(sw.getAttribute("aria-disabled"), "true", "the switch is not marked aria-disabled");
  assert(!sw.hasAttribute("disabled"),
    "the switch uses the native disabled attribute, which takes the reason out of the a11y tree");
  eq(sw.getAttribute("title"), "needs operator or admin access",
    `the switch does not state who may press it (${sw.getAttribute("title")})`);
  assert(sw.className.includes("nx-locked"), "the switch does not render dimmed");

  click(sw);
  await settle();
  eq(asked.length, 0, `a viewer's press reached the rig (${JSON.stringify(asked)})`);
  // And it EXPLAINED rather than swallowing the tap.
  const toasts = (useStore.getState() as any).toasts as { title: string }[];
  assert(toasts.some((t) => /operator or admin/.test(t.title)),
    "the press was swallowed with no explanation");
});

test("a viewer's gain tile and binning are inert too, and still readable", () => {
  const bin = q('[data-testid="binning"]');
  eq(bin.getAttribute("aria-disabled"), "true", "binning is live for a viewer");
  assert(/1x1/.test(text()), "the binning value is hidden from a viewer rather than shown inert");
});

// ============================================ 4. no reading is not zero degrees
await testAsync("HONESTY: a camera reporting no temperature draws an EMPTY ring reading --", async () => {
  seed({
    principal: OPERATOR,
    status: camStatus({ camera: { temperature: null, cooler: { on: false, power: null, target_c: -10, at_target: false, can_report_power: false } } }),
  });
  mount();
  await settle();

  const ring = q('[data-testid="camera-ring"]');
  assert(ring != null, "the ring vanished instead of rendering empty");
  eq(ring.getAttribute("data-temp"), "", "the ring claims a temperature the camera did not report");
  eq(q(".nx-ring-value").textContent, "--", "the ring's centre invented a number");
  eq(q('[data-testid="camera-ring-arc"]').getAttribute("stroke-dasharray"),
    `0.0 ${RING_CIRCUMFERENCE}`, "an arc was drawn for a camera that reported nothing");
  eq(ring.getAttribute("data-tone"), "dim", "the empty ring is drawn as if it were live");
  // The header must not claim a temperature either.
  const line = q(".nx-sheet-live").textContent as string;
  assert(!/-?\d+\.\d°C/.test(line.replace("--", "")) || /--/.test(line),
    `the live line printed a temperature nothing measured (${line})`);
});

test("the curve says it is building rather than drawing two points as a trend", () => {
  const curve = q('[data-testid="camera-curve"]');
  assert(curve != null, "no curve element");
  eq(curve.getAttribute("data-ready"), "false", "a curve was drawn from fewer than three readings");
  assert(q('[data-testid="camera-curve-line"]') == null,
    "a polyline was drawn before there were readings to draw");
  assert(/building the curve/.test(text()), "the card does not say why there is no curve");
});

// ==================================== 5. the two config writes and the ramp
await testAsync("WARM UP BEFORE PARK sends the WHOLE cooling block, and never a setpoint", async () => {
  seed({ principal: { ...OPERATOR, role: "admin", caps: [...OPERATOR.caps, "config.safety", "control.power"] } });
  mount();
  await settle();
  asked.length = 0;

  click(q('[data-testid="warm-ramp"]'));
  await settle();
  const post = asked.find((a) => a.method === "POST" && a.url === "/api/config");
  assert(post != null, `the ramp switch wrote nothing (${JSON.stringify(asked)})`);
  const body = post!.body as { cooling: Record<string, unknown> };
  eq(body.cooling.warm_ramp, false, "the ramp switch did not flip the stored value");
  eq(body.cooling.warm_rate_c_per_min, 2, "the write dropped the rest of the block");
  assert(!("setpoint_c" in body.cooling),
    "the cooling write carried a setpoint - it would overwrite a Cool command from another screen");
});

test("the ramp rate is on the sheet, beside the promise it makes true", () => {
  assert(q('[data-testid="ramp-rate"]') != null, "the warm ramp rate has no control");
  assert(/2°C\/min/.test(text()), "the sheet does not print the rate the ramp actually uses");
});

await testAsync("the dew heater sends the level, and says the camera reports none back", async () => {
  asked.length = 0;
  click(q('[data-testid="dew-switch"]'));
  await settle();
  const post = asked.find((a) => a.url === "/api/camera/dew-heater");
  assert(post != null, "the dew heater switch sent nothing");
  eq(JSON.stringify(post!.body), JSON.stringify({ power: 100 }),
    `the dew heater body is wrong (${JSON.stringify(post!.body)})`);
  // E23: the design claims this heater follows the dew margin from Weather.
  assert(!/dew margin/i.test(text()),
    "the sheet claims the window heater follows the dew margin, which nothing in the engine does");
});

await testAsync("MEASURE GAIN runs the learn loop at the gain the camera is actually on", async () => {
  asked.length = 0;
  click(q('[data-testid="measure-gain"]'));
  await settle();
  const post = asked.find((a) => a.url === "/api/camera/egain/learn");
  assert(post != null, "MEASURE GAIN sent nothing");
  eq(JSON.stringify(post!.body), JSON.stringify({ gain: 100 }),
    `the learn loop was started at the wrong gain (${JSON.stringify(post!.body)})`);
});

// =========================================== 6. a run owns the camera
await testAsync("while a run has the camera, the controls say so and stay put", async () => {
  seed({ principal: OPERATOR, sequence: { state: "running", target: "NGC 6946" } });
  mount();
  await settle();
  asked.length = 0;

  const sw = q('[data-testid="cooler-switch"]');
  eq(sw.getAttribute("aria-disabled"), "true", "a running run left the cooler switch live");
  assert(/A run has the camera/.test(sw.getAttribute("title") || ""),
    `the reason does not name the blocker (${sw.getAttribute("title")})`);
  assert(/Session - Now/.test(sw.getAttribute("title") || ""),
    "the reason does not say where the run can be stopped");
  click(sw);
  await settle();
  eq(asked.length, 0, "a locked control still reached the rig");
});

// ============================================== 7. the not-connected state
await testAsync("with no camera the sheet still renders every control, honest-disabled", async () => {
  seed({
    principal: OPERATOR,
    equipConnected: false,
    status: {
      connected: {}, looping: false, busy_lanes: [],
      backend_links: [{ role: "camera", connected: false, error: null }],
    } as unknown as RigStatus,
  });
  mount();
  await settle();

  assert(q('[data-testid="camera-empty"]') != null, "no empty state for a missing camera");
  assert(/Assign a camera on ADD A DEVICE/.test(text()), "the empty state does not say what to do");
  const sw = q('[data-testid="cooler-switch"]');
  assert(sw != null, "the controls were REPLACED by the empty state instead of dimmed beside it");
  eq(sw.getAttribute("title"), "connect a camera first",
    `the reason is not the missing role (${sw.getAttribute("title")})`);
  eq(q(".nx-sheet-live").textContent, "NOT CONNECTED", "the live line claims a state");
  eq(cameraLiveLine(undefined, false, 100, 1), "NOT CONNECTED",
    "the live line helper invents a state for a camera that is not there");
});

// ============== polar alignment owns the camera on this sheet too (r4 #25)
// MEASURE GAIN exposes repeatedly at two gains. Polar alignment holds the
// camera for its whole run and spawns its own lane
// (server/astrodeck/hub.py:297); this sheet named the sequence and never named
// polar, so the button was live through an alignment and came back as a raw 409.
await testAsync("MEASURE GAIN is honest-disabled while an alignment owns the camera",
  async () => {
    const { POLAR_REASON } = await import("../capture/captureGate");
    seed();
    mount();
    await settle();
    const before = q('[data-testid="measure-gain"]');
    assert(before != null, "precondition: MEASURE GAIN is not on the sheet");
    eq(before.getAttribute("aria-disabled"), null,
      "precondition: MEASURE GAIN was already locked with no alignment running");

    seed({ status: camStatus({ busy_lanes: ["polar"] }) });
    mount();
    await settle();
    const btn = q('[data-testid="measure-gain"]');
    assert(btn != null, "MEASURE GAIN was hidden during an alignment instead of locked");
    eq(btn.getAttribute("aria-disabled"), "true",
      "MEASURE GAIN pressed through a polar alignment - the rig answers that with a raw 409");
    eq(btn.getAttribute("title"), POLAR_REASON,
      "MEASURE GAIN is locked but does not name the alignment");
    assert(btn.hasAttribute("disabled") === false,
      "MEASURE GAIN used the native disabled attribute, which takes the reason out of the "
      + "accessibility tree");

    asked.length = 0;
    click(btn);
    await settle();
    eq(asked.length, 0, `the press reached the rig anyway: ${JSON.stringify(asked)}`);
  });

act(() => { rootRef?.unmount(); });

const total = passed + failed;
console.log(`rigCameraDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
