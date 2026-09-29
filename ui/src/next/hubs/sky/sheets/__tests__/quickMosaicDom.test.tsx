// quickMosaicDom.test.tsx - what the quick sheet does with a framing that was
// kept, MOUNTED and pressed (review #3, #34, the quick-defaults loop of #4, and
// S6's converged doors: #196, #154's door half, spec 2026-09-23 flows mosaic
// section 8 S6).
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/quickMosaicDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// THE DEFECT THIS FILE WAS BUILT FOR, AND WHAT S6 DID TO IT. FRAME mode drew a
// mosaic, kept the engine's panel centres on `store.framing.panels`, and
// nothing read them (review #3). The fix wired them into the classic Plan as
// targets sharing a `mosaic_group` beside a one-target flow: the Plan side
// channel, shot panel-first (#154). S6 deleted that channel: a mosaic reaches a
// night through Send to Flow Wizard, as ONE TARGET block. So this file now
// holds the opposite of what it once did, and grades it the same way, on the
// store and on the requests:
//
//   1. GENERATE FLOW WRITES NO PLAN TARGET, for a kept mosaic or anything
//      else: the store's Plan (which holds a hand-built target, so unchanged
//      is not empty) is byte-for-byte what it was. This is the SABOTAGE
//      TARGET: leave the side channel wired and this file goes red.
//   2. A KEPT MOSAIC IS OFFERED THE WIZARD. The note says GENERATE FLOW plans
//      one target and SEND TO FLOW WIZARD plans the grid, and the button opens
//      the wizard with this framing in the route.
//   3. THE FLOW CARD IS TOLD NO GRID, and draws no synthetic MOSAIC card even
//      for a link from before S6 that still carries one.
//   4. A FRAMING FOR ANOTHER TARGET IS NOT THIS TARGET'S (the framing slice is
//      global and shared with the Atlas).
//   5. THE FLOW'S OWN TARGET NODE carries the framed angle, not the node
//      vocabulary's shipped 23.4.
//   6. ONE HORIZON. The arc's dashed floor is the site's limit.
//
// MUTATION RECORD, 2026-09-28, each run in a private scratch copy of ui/
// (scratchpad/S6-DOORS-mut in the session scratchpad, never the shared tree,
// #254). Output verbatim.
//
//   MUTANT "quick side channel left wired" (quick.tsx generate: a kept mosaic's
//   panels appended to the Plan again before the flow opens). Observed
//   ("quickMosaicDom.test: 12/13 passed"):
//     x GENERATE FLOW puts NOTHING in the Plan, and posts one target: the store's Plan changed: the Plan side channel is still wired (expected true, got false)
//
//   MUTANT "quick still tells the flow card the grid" (nav.sheet("flow") given
//   `mosaic: "${cols}x${rows}"` again). Observed ("quickMosaicDom.test: 12/13 passed"):
//     x the flow card is told no grid in the hash: the flow card was told a grid, for a synthetic card that no longer exists: "#/sky/quick/flow?id=f1&mosaic=2x2" (expected undefined, got 2x2)
//
//   MUTANT "synthetic MOSAIC card restored" (flowLane.ts: the pass-through given
//   its old body back, inserting the MOSAIC card after TARGET). Observed
//   ("quickMosaicDom.test: 12/13 passed"):
//     x a link from before S6 carrying a grid gets the saved graph's lane and no MOSAIC card: the flow card still draws the synthetic MOSAIC card: ["TARGET","MOSAIC 2×2"]
//
//   MUTANT "the quick sheet's SEND TO FLOW WIZARD opens nothing" (its onPress
//   a no-op). Observed ("quickMosaicDom.test: 12/13 passed"):
//     x SEND TO FLOW WIZARD opens the wizard with this framing in the route, and writes no Plan target: the wizard did not open over the quick sheet: "#/sky/quick?target=m31" (expected quick/flowWizard, got quick)

/* eslint-disable @typescript-eslint/no-explicit-any */

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
  "Element", "SVGElement", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

