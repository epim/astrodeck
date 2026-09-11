// quickMosaicDom.test.tsx - what GENERATE FLOW does with a framing that was
// kept, MOUNTED and pressed (review #3, #34, and the quick-defaults loop of #4).
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/quickMosaicDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// THE DEFECT THIS FILE EXISTS FOR. FRAME mode drew a mosaic, POSTed the engine
// for its panel centres, kept them on `store.framing.panels` and toasted that
// they went "into the flow" - and nothing read them. `panelsToTargets` was
// called by ONE test and by no production code; `quick.tsx` hardcoded
// `panels: 0` and posted a single ra/dec. Every gate was green over a feature
// that did not exist, because the only witness was a toast.
//
// FIVE THINGS THAT CAN ONLY BE CHECKED WITH THE SHEET UP:
//
//   1. THE PANELS REACH THE PLAN. `store.plan.targets` is where the engine's
//      mosaic mechanism lives (N targets sharing a `mosaic_group`); the quick
//      route takes ONE `{name, ra, dec}` and cannot be handed six. This is the
//      SABOTAGE TARGET: break the call and this file goes red.
//   2. EVERY PANEL CARRIES THE ANGLE. `rotation_deg` on a plan target is the
//      ONLY trigger for `goto_and_center`'s rotate-to-PA. Panels without it
//      image at whatever angle the camera was left at, under a card promising
//      an angle.
//   3. A FRAMING FOR ANOTHER TARGET IS NOT THIS TARGET'S. The framing slice is
//      global and shared with the Atlas.
//   4. THE FLOW'S OWN TARGET NODE. `wizard.quick` leaves the node vocabulary's
//      shipped `rotation: 23.4` in place, so every quick flow was quietly
//      commanding a fixture angle.
//   5. ONE HORIZON. The arc's dashed floor must be the site's limit, which is
//      the number the visibility fetch and the ranking already use.

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
const { arcY } = await import("../quickNightArc");

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

function framingFor(id: string, name: string) {
  return {
    target: { id, name, type: "Galaxy", ra_hours: 0.712305, dec_deg: 41.26917, mag: 3.4, size_arcmin: 190 },
    center: { ra_hours: 0.712305, dec_deg: 41.26917 },
    rotation_deg: 30,
    survey: "CDS/P/DSS2/color",
    stretch: "linear",
    fovZoomDeg: 2,
    mosaic: { rows: 2, cols: 2, overlap: 0.15 },
    panels: PANELS,
  };
}

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
    // has: `addTargetsToPlan` reads it back and writes it whole.
    useStore.getState().setPlan({ ...useStore.getState().plan, targets: [] }, false);
  });
}

const press = async (): Promise<void> => {
  await act(async () => {
    q('[data-testid="quick-generate"]').dispatchEvent(
      new win.MouseEvent("click", { bubbles: true, cancelable: true }),
    );
  });
  await settle();
};

// ================================ 1. a framing for THIS target reaches the plan
seed(framingFor("m31", "Andromeda Galaxy"));
let root = createRoot(container);
await act(async () => {
  root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
});
await settle();

test("precondition: the sheet is up and knows it is framing a mosaic", () => {
  assert(q('[data-testid="sky-quick"]') != null,
    "no sky-quick marker: the fixture is wrong, not the component");
  const note = q('[data-testid="quick-mosaic-note"]');
  assert(note != null, "a 2x2 framing produced no word about what GENERATE FLOW will do with it");
  assert(/4 panels/.test(note.textContent), `the note must name the count: "${note.textContent}"`);
  assert(/plan targets/.test(note.textContent),
    "the note must name where the panels go - that is the surprising half");
});

test("the CTA counts the panels it is about to queue", () => {
  const cta = q('[data-testid="quick-generate"]');
  assert(/4 panels/.test(cta.textContent),
    `the CTA hardcoded zero panels for the entire life of the feature: "${cta.textContent}"`);
});

await testAsync("GENERATE FLOW puts every panel in the plan, grouped and angled", async () => {
  eq(useStore.getState().plan.targets.length, 0, "precondition: the plan starts empty");
  await press();

  const targets = useStore.getState().plan.targets;
  eq(targets.length, 4, "the engine's four panel centres must reach the plan");

  // Named as `AtlasView.sendToPlan` named them, because an operator reading the
  // plan and an operator reading the Atlas have to be reading the same night.
  eq(targets[0].name, "m31 1-1", "panel names are 1-based row-col off the catalogue id");
  eq(targets[3].name, "m31 2-2", "the last panel");

  // The COORDINATES are the runnable part: a panel list carrying the framing
  // centre four times would slew to one point and file four names.
  near(targets[0].ra_hours, 0.66, 1e-9, "panel 1-1 RA");
  near(targets[3].dec_deg, 40.8, 1e-9, "panel 2-2 Dec");
  assert(new Set(targets.map((t: any) => `${t.ra_hours},${t.dec_deg}`)).size === 4,
    "the four panels collapsed onto fewer than four distinct pointings");

  // The ANGLE. `rotation_deg` on a plan target is the only trigger for
  // `goto_and_center`'s rotate-to-PA (sequence/engine.py:5185, 5880).
  for (const t of targets) eq(t.rotation_deg, 30, `${t.name} lost the commanded angle`);

  // One group, so a re-frame REPLACES rather than doubling (store.ts:1307).
  eq(new Set(targets.map((t: any) => t.mosaic_group)).size, 1, "the panels must share one group");
  eq(targets[0].mosaic_group, "m31", "grouped by the catalogue id");

  // Only the FIRST panel focuses, exactly as the Atlas did: four autofocus
  // sweeps at the start of a mosaic is twenty minutes of dark sky.
  eq(targets.filter((t: any) => t.autofocus_first).length, 1, "exactly one panel autofocuses first");
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

await testAsync("the flow card is told which grid was queued, in the hash", async () => {
  assert(/mosaic=2x2/.test(String(win.location.hash)),
    `the flow card must be told what was queued rather than reading the global framing slice: "${win.location.hash}"`);
});

act(() => { root.unmount(); });

// ==================== 2. a framing kept for ANOTHER target is not this target's
{
  seed(framingFor("m42", "Orion Nebula"));
  win.location.hash = "#/sky/quick?target=m31";
  root = createRoot(container);
  await act(async () => {
    root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
  });
  await settle();

  test("a mosaic framed for another target says nothing on this sheet", () => {
    assert(q('[data-testid="quick-mosaic-note"]') == null,
      "M42's framing was described on M31's sheet - the framing slice is global");
    assert(!/panels/.test(q('[data-testid="quick-generate"]').textContent),
      "the CTA counted another target's panels");
  });

  await testAsync("and nothing of it reaches the plan", async () => {
    await press();
    eq(useStore.getState().plan.targets.length, 0,
      "another target's panels were queued under this target's name");
    assert(!/mosaic=/.test(String(win.location.hash)),
      "the flow card was told about a mosaic this flow does not have");
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

// ============================================= 3. one horizon on the night arc
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

// ================================ 4. the quick defaults, from the SKY hub's end
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
