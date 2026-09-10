// rigWheelDom.test.tsx - the FILTER WHEEL device sheet, MOUNTED (plan
// hub-rig.md B.6, task T-RIG-4).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigWheelDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// FIVE THINGS WORTH A TEST:
//
//   1. PRECONDITION. n slot buttons on the ring AND n rows in the table, from
//      one seven-name wheel. Without it, every assertion below could pass on a
//      sheet that mounted nothing.
//   2. A TAP IS A MOVE. `POST /api/filterwheel/position {position}` - the ring
//      is not a picture of the wheel, it is the control for it.
//   3. AN OFFSET EDIT WRITES THE WHOLE BODY, AND ONLY THE ARRAY IT CHANGED.
//      `POST /api/filterwheel/names` carrying `names`, the new `offsets`, and
//      an explicit `null` for `opaque`/`narrowband`/`exposures`/`gains`. The
//      nulls are the anti-clobber property (`server-routes.md` 4.2: null means
//      "leave the stored value alone"), so an offset edit here cannot revert a
//      name edit landing from another client at the same moment. Sending `[]`
//      or a stale copy instead would silently wipe blackout flags and per-filter
//      exposures - which is why this assertion is also the sabotage this task
//      ran for real.
//   4. A BLACKOUT SLOT HAS NO OFFSET TO SET. A slot with no light path cannot be
//      measured, so the stepper is replaced by an em-dash stand-in whose
//      aria-label says why - verbatim from `FilterNamesModal.tsx:377-396`, which
//      is where that sentence was written and where the reason belongs.
//   5. A VIEWER SEES THE SAME WHEEL, INERT. Slot taps carry the reason, and
//      pressing one fires NO request.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/wheel", pretendToBeVisual: true },
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
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ method, url: String(url), body: init?.body ? JSON.parse(init.body) : null });
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { WheelSheet, WRITE_DEBOUNCE_MS, OFFSET_STEP, RING_CAPTION } =
  await import("../sheets/wheel");
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
/** Past the write debounce, then settle. A held press is meant to be ONE POST,
 *  so every table assertion has to wait the trailing edge out. */
const flushWrites = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, WRITE_DEBOUNCE_MS + 60)); });
  await settle();
};