win.localStorage.setItem("astrodeck-next-sky-quick", JSON.stringify({ hours: 6 }));

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
const M31 = {
  id: "m31", name: "Andromeda Galaxy", type: "Galaxy", kind: "dso",
  ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190,
  difficulty: "easy",
};

/** The graph the server hands back: one TARGET node carrying the node
 *  vocabulary's SHIPPED rotation, which is the fixture value nothing replaced. */
const targetNode = () => ({
  id: "n3", type: "target", x: 0, y: 0,
  params: { name: "M31 - Andromeda", ra: "00h 42m 44s", dec: "+41° 16′ 09″", rotation: 23.4 },
});
const flowRecord = () => ({
  id: "f1", name: "Quick session: Andromeda Galaxy", folder: "My flows",
  tagline: "", graph: { nodes: [targetNode()], edges: [] },
  created_ts: 0, updated_ts: 0, last_run: null, last_result: "", readonly: false,
});

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });
  const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
  if (u.includes("/api/catalog?q=")) return ok({ results: [M31], notes: [] });
  if (u.includes("/api/visibility")) return ok({});
  if (u.includes("/api/flows/quick")) return ok({ flow: flowRecord(), started: false });
  if (u.includes("/api/flows/compile")) return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  if (u.includes("/api/flows/f1")) return ok(flowRecord());
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { QuickSessionSheet } = await import("../quick");
const { FlowCardSheet } = await import("../flow");
const { arcY } = await import("../quickNightArc");
const { SEND_TO_WIZARD } = await import("../quickCopy");
const { parseHash } = await import("../../../../router");
const { DEFAULT_OVERLAP } = await import("../../../../../lib/framing");
const { raHms, decDms } = await import("../../../session/flows/create/quickPayload");

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
function near(got: number, want: number, tol: number, msg: string): void {
  if (!(Math.abs(got - want) <= tol)) {
    throw new Error(`${msg} (expected ${want} +/- ${tol}, got ${got})`);
  }
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const q = (sel: string) => container.querySelector(sel) as any;
const route = () => parseHash(String(win.location.hash));

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount", "control.guide"],
};

/** The panel centres the ENGINE returned, as FRAME mode kept them. */
const PANELS = [
  { row: 0, col: 0, ra_hours: 0.66, dec_deg: 41.7, rotation_deg: 30 },
  { row: 0, col: 1, ra_hours: 0.76, dec_deg: 41.7, rotation_deg: 30 },
  { row: 1, col: 0, ra_hours: 0.66, dec_deg: 40.8, rotation_deg: 30 },
  { row: 1, col: 1, ra_hours: 0.76, dec_deg: 40.8, rotation_deg: 30 },
];

/** The framing's centre, dragged off the catalogue position, so a prefill
 *  that took the catalogue's would show. */
const CENTRE = { ra_hours: 0.7208333, dec_deg: 41.5 };

function framingFor(id: string, name: string) {
  return {
    target: { id, name, type: "Galaxy", ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190 },
    center: CENTRE,
    rotation_deg: 30,
    survey: "CDS/P/DSS2/color",
    stretch: "linear",
    fovZoomDeg: 2,
    mosaic: { rows: 2, cols: 2, overlap: DEFAULT_OVERLAP },
    panels: PANELS,
  };
}

/** A Plan that already holds a hand-built target, so "unchanged" means the
 *  same target list and not merely an empty one. */
const HAND_BUILT = [{
  id: "hand-1", name: "NGC 7000", ra_hours: 20.98, dec_deg: 44.3, center: true,
  autofocus_first: true, calibration: false,
  steps: [{ id: "s1", filter: null, exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 12, frame_type: "light" }],
}];
const planNow = () => JSON.stringify(useStore.getState().plan);

