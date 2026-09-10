// rigGuiderDom.test.tsx - the GUIDER sheet, MOUNTED (plan hub-rig.md D.2,
// T-RIG-5 row).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigGuiderDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH ASSERTING HERE. The sheet is mostly other people's components,
// so the tests are about the five decisions this sheet actually makes, each of
// which is a defect that has been shipped at least once somewhere:
//
//   1. THE DIAL WRITES BOTH AXES. `aggression` and `min_move` are per-axis on
//      the wire (`ra_params` / `dec_params`). One dial writing one axis leaves
//      the mount corrected differently in RA and Dec than the number on screen
//      claims.
//   2. THE PUT IS A WHOLE-BLOCK REPLACE. `PUT /api/guide/settings` takes a
//      pydantic `GuideConfig`, so any field the body omits is reset to a model
//      default - which is how a tuning save could silently zero the dither
//      distance and reset the guide camera's exposure. The test reads the body
//      and insists the untouched fields are still in it.
//   3. STOP GUIDING IS NEVER GATED ON A BUSY LANE. It is the escape hatch for
//      the lane it ends.
//   4. PPEC IS RA-ONLY. `validateGuideSettings` THROWS on PPEC for Dec, so a Dec
//      picker that offered it would hand the user a save that cannot succeed.
//   5. A VIEWER SEES THE WHOLE SHEET AND CHANGES NOTHING. Dimmed, aria-disabled,
//      focusable, with the reason - and no command reaches the rig.
//
// WHAT THIS CANNOT DO: jsdom lays nothing out and decodes no images, so the
// trace SVG measures 0x0 and the guide-camera preview never loads a frame. What
// is real is which node carries which attribute and which request was made.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/guider", pretendToBeVisual: true },
);
const win = dom.window as any;

let tabletWidth = false;
win.matchMedia = (q: string) => ({
  matches: tabletWidth && /min-width:\s*768px/.test(q),
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "Image", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
/** The rig's stored guide block. Deliberately carries fields this sheet does NOT
 *  render (`recover_guiding`, `exposure_s`, `dither_pixels`, `offset`): they are
 *  what the whole-block-replace assertion is about. */
const storedGuide: Record<string, unknown> = {
  ra_algorithm: "hysteresis",
  dec_algorithm: "resist_switch",
  dec_guide_mode: "auto",
  blc_pulse_ms: 0,
  ra_params: { min_move: 0.2, hysteresis: 0.1, aggression: 0.7 },
  dec_params: { min_move: 0.2, aggression: 1.0 },
  dither_pixels: 3.0,
  recover_guiding: true,
  exposure_s: 2.0,
  gain: 100,
  binning: 1,
  offset: 30,
  recalibrate_after_pier_change: true,
};

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: u, body });
  const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/guide/settings")) return ok(method === "PUT" ? body : storedGuide);
  if (u.includes("/api/guide/calibration")) {
    if (method === "DELETE") return ok({ cleared: true });
    return ok({ report: {
      is_valid: true, ortho_error_deg: 2.4, declination_deg: 41, pier_side: "east",
      binning: 1, advisories: [], source: "native",
    } });
  }
  if (u.includes("/api/guide/assistant/report")) return ok({ report: null });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { GuiderSheet } = await import("../sheets/guider");
const model = await import("../lib/guiderModel");

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
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount", "control.power"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function guideStats(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    guiding: true, rms_ra: 0.41, rms_dec: 0.46, rms_total: 0.62, snr: 48,
    recent: Array.from({ length: 24 }, (_, i) => ({ t: i, ra: 0.2, dec: -0.1 })),
    is_arcsec: true, image_scale: 1.6, phase: "guiding",
    ...over,
  };
}

