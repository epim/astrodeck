// quickSessionDom.test.tsx - the quick-session sheet, MOUNTED and pressed.
//
//   Run directly:  npx tsx src/next/hubs/sky/sheets/__tests__/quickSessionDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE FOUR THINGS THIS FILE IS FOR:
//
//  1. A PRECONDITION MARKER. FILTER CYCLE is on screen and there are rows
//     carrying THIS RIG'S filter names. Without that, every assertion below
//     could pass over an empty sheet, which is the trap the repo already has a
//     name for ("a broken probe reads as a passing app").
//  2. THE BLACKOUT SLOT IS NOT A FILTER. The seeded wheel's sixth slot is
//     opaque - it holds no glass. If it reached the cycle the wheel would spend
//     the night rotating to a piece of metal and every sixth sub would be a
//     dark filed as a light.
//  3. THE EXACT POSTED BODY. `subs: 51` is the number of complete passes that
//     fit six hours of L/R/G/B at 60 s and Ha at 180 s. It is what the engine
//     shoots, so a body assertion is the only thing standing between the screen
//     and a night of the wrong length.
//  4. A VIEWER SEES THE SCREEN AND CANNOT FIRE IT. `POST /api/flows/quick`
//     needs BOTH control.capture and control.mount; the button says so and no
//     request leaves.

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

// The persisted per-phone default, written BEFORE the first render: the sheet
// reads it in a lazy initialiser, and six hours is the window the 51 passes are
// counted over.
win.localStorage.setItem("astrodeck-next-sky-quick", JSON.stringify({ hours: 6 }));

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
const { NO_FILTER_REASON } = await import("../quickCopy");

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
const root = createRoot(container);
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.mount", "control.guide"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function seed(principal: unknown = OPERATOR): void {
  useStore.setState({
    principal,
    equipConnected: true,
    wsPhase: "up",
    site: { name: "Back lawn", latitude: 47.6, longitude: -122.3, elevation_m: 50, is_default: false, horizon_min_deg: 20 },
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim" } },
      filterwheel: {
        position: 0,
        names: ["L", "R", "G", "B", "Ha", "Dark"],
        // The sixth slot holds NO GLASS. It must not reach the cycle.
        opaque: [false, false, false, false, false, true],
        narrowband: [false, false, false, false, true, false],
        exposures: [60, 60, 60, 60, 180, null],
      },
    },
    weather: null,
    frameSettings: {
      capture: { exposure_s: 60, gain: 100, offset: 30, binning: 1, filter: null },
      focus: { exposure_s: 2, gain: 200, offset: 30, binning: 1, filter: null },
      solve: { exposure_s: 0.3, gain: 200, offset: 30, binning: 1, filter: null },
      guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
    },
  } as never);
}

seed();
await act(async () => {
  root.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
});
await settle();

// ------------------------------------------------------------ 1. the marker
test("the sheet rendered, with THIS rig's filter rows on it", () => {
  assert(q('[data-testid="sky-quick"]') != null,
    "no sky-quick marker: the fixture is wrong, not the component");
  assert(/FILTER CYCLE/.test(container.textContent),
    "no FILTER CYCLE header - nothing below this asserts anything");
  const rows = qa("[data-filter-row]");
  assert(rows.length >= 4, `expected at least four filter rows, got ${rows.length}`);
  for (const name of ["L", "R", "G", "B"]) {
    const row = q(`[data-filter-row="${name}"]`);
    assert(row != null, `no row for the wheel's ${name} slot`);
    const label = row.querySelector("[data-filter-label]");
    assert(label != null && label.textContent === name,
      `the ${name} row is not labelled with the wheel's own name (got ${label?.textContent})`);
  }
});

test("the blackout slot is not offered as a filter", () => {
  assert(q('[data-filter-row="Dark"]') == null,
    "the opaque slot reached the cycle - the wheel would rotate to a piece of metal");
  eq(qa("[data-filter-row]").length, 5, "five usable slots, not six");
});

test("every checked row shows the same count: one sub per filter per pass", () => {
  const rows = qa("[data-filter-row]");
  for (const r of rows) {
    const count = r.querySelector("[data-filter-count]");
    eq(count?.getAttribute("data-filter-count"), "51",
      `${r.getAttribute("data-filter-row")} does not show 51 passes`);
  }
  // Same count, DIFFERENT banked time: that is what "one sub per filter per
  // pass" means, and it is the half a single number could not say.
  eq(q('[data-filter-row="Ha"] [data-filter-total]').getAttribute("data-filter-total"),
    String(51 * 180), "Ha banks 180 s per pass");
  eq(q('[data-filter-row="L"] [data-filter-total]').getAttribute("data-filter-total"),
    String(51 * 60), "L banks 60 s per pass");
});