function seed(framing: unknown, horizonMinDeg = 30): void {
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      equipConnected: true,
      wsPhase: "up",
      site: { name: "Back lawn", latitude: 47.6, longitude: -122.3, elevation_m: 50, is_default: false, horizon_min_deg: horizonMinDeg },
      status: {
        connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim" } },
        filterwheel: {
          position: 0,
          names: ["L", "R", "G", "B"],
          opaque: [false, false, false, false],
          narrowband: [false, false, false, false],
          exposures: [60, 60, 60, 60],
        },
      },
      config: {
        optics: { focal_length_mm: 1000, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176 },
      },
      weather: null,
      framing,
      frameSettings: {
        capture: { exposure_s: 60, gain: 100, offset: 30, binning: 1, filter: null },
        focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
        solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
        guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
      },
    } as never);
    // Through the store's own action, so the plan keeps every field a real one
    // has, with a hand-built target already in it.
    useStore.getState().setPlan({ ...useStore.getState().plan, targets: HAND_BUILT } as never, false);
  });
}

const press = async (testid: string): Promise<void> => {
  const el = q(`[data-testid="${testid}"]`);
  assert(el != null, `no ${testid} to press - the fixture is wrong, not the component`);
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};

// ============================== 1. a kept mosaic for THIS target, and GENERATE
seed(framingFor("m31", "Andromeda Galaxy"));
const PLAN0 = planNow();
let root = createRoot(container);
await act(async () => {
  root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
});
await settle();

test("precondition: the sheet is up and knows it is holding a kept mosaic", () => {
  assert(q('[data-testid="sky-quick"]') != null,
    "no sky-quick marker: the fixture is wrong, not the component");
  const note = q('[data-testid="quick-mosaic-note"]');
  assert(note != null, "a kept 2x2 framing produced no word about where it goes");
  assert(/4 panels/.test(note.textContent), `the note must name the count: "${note.textContent}"`);
  assert(note.textContent.includes(SEND_TO_WIZARD), `the note must name the wizard: "${note.textContent}"`);
  assert(!/plan targets|mosaic group/.test(note.textContent),
    `the note still describes the retired Plan side channel: "${note.textContent}"`);
});

test("the CTA counts no panels: GENERATE FLOW plans one target", () => {
  const cta = q('[data-testid="quick-generate"]');
  assert(!/panels/.test(cta.textContent),
    `the CTA counts panels GENERATE FLOW will not shoot: "${cta.textContent}"`);
});

await testAsync("GENERATE FLOW puts NOTHING in the Plan, and posts one target", async () => {
  eq(useStore.getState().plan.targets.length, 1, "precondition: the Plan holds its hand-built target");
  await press("quick-generate");
  const quick = asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/quick"));
  eq(quick.length, 1, "GENERATE FLOW did not post the quick route exactly once:");
  assert(quick[0].body?.target && typeof quick[0].body.target.name === "string",
    "the quick route was not posted one target");
  eq(planNow() === PLAN0, true, "the store's Plan changed: the Plan side channel is still wired");
  // Read loosely: the integration deleted the slice with PlanEditor.tsx's
  // banner (#461), and this check outlives it: a door that raised the
  // banner again under that name would still be caught here.
  eq((useStore.getState() as any).atlasBannerPending ?? null, null, "the retired Plan banner was raised");
});

await testAsync("the flow's own TARGET node carries the framed angle, not the fixture 23.4", async () => {
  // `flowsApi.save` PUTs `{ flow }` to /api/flows/{id}.
  const saves = asks.filter((a) => a.method === "PUT" && /\/api\/flows\/f1(\?|$)/.test(a.url));
  assert(saves.length > 0, "the graph was never saved back, so the angle never left the phone");
  const node = saves[saves.length - 1].body?.flow?.graph?.nodes?.find((n: any) => n.type === "target");
  assert(node != null, "no TARGET node in the saved graph");
  eq(node.params.rotation, 30,
    "the shipped 23.4 survived: every quick flow would command a fixture position angle");
});

await testAsync("the flow card is told no grid in the hash", async () => {
  const r = route();
  eq(r.sheets[r.sheets.length - 1], "flow", `GENERATE FLOW did not open the flow card: "${win.location.hash}"`);
  eq(r.params.mosaic, undefined,
    `the flow card was told a grid, for a synthetic card that no longer exists: "${win.location.hash}"`);
});