// ------------------------------------------------------------------ fixtures
const NAMES = ["L", "R", "G", "B", "Ha", "OIII", "SII"];
const OPERATOR = {
  role: "operator",
  email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function wheelStatus(over: Record<string, unknown> = {}): RigStatus {
  const fw = {
    position: 0,
    names: NAMES,
    current: "L",
    offsets: [0, 12, -8, 4, 60, 55, 58],
    opaque: [false, false, false, false, false, false, false],
    narrowband: [false, false, false, false, true, true, true],
    exposures: [60, 120, 120, 120, 300, 300, 300],
    gains: [100, 100, 100, 100, 200, 200, 200],
    moving: false,
    ...(over.filterwheel as object ?? {}),
  };
  return {
    connected: {
      filterwheel: { name: "Wanderer Snowflake", kind: "filterwheel", connected: true },
      focuser: { name: "ZWO EAF", kind: "focuser", connected: true },
      camera: { name: "ZWO ASI2600MM Pro", kind: "camera", connected: true },
    },
    looping: false,
    busy_lanes: [],
    backend_links: [
      { role: "filterwheel", connected: true, error: null },
      { role: "focuser", connected: true, error: null },
      { role: "camera", connected: true, error: null },
    ],
    filterwheel: fw,
    focuser: { position: 19950, max: 20000, temperature: 11.2, moving: false },
    camera: {
      temperature: -10, can_cool: true, width: 6248, height: 4176,
      max_gain: 300, max_bin: 4,
    },
    ...over,
  } as unknown as RigStatus;
}

const CONFIG = {
  standards: {
    apply_filter_offsets: true, refocus_on_temp_delta_c: 1.5, min_stars: 0,
    max_guide_rms: 0, max_eccentricity: 0, max_consecutive_rejects: 0,
    max_consecutive_rejects_night: 0,
  },
  escalation: { hfr_reject_factor: 0, hfr_reject_action: "warn" },
};

function seed(over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: wheelStatus(),
      equipConnected: true,
      wsPhase: "up",
      principal: OPERATOR,
      config: CONFIG,
      sequence: { state: "idle" },
      previews: [],
      livePreviewId: null,
      selectedPreviewId: null,
      filterOffsetsLearn: null,
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
  act(() => { rootRef!.render(createElement(WheelSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const text = () => (container.textContent || "") as string;
const byAria = (label: string) =>
  qa("[aria-label]").find((n: any) => n.getAttribute("aria-label") === label);
const namesPosts = () => asked.filter((a) => a.url.includes("/api/filterwheel/names"));
const movePosts = () => asked.filter((a) => a.url.includes("/api/filterwheel/position"));

// ====================================================== 1. the precondition
seed();
mount();

test("the sheet mounted: a ring of seven slots and a table of seven rows", () => {
  assert(q('[data-testid="rig-wheel"]') != null,
    "no sheet marker - the sheet did not render at all");
  const ring = q('[data-testid="wheel-ring"]');
  assert(ring != null, "no ring container");
  eq(ring.getAttribute("data-mode"), "ring",
    "a 7-slot wheel did not draw the ring");
  for (let i = 0; i < NAMES.length; i++) {
    assert(q(`[data-testid="wheel-slot-${i}"]`) != null, `no tap target for slot ${i}`);
    assert(q(`[data-testid="wheel-row-${i}"]`) != null, `no table row for slot ${i}`);
  }
  eq(qa('[data-testid^="wheel-slot-"]').length, 7, "wrong number of slot tap targets");
  eq(qa('[data-testid^="wheel-row-"]').length, 7, "wrong number of table rows");
  // The centre says what is in the light path, and the types are derived
  // (E21) rather than read off a wire field that does not exist.
  eq((q('[data-testid="wheel-current"]').textContent || "").trim(), "L",
    "the centre of the ring does not name the current filter");
  assert(/broadband/.test(text()) && /narrowband/.test(text()),
    "the derived slot types are missing from the table");
  assert(text().includes(RING_CAPTION), "the ring's caption is missing");
  // The footer names the REFERENCE slot rather than claiming "relative to L"
  // on a wheel whose reference is not L (E20).
  assert(/Offsets are steps relative to L and apply on every filter change/.test(text()),
    "the footer does not name the reference slot the offsets are measured against");
  eq(asked.length, 0,
    "the sheet asked the rig for something on mount - it renders from the status frame");
});

// ========================================================= 2. a tap is a move
await testAsync("tapping a slot moves the wheel to it", async () => {
  asked.length = 0;
  click(q('[data-testid="wheel-slot-4"]'));
  await settle();
  eq(movePosts().length, 1,
    `tapping Ha did not POST /api/filterwheel/position (asked ${JSON.stringify(asked)})`);
  eq(JSON.stringify(movePosts()[0].body), JSON.stringify({ position: 4 }),
    "the move did not carry the slot that was tapped");
  // The ring turns to the TARGET while the change is in flight: every backend
  // reports the OLD slot right up to the landing, which is what made the wheel
  // look like it had ignored the pick.
  eq((q('[data-testid="wheel-current"]').textContent || "").trim(), "Ha",
    "the ring still shows the outgoing filter after the tap was accepted");
});

test("tapping the slot the wheel is already on asks the rig for nothing", () => {
  seed();
  mount();
  asked.length = 0;
  click(q('[data-testid="wheel-slot-0"]'));
  eq(movePosts().length, 0,
    "a tap on the current slot commanded a move to where the wheel already is");
});

// ============================================ 3. the offset write, in full
await testAsync("an offset step writes names + that array, and nulls elsewhere", async () => {
  seed();
  mount();
  asked.length = 0;
  const plus = byAria("Slot 2 focus offset in steps up");
  assert(plus != null, "no offset stepper on slot 2");
  click(plus);
  await settle();
  // The value on screen moves at the press, before the debounce lets the write
  // out: a stepper that waited 400 ms to redraw would read as a dead button.
  const row = q('[data-testid="wheel-row-1"]');
  assert(/\+14/.test(row.textContent || ""),
    `slot 2's offset does not read +14 at the press (got ${JSON.stringify(row.textContent)})`);
  await flushWrites();

  eq(namesPosts().length, 1,
    `one offset press must be exactly one POST /api/filterwheel/names `
    + `(asked ${JSON.stringify(asked)})`);
  const body = namesPosts()[0].body;
  eq(namesPosts()[0].method, "POST", "the wheel config write must be a POST");
  eq(JSON.stringify(body.names), JSON.stringify(NAMES),
    "the body must carry the names the other arrays are parallel to");
  // R was 12; one press of the design's +-2 step makes it 14, and every other
  // slot is untouched.
  eq(JSON.stringify(body.offsets), JSON.stringify([0, 12 + OFFSET_STEP, -8, 4, 60, 55, 58]),
    "the offsets array is not the current one with the pressed slot changed by +2");
  for (const key of ["opaque", "narrowband", "exposures", "gains"]) {
    eq(body[key], null,
      `${key} was sent as ${JSON.stringify(body[key])} rather than null - an offset `
      + "edit must leave every other stored array alone, or it clobbers a "
      + "concurrent edit (and blackout flags are how darks are shot)");
  }
  // …and once the rig has answered, the sheet is back on the status frame's own
  // arrays rather than holding its optimistic copy for ever.
  assert(/\+12/.test((q('[data-testid="wheel-row-1"]').textContent || "")),
    "the optimistic offset outlived the write - the next status frame is the truth");
});

await testAsync("a held press is one POST, not one per repeat", async () => {
  seed();
  mount();
  asked.length = 0;
  const plus = byAria("Slot 2 focus offset in steps up");
  click(plus);
  click(plus);
  click(plus);
  await flushWrites();
  eq(namesPosts().length, 1,
    `three presses inside the debounce window sent ${namesPosts().length} writes`);
  eq(JSON.stringify(namesPosts()[0].body.offsets),
    JSON.stringify([0, 12 + 3 * OFFSET_STEP, -8, 4, 60, 55, 58]),
    "the one write that lands must carry the value the last press left on screen");
});

await testAsync("pinning a default sub writes exposures, and nothing else", async () => {
  seed();
  mount();
  asked.length = 0;
  click(q('[data-testid="wheel-sub-1"]'));
  await settle();
  const chip = qa("button").find((b: any) => (b.textContent || "").trim() === "180 s");
  assert(chip != null, "the default-sub picker did not open");
  click(chip);
  await flushWrites();
  eq(namesPosts().length, 1, "pinning a default sub did not write the wheel config");
  eq(JSON.stringify(namesPosts()[0].body.exposures),
    JSON.stringify([60, 180, 120, 120, 300, 300, 300]),
    "the exposures array is not the current one with the pinned slot changed");
  eq(namesPosts()[0].body.offsets, null,
    "a default-sub edit sent the offsets array, which it did not change");
});

// ==================================================== 4. a blackout slot
await testAsync("a blackout slot offers no offset, and says why", async () => {
  const opaque = [false, false, false, true, false, false, false];
  seed({ status: wheelStatus({ filterwheel: { opaque, names: NAMES, position: 0 } }) });
  mount();
  await settle();
  const standin = byAria("Slot 4 focuser offset - not applicable, blackout slot");
  assert(standin != null,
    "slot 4 is a blackout and its offset cell is not the em-dash stand-in with "
    + "the reason - an offset through a slot with no light path is not a measurement");
  eq((standin.textContent || "").trim(), "—",
    "the blackout stand-in is not an em dash");
  assert(byAria("Slot 4 focus offset in steps") == null,
    "a blackout slot still has an offset stepper");
  const row = q('[data-testid="wheel-row-3"]');
  assert(/blackout/.test(row.textContent || ""),
    "the blackout slot's derived type word is missing");
  // …and the slot is still tappable: parking on a blackout slot is how darks
  // are shot through a wheel (`captureFilterDial.ts`'s own reason).
  const tap = q('[data-testid="wheel-slot-3"]');
  assert(tap != null && tap.getAttribute("aria-disabled") == null,
    "the blackout slot cannot be moved to - that is how darks are shot");
});

// ====================================================== 5. the viewer's screen
await testAsync("a viewer sees the wheel, told why, and pressing it asks nothing",
  async () => {
    seed({ principal: VIEWER });
    mount();
    await settle();
    const slot = q('[data-testid="wheel-slot-4"]');
    assert(slot != null, "a viewer cannot see the ring at all - the screen was replaced");
    eq(slot.getAttribute("aria-disabled"), "true", "a viewer's slot tap is live");
    assert(/needs/.test(slot.getAttribute("title") || ""),
      `the viewer is not told why the wheel is locked (got ${JSON.stringify(slot.getAttribute("title"))})`);
    assert(slot.hasAttribute("disabled") === false,
      "the slot uses the native disabled attribute, which takes the reason out of "
      + "the accessibility tree");

    asked.length = 0;
    click(slot);
    const plus = byAria("Slot 2 focus offset in steps up");
    if (plus) click(plus);
    await flushWrites();
    eq(asked.length, 0, `a viewer's presses reached the rig: ${JSON.stringify(asked)}`);
    const toasts = (useStore.getState() as any).toasts as { title?: string }[];
    assert(toasts.length >= 1,
      "a locked press was silent - the reason must be stated, not merely withheld");
  });

// ============================================= the list fallback above 12
await testAsync("a 13-slot wheel drops the ring and keeps every slot reachable",
  async () => {
    const names = ["L", "R", "G", "B", "Ha", "OIII", "SII", "U", "V", "I", "Z", "Y", "DARK"];
    seed({
      status: wheelStatus({
        filterwheel: {
          names, position: 0, offsets: new Array(13).fill(0),
          opaque: new Array(13).fill(false), narrowband: new Array(13).fill(false),
          exposures: new Array(13).fill(null), gains: new Array(13).fill(null),
        },
      }),
    });
    mount();
    await settle();
    eq(q('[data-testid="wheel-ring"]').getAttribute("data-mode"), "list",
      "a 13-slot wheel still drew a ring whose labels cannot be read");
    eq(qa('[data-testid^="wheel-row-"]').length, 13,
      "the table lost slots when the ring was dropped");
    assert(q('[data-testid="wheel-slot-12"]') != null,
      "the thirteenth slot is unreachable in the list fallback");
  });

// ============== polar alignment owns the camera on this sheet too (r4 #25)
// LEARN OFFSETS steps the focuser through every filter, exposing at each stop.
// Polar alignment holds the camera for its whole run and spawns its own lane
// (server/astrodeck/hub.py:297), which this sheet never named - so the run
// started, collided, and came back as a raw 409.
await testAsync("LEARN OFFSETS is honest-disabled while an alignment owns the camera",
  async () => {
    const { POLAR_REASON } = await import("../capture/captureGate");
    const learnButton = () =>
      qa("button").find((b: any) => (b.textContent || "").trim() === "LEARN OFFSETS");

    seed();
    mount();
    await settle();
    const before = learnButton();
    assert(before != null, "precondition: LEARN OFFSETS is not on the sheet");
    eq(before.getAttribute("aria-disabled"), null,
      "precondition: LEARN OFFSETS was already locked with no alignment running");

    seed({ status: wheelStatus({ busy_lanes: ["polar"] }) });
    mount();
    await settle();
    const btn = learnButton();
    assert(btn != null, "LEARN OFFSETS was hidden during an alignment instead of locked");
    eq(btn.getAttribute("aria-disabled"), "true",
      "LEARN OFFSETS pressed through a polar alignment - the rig answers that with a raw 409");
    eq(btn.getAttribute("title"), POLAR_REASON,
      "LEARN OFFSETS is locked but does not name the alignment");
    assert(btn.hasAttribute("disabled") === false,
      "LEARN OFFSETS used the native disabled attribute, which takes the reason out of the "
      + "accessibility tree");

    asked.length = 0;
    click(btn);
    await settle();
    eq(asked.length, 0, `the press reached the rig anyway: ${JSON.stringify(asked)}`);
  });

act(() => { if (rootRef) rootRef.unmount(); });

const total = passed + failed;
console.log(`rigWheel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