test("the CTA says what it will queue", () => {
  const cta = q('[data-testid="quick-generate"]');
  assert(cta != null, "no GENERATE FLOW button");
  assert(/GENERATE FLOW · 5 filters · 6h 00m/.test(cta.textContent),
    `the CTA does not describe the night: ${cta.textContent}`);
  assert(cta.getAttribute("aria-disabled") == null, "an operator's CTA must be live");
});

// -------------------------------------------------------- 2. the posted body
await testAsync("GENERATE FLOW posts subs: 51 and the checked filters, in wheel order", async () => {
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
  eq(body.subs, 51, "`subs` is the pass count the rows show");
  eq(body.filters.join(","), "L,R,G,B,Ha", "the checked slots, in wheel order, blackout excluded");
  eq(body.exposures.Ha, 180, "Ha keeps its own sub length");
  eq(body.exposures.L, 60, "");
  eq(body.target.name, "Andromeda Galaxy", "");
  // BOTH COORDINATES ALWAYS TRAVEL: `to_plan` reads ra/dec and never the name,
  // so a flow with a name and no coordinates slews to the node's shipped M31.
  eq(body.target.ra, "00h 42m 44s", "the RA the TARGET node stores");
  eq(body.target.dec, "+41° 16′ 09″", "the Dec the TARGET node stores");
  eq(body.run, false, "the sheet saves; the flow card runs");
});

// ------------------------------------------------------- 3. nothing to shoot
await testAsync("unticking every filter locks the CTA and names the gap", async () => {
  const boxes = qa('[data-filter-row] [role="checkbox"]');
  eq(boxes.length, 5, "one checkbox per usable slot");
  for (const b of boxes) {
    await act(async () => {
      b.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
  }
  await settle();
  const cta = q('[data-testid="quick-generate"]');
  eq(cta.getAttribute("aria-disabled"), "true", "the CTA must refuse, not look ready");
  eq(cta.getAttribute("title"), NO_FILTER_REASON, "and say why");
  assert(/pick a filter/.test(cta.textContent), `the label does not name the gap: ${cta.textContent}`);

  const before = quickPosts().length;
  await act(async () => {
    cta.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(quickPosts().length, before, "a locked press must fire nothing");
});

// ------------------------------------------------------------- 4. the viewer
await testAsync("a viewer sees the whole sheet, honest-disabled, and fires nothing", async () => {
  win.localStorage.setItem("astrodeck-next-sky-quick", JSON.stringify({ hours: 6 }));
  await act(async () => { root.unmount(); });
  const root2 = createRoot(container);
  seed(VIEWER);
  const visibilityAsksBefore = asks.filter((a) => a.url.includes("/api/visibility")).length;
  await act(async () => {
    root2.render(createElement(QuickSessionSheet, { params: { target: "m31" }, depth: 0 as const }));
  });
  await settle();

  // Positive control: the read-only screen is a WHOLE screen, not a stub.
  assert(/FILTER CYCLE/.test(container.textContent), "a viewer must see the same sheet");
  eq(qa("[data-filter-row]").length, 5, "with the same rows");

  const cta = q('[data-testid="quick-generate"]');
  eq(cta.getAttribute("aria-disabled"), "true", "the CTA is refused for a viewer");
  const why = cta.getAttribute("title") ?? "";
  assert(/needs .* access/.test(why), `the reason must name the access level, got ${why}`);

  // GET /api/visibility needs view.site_derived (catalog/visibility.py), which
  // this viewer does not hold - the sheet must not even ask, not merely eat a
  // 403 quietly (quickVisibility.ts's useVisibilityNight gates on useCan).
  const visibilityAsksAfter = asks.filter((a) => a.url.includes("/api/visibility")).length;
  eq(visibilityAsksAfter, visibilityAsksBefore, "a viewer's render must fire zero /api/visibility requests");
  // And the screen renders the same honest fallback a null ephemeris always
  // has (this is exactly what a 404/403 produced before the gate existed):
  // no dark window to show, so no fabricated dawn time.
  assert(/NO ASTRO-DARK/.test(container.textContent),
    "the honest no-ephemeris state must still render for a viewer");

  const before = quickPosts().length;
  await act(async () => {
    cta.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
  eq(quickPosts().length, before, "a viewer's press must not reach /api/flows/quick");
  await act(async () => { root2.unmount(); });
});

const total = passed + failed;
console.log(`quickSessionDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