act(() => { root.unmount(); });

// ============================== 2. the kept mosaic's way forward: the wizard
{
  seed(framingFor("m31", "Andromeda Galaxy"));
  const plan0 = planNow();
  win.location.hash = "#/sky/quick?target=m31";
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
  });
  await settle();

  await testAsync("SEND TO FLOW WIZARD opens the wizard with this framing in the route, and writes no Plan target", async () => {
    const b = q('[data-testid="quick-send-to-wizard"]');
    assert(b != null, "a kept mosaic is not offered SEND TO FLOW WIZARD");
    eq(b.textContent, SEND_TO_WIZARD, "the button's label:");
    await press("quick-send-to-wizard");
    const r = route();
    eq(r.sheets.join("/"), "quick/flowWizard", `the wizard did not open over the quick sheet: "${win.location.hash}"`);
    eq(r.params.target, "m31", "the quick sheet's own params did not survive the opening");
    // The framing, as the wizard's prefill: the dragged CENTRE, not the
    // catalogue's; the commanded PA; the grid; the one overlap; the rig's field.
    eq(r.params.wz_name, "Andromeda Galaxy", "the framing's name:");
    eq(r.params.wz_ra, raHms(CENTRE.ra_hours), "the framing's RA:");
    eq(r.params.wz_dec, decDms(CENTRE.dec_deg), "the framing's Dec:");
    eq(r.params.wz_pa, "30", "the framing's PA:");
    eq(`${r.params.wz_cols}x${r.params.wz_rows}`, "2x2", "the framing's grid:");
    eq(r.params.wz_overlap, String(DEFAULT_OVERLAP * 100), "the framing's overlap:");
    eq(r.params.wz_angle, undefined, "an angle MODE was sent, but a framing holds none");
    eq(r.params.wz_skip, undefined, "a skip was sent, but the Sky cannot skip a panel");
    near(Number(r.params.wz_fovx), 1.346, 0.001, "the rig's field (x):");
    eq(planNow() === plan0, true, "opening the wizard changed the Plan");
    eq(asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/wizard")).length, 0,
      "the button generated a flow itself instead of opening the wizard");
  });

  act(() => { root.unmount(); });
}

// ==================== 3. a framing kept for ANOTHER target is not this target's
{
  seed(framingFor("m42", "Orion Nebula"));
  const plan0 = planNow();
  win.location.hash = "#/sky/quick?target=m31";
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
  });
  await settle();

  test("a mosaic framed for another target says nothing on this sheet, and offers no wizard", () => {
    assert(q('[data-testid="quick-mosaic-note"]') == null,
      "M42's framing was described on M31's sheet - the framing slice is global");
    assert(q('[data-testid="quick-send-to-wizard"]') == null,
      "M31's sheet offered to send M42's framing to the wizard");
    assert(!/panels/.test(q('[data-testid="quick-generate"]').textContent),
      "the CTA counted another target's panels");
  });

  await testAsync("and nothing of it reaches the Plan", async () => {
    await press("quick-generate");
    eq(planNow() === plan0, true, "the Plan changed on another target's framing");
    eq(route().params.mosaic, undefined, "the flow card was told about a mosaic this flow does not have");
  });

  await testAsync("with no framing of its own, the TARGET node commands NO angle", async () => {
    const saves = asks.filter((a) => a.method === "PUT" && /\/api\/flows\/f1(\?|$)/.test(a.url));
    assert(saves.length > 0, "the graph was never saved back");
    const node = saves[saves.length - 1].body?.flow?.graph?.nodes?.find((n: any) => n.type === "target");
    eq(node.params.rotation, -1,
      "-1 is to_plan's own 'no angle constraint'; 23.4 would rotate the camera to a fixture value");
  });

  act(() => { root.unmount(); });
}