function seed(over: Record<string, unknown> = {}, statusOver: Record<string, unknown> = {}): void {
  const base = useStore.getState();
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      wsPhase: "up",
      equipConnected: true,
      guide: null,
      guideAssistant: null,
      guideRmsByKind: {},
      toasts: [],
      plan: { name: "tonight", targets: [], guide: true, dither_every: 3 },
      frameSettings: {
        ...base.frameSettings,
        guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
      },
      config: { active_profile_id: null, providers: { guide: "auto" } },
      status: {
        connected: { guider: { name: "ZWO ASI120MM", connected: true } },
        backend_links: [{ role: "guider", connected: true, error: null }],
        busy_lanes: [],
        guider: { ...guideStats(), name: "AstroDeck native" },
        guide_camera: { name: "ZWO ASI120MM", connected: true, preview_ok: true, preview_source: "ZWO ASI120MM" },
        providers: { guide: { kind: "astrodeck", label: "AstroDeck native", reason: "native guider", options: [], eligible: [] } },
        ...statusOver,
      },
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(GuiderSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => [...container.querySelectorAll(sel)] as any[];
const text = () => (container.textContent || "") as string;
const puts = () => asked.filter((a) => a.method === "PUT" && a.url.includes("/api/guide/settings"));
const commands = () => asked.filter((a) => a.method !== "GET");

// ============================================================ precondition
seed();
mount();
await settle();

test("the sheet mounted, with the trace, the four tiles and the primary action on it", () => {
  // The anti-blank-page guard (astrodeck-ui-probe-traps: a broken probe reads as
  // a passing app). Every assertion below is about ONE control; "no control has
  // the wrong attribute" is trivially true of a sheet that rendered nothing.
  assert(q('[data-testid="rig-guider"]') != null, "no sheet marker - the sheet did not render at all");
  assert(q('[data-testid="guider-trace"]') != null, "no trace card");
  eq(qa('[data-testid="guider-tiles"] .nx-readout').length, 4,
    "the readout grid is not showing four tiles");
  assert(q('[data-testid="guider-primary"]') != null, "no primary action button");
  assert(q('[data-testid="guider-assistant"]') != null, "the guiding assistant task is missing (GAP-5)");
  assert(q('[data-testid="guider-provider"]') != null, "the provider row is missing (GAP-5)");
  assert(q('[data-testid="guider-dither-px"]') != null, "the dither-pixels stepper is missing");
  assert(q('[data-testid="guider-clear-calibration"]') != null, "CLEAR CALIBRATION is missing (GAP-5)");
  assert(q('[data-testid="guider-offset"]') != null, "the guide-scope offset panel is missing");
});

test("the header's live line carries numbers the body does not repeat", () => {
  const live = q(".nx-sheet-live");
  assert(live != null, "the sheet header has no live line");
  const s = (live.textContent || "");
  assert(/RMS/.test(s) && /star SNR 48/.test(s),
    `the live line is a caption, not state: ${JSON.stringify(s)}`);
});

test("SCATTER is a second tab on the same trace, not a second card", () => {
  assert(/DEC/.test(text()), "precondition: the trace legend is not on screen");
  const tab = qa('[data-testid="guider-tabs"] button')
    .find((b: any) => /SCATTER/.test(b.textContent || ""));
  assert(tab != null, "no SCATTER tab on the trace card");
  click(tab);
  assert(!/NO GUIDE DATA YET/.test(text()), "the scatter tab is rendering the empty graph");
  assert(q('svg[viewBox="0 0 140 140"]') != null, "the SCATTER tab did not render the scatter plot");
  const back = qa('[data-testid="guider-tabs"] button')
    .find((b: any) => /TRACE/.test(b.textContent || ""));
  click(back);
});

// ==================================================== the dial writes BOTH axes
await testAsync("the AGGRESSION dial writes ra_params AND dec_params, through the validator", async () => {
  const tile = q('[data-testid="tile-aggression"]');
  assert(tile != null, "no AGGRESSION tile");
  click(tile);
  const stop = q('[data-testid="guider-dial"] .nx-dial-stop[data-value="85"]');
  assert(stop != null, "the dial is not offering the 85% stop under the AGGRESSION tile");
  const before = puts().length;
  click(stop);
  await settle();
  const wrote = puts();
  eq(wrote.length, before + 1, "moving the dial did not PUT /api/guide/settings");
  const body = wrote[wrote.length - 1].body;
  eq(body.ra_params.aggression, 0.85, "the RA axis did not get the dialled aggression");
  eq(body.dec_params.aggression, 0.85,
    "the Dec axis did not get the dialled aggression - one dial has to write BOTH axes");
  // snake_case at the PUT boundary only: the client's own state stays camelCase.
  assert(!("minMove" in body.ra_params),
    `the body carries camelCase keys the server does not know: ${JSON.stringify(body.ra_params)}`);
  eq(body.ra_params.min_move, 0.2, "min_move was dropped or renamed on the way out");
});

test("the note says the dial sets both axes and where the per-axis values live", () => {
  const note = q('[data-testid="guider-both-axes"]');
  assert(note != null, "no note under the dial - one number silently writing two axes is unexplained");
  eq((note.textContent || "").trim(), model.BOTH_AXES_NOTE, "the both-axes note is hand-written");
});

test("the PUT is a WHOLE-BLOCK replace: fields this sheet never renders survive it", () => {
  // PUT /api/guide/settings takes a pydantic GuideConfig, so an omitted field is
  // not "unchanged", it is reset to a model default. A body missing these would
  // zero the dither distance and reset the guide camera's own exposure.
  const body = puts()[puts().length - 1].body;
  eq(body.dither_pixels, 3.0, "the dither distance was dropped from the PUT body");
  eq(body.recover_guiding, true, "recover_guiding was dropped from the PUT body");
  eq(body.exposure_s, 2.0, "the guide camera's exposure was dropped from the PUT body");
  eq(body.recalibrate_after_pier_change, true,
    "recalibrate_after_pier_change was dropped from the PUT body");
});

await testAsync("MIN MOVE is dialled in guide-camera pixels, never stamped with a prime", async () => {
  click(q('[data-testid="tile-minmove"]'));
  const tile = q('[data-testid="tile-minmove"]');
  assert(/px/.test(tile.textContent || ""),
    `MIN MOVE is not labelled in pixels: ${JSON.stringify(tile.textContent)}`);
  assert(!/″/.test(tile.textContent || ""),
    "MIN MOVE is labelled arcsec - it is a pixel threshold (phd2-guiding.md:744)");
  const stop = q('[data-testid="guider-dial"] .nx-dial-stop[data-value="0.3"]');
  assert(stop != null, "the dial is not offering the 0.3 px stop under MIN MOVE");
  const before = puts().length;
  click(stop);
  await settle();
  const body = puts()[puts().length - 1].body;
  eq(puts().length, before + 1, "the MIN MOVE dial did not write");
  eq(body.ra_params.min_move, 0.3, "the RA axis did not get the dialled min move");
  eq(body.dec_params.min_move, 0.3, "the Dec axis did not get the dialled min move");
});

// ==================================================================== dither
await testAsync("the dither distance is rig-level and rides the same whole-block PUT", async () => {
  // Deviation E14: the CADENCE is per-night (SequencePlan.dither_every) and the
  // DISTANCE is rig-level (config.guide.dither_pixels), so the sheet steps the
  // distance and only READS the cadence.
  const cadence = q('[data-testid="guider-dither-cadence"]');
  assert(cadence != null, "no per-plan dither cadence row");
  assert(/every 3 subs/.test(cadence.textContent || ""),
    `the cadence row does not read the plan: ${JSON.stringify(cadence.textContent)}`);
  const before = puts().length;
  click(q('[data-testid="guider-dither-px"] [aria-label="Dither pixels up"]'));
  await settle();
  eq(puts().length, before + 1, "the dither stepper did not write");
  const body = puts()[puts().length - 1].body;
  eq(body.dither_pixels, 3.5, "the dither distance did not step by 0.5 px");
  eq(body.ra_algorithm, "hysteresis",
    "the dither write dropped the tuning fields - the PUT replaces the whole block");
});

await testAsync("DITHER NOW sends only the settle overrides the user actually set", async () => {
  // Blank is NOT zero: an empty settle field means "the guider's own default",
  // and sending 0 would be an instruction the guider obeys.
  asked.length = 0;
  click(q('[data-testid="guider-dither-now"]'));
  await settle();
  let sent = asked.filter((a) => a.url.includes("/api/guide/dither"));
  eq(sent.length, 1, "DITHER NOW did not POST /api/guide/dither");
  eq(sent[0].body.settle_pixels, undefined,
    "a blank settle field was sent as a value the guider would obey");
  const px = q('[data-testid="guider-settle-px"]');
  assert(px != null, "the settle-pixels override has no home on this sheet");
  act(() => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(px, "1.5");
    px.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  asked.length = 0;
  click(q('[data-testid="guider-dither-now"]'));
  await settle();
  sent = asked.filter((a) => a.url.includes("/api/guide/dither"));
  eq(sent[0].body.settle_pixels, 1.5, "the settle-pixels override did not ride with the dither");
});

// ================================================== calibration + the assistant
await testAsync("CLEAR CALIBRATION deletes the PERSISTED copy and says which copy went", async () => {
  const btn = q('[data-testid="guider-clear-calibration"]');
  assert(btn.getAttribute("aria-disabled") == null, "CLEAR CALIBRATION is locked for an operator");
  click(btn);
  await settle();
  const del = asked.filter((a) => a.method === "DELETE" && a.url.includes("/api/guide/calibration"));
  eq(del.length, 1, "CLEAR CALIBRATION did not DELETE /api/guide/calibration");
  // The GET reads the ENGINE's live calibration and the DELETE only unlinks the
  // persisted file, so the panel correctly stays green - and has to say so.
  const note = q('[data-testid="guider-saved-cleared"]');
  assert(note != null,
    "nothing explains why the calibration readout is unchanged after a clear");
  assert(/saved copy was cleared/i.test(note.textContent || ""),
    `the note does not name which copy went: ${JSON.stringify(note.textContent)}`);
});

await testAsync("RUN GUIDING ASSISTANT fires its own start path, with the backlash choice", async () => {
  // The assistant needs the mount to itself, so it is only offered while nothing
  // is guiding. Re-seed idle rather than assert against a state it refuses.
  seed({}, { guider: { ...guideStats({ guiding: false, phase: "idle" }), name: "AstroDeck native" } });
  mount();
  await settle();
  const btn = q('[data-testid="guider-assistant-start"]');
  assert(btn != null, "no RUN GUIDING ASSISTANT button");
  eq(btn.getAttribute("aria-disabled"), null,
    `the assistant is locked for an operator on a native guider: ${btn.getAttribute("title")}`);
  click(btn);
  await settle();
  const started = asked.filter((a) => a.url.includes("/api/guide/assistant/start"));
  eq(started.length, 1, "pressing RUN GUIDING ASSISTANT did not POST /api/guide/assistant/start");
  eq(started[0].body.include_backlash, true,
    "the backlash switch's value did not ride with the start");
});

// ============================================== the never-blocked STOP GUIDING
test("STOP GUIDING is never aria-disabled, even with the guide lane busy", () => {
  // Plan section C's never-blocked list. Gating Stop on the lane it exists to
  // end is the exact bug the inventory records three separate times.
  seed({}, {
    busy_lanes: ["guide", "capture"],
    guider: { ...guideStats({ guiding: true, phase: "guiding" }), name: "AstroDeck native" },
  });
  mount();
  const btn = q('[data-testid="guider-primary"]');
  assert(/STOP GUIDING/.test(btn.textContent || ""),
    `the primary action is not showing the STOP face while guiding: ${JSON.stringify(btn.textContent)}`);
  eq(btn.getAttribute("aria-disabled"), null,
    `STOP GUIDING is locked while the guide lane is busy: ${btn.getAttribute("title")}`);
});

test("a native guider's uninterruptible calibration walk is a REASON, not silence", () => {
  // The one state where Stop really is refused: `stop_guiding` sets a flag the
  // walk never checks, so the press would be swallowed. It says so.
  seed({}, {
    busy_lanes: ["guide"],
    guider: { ...guideStats({ guiding: false, phase: "calibrating" }), name: "AstroDeck native" },
  });
  mount();
  const btn = q('[data-testid="guider-primary"]');
  eq(btn.getAttribute("aria-disabled"), "true",
    "Stop claims to work during a native calibration walk, where the press is swallowed");
  assert(/cannot be interrupted/.test(btn.getAttribute("title") || ""),
    `the refusal does not say why: ${btn.getAttribute("title")}`);
});

// ==================================================== PPEC is RA-only
await testAsync("the Dec algorithm picker does not offer PPEC", async () => {
  seed();
  mount();
  // The pickers only become <select>s once the GET has landed: before that every
  // tuning control is honest-disabled behind `saveReason`, because a PUT built
  // from factory defaults would replace a tuned axis with them.
  await settle();
  const ra = q('[data-testid="guider-ra-algorithm"]');
  const dec = q('[data-testid="guider-dec-algorithm"]');
  assert(ra != null && dec != null, "the algorithm pickers are missing from the phone tuning rows");
  assert([...ra.querySelectorAll("option")].some((o: any) => o.value === "ppec"),
    "the RA picker does not offer Predictive PEC");
  assert(![...dec.querySelectorAll("option")].some((o: any) => o.value === "ppec"),
    "the Dec picker offers PPEC - validateGuideSettings THROWS on it, so that save can never land");
  assert(q('[data-testid="guider-ppec"]') != null, "no PREDICTIVE PEC switch on the phone (GAP-5)");
});

await testAsync("the full tuning drawer is tablet-and-desktop only", async () => {
  // GAP-5: "Defer the full editor to tablet; expose algorithm + PEC toggles on
  // the phone." The phone must not grow a param editor it has no room for.
  assert(q('[data-testid="guider-blc"]') == null,
    "the backlash-pulse field is on the phone layout");
  assert(q('[data-testid="guider-tuning-save"]') == null,
    "the tuning SAVE button is on the phone layout");
  tabletWidth = true;
  mount();
  await settle();
  assert(q('[data-testid="guider-blc"]') != null, "the tablet drawer has no backlash-pulse field");
  assert(q('[data-testid="guider-dec-mode"]') != null, "the tablet drawer has no Dec guide direction");
  assert(q('[data-testid="guider-tuning-save"]') != null, "the tablet drawer has no SAVE");
  tabletWidth = false;
});

// ==================================================================== viewer
await testAsync("a viewer sees the whole sheet, changes nothing, and is told why", () => {
  // Seeded IDLE so the primary action shows its start face: what a viewer sees
  // is decided by the rig's state, and only its reachability by the role.
  seed({ principal: VIEWER }, {
    guider: { ...guideStats({ guiding: false, phase: "idle" }), name: "AstroDeck native" },
  });
  mount();
  return settle().then(() => {
    asked.length = 0;
    const primary = q('[data-testid="guider-primary"]');
    assert(primary != null, "the primary action is HIDDEN from a viewer - nothing may be hidden");
    assert(/LOOP \+ PICK STAR/.test(primary.textContent || ""),
      "a viewer is not seeing the idle face of the primary action");
    eq(primary.getAttribute("aria-disabled"), "true", "LOOP + PICK STAR is live for a viewer");
    assert(/needs .*access/.test(primary.getAttribute("title") || ""),
      `the lock carries no capability sentence: ${primary.getAttribute("title")}`);
    // Focusable, not removed from the tree: the reason has to be reachable.
    eq(primary.hasAttribute("disabled"), false,
      "the native disabled attribute is used - that strips the control and its reason from the a11y tree");
    click(primary);
    click(q('[data-testid="guider-clear-calibration"]'));
    click(q('[data-testid="guider-dither-now"]'));
    eq(commands().length, 0,
      `a viewer's presses reached the rig: ${JSON.stringify(commands().map((a) => `${a.method} ${a.url}`))}`);
    assert(useStore.getState().toasts.length > 0,
      "a locked press was silent - it must state the reason");
  });
});

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`rigGuider.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