// ============================ 4. the flow card draws no synthetic MOSAIC card
{
  seed(null);
  await act(async () => { await useStore.getState().flowsOpen("f1"); });
  win.location.hash = "#/sky/quick/flow?id=f1&mosaic=2x2";
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(FlowCardSheet, { params: { id: "f1", mosaic: "2x2" }, depth: 0 as const }));
  });
  await settle();

  test("a link from before S6 carrying a grid gets the saved graph's lane and no MOSAIC card", () => {
    const lane = q('[data-testid="flow-lane"]');
    assert(lane != null, "the flow card drew no lane - the fixture is wrong, not the component");
    assert(q('[data-lane-card="TARGET"]') != null, "the lane lost the graph's own TARGET card");
    const cards = Array.from(container.querySelectorAll("[data-lane-card]"))
      .map((el: any) => el.getAttribute("data-lane-card"));
    assert(!cards.some((c) => /^MOSAIC/.test(c)),
      `the flow card still draws the synthetic MOSAIC card: ${JSON.stringify(cards)}`);
    assert(!/plan targets|mosaic group/.test(lane.textContent),
      `the lane still describes the retired Plan side channel: "${lane.textContent}"`);
  });

  act(() => { root.unmount(); });
}

// ============================================= 5. one horizon on the night arc
{
  seed(null, 35);
  win.location.hash = "#/sky/quick?target=m31";
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
  });
  await settle();

  test("the arc's dashed floor is the SITE's limit, not a hardcoded 25", () => {
    const line = container.querySelector('[data-testid="quick-arc"] line[stroke-dasharray]') as any;
    assert(line != null, "no dashed floor line on the arc");
    const y = Number(line.getAttribute("y1"));
    near(y, arcY(35), 1e-6, "the dashed line sits at the site's 35 degree limit");
    assert(Math.abs(y - arcY(25)) > 0.5,
      "the line is still drawn at 25 degrees, which is neither the site's limit nor the engine's");
  });

  test("and the legend under it prints that same number", () => {
    const legend = q('[data-testid="quick-arc-floor"]');
    assert(legend != null, "the chart draws a limit line and never says what it is");
    assert(/35°/.test(legend.textContent), `the legend must carry the site's own limit: "${legend.textContent}"`);
    assert(!/25°/.test(container.textContent),
      "the hardcoded 25 degree floor is still being printed somewhere on this sheet");
  });

  act(() => { root.unmount(); });
}

// ================================ 6. the quick defaults, from the SKY hub's end
{
  // The REAL consumer, mounted: Settings > SKY > QUICK SESSION DEFAULTS. Both
  // halves of this loop are asserted against the OTHER surface, here and in
  // `settings/__tests__/settingsDom.test.tsx`, because each half asserting its
  // own storage key is exactly how the two shipped disconnected (review #46).
  const { QuickDefaultsSheet } = await import("../../../settings/sheets/QuickDefaultsSheet");
  seed(null, 20);
  win.location.hash = "#/sky/quick?target=m31";
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
  });
  await settle();

  await testAsync("what this sheet learns is what the SETTINGS sheet shows", async () => {
    const box = q('[data-filter-row="L"] [role="checkbox"]');
    assert(box != null, "no L row to untick - the fixture is wrong, not the component");
    await act(async () => {
      box.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await settle();
    act(() => { root.unmount(); });

    const settings = createRoot(container);
    await act(async () => {
      settings.render(createElement(QuickDefaultsSheet, { params: {}, depth: 0 as const }));
    });
    await settle();

    assert(q('[data-testid="quick-empty"]') == null,
      "the settings sheet still says nothing has been learned after a quick session was edited");
    const l = q('[data-testid="quick-filter-L"]');
    assert(l != null, "no L row on the settings sheet");
    eq(l.getAttribute("aria-checked"), "false",
      "L was unticked on the Sky quick sheet and Settings shows");
    const r = q('[data-testid="quick-filter-R"]');
    eq(r?.getAttribute("aria-checked"), "true", "R was left alone and Settings shows");
    act(() => { settings.unmount(); });
  });
}

const total = passed + failed;
console.log(`quickMosaicDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
