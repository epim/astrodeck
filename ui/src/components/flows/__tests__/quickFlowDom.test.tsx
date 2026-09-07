// quickFlowDom.test.tsx -- the QUICK FLOW sheet: what it posts, and what it asks
// before it opens the shutter.
//
//   Run directly:  npx tsx src/components/flows/__tests__/quickFlowDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE FAILURE TO GUARD AGAINST LOOKS FINE. Every field on this sheet has a
// working default, so a handler that posted its DEFAULTS -- ten subs, the whole
// wheel, guided -- returns a valid 200 and generates the wrong night, with no
// error anywhere. Exactly the shape flowWizardDom.test.tsx was written for on
// the guided sheet. So the tests below change every answer away from its default
// before pressing anything, and assert the exact body.
//
// AND THE SECOND ONE OPENS THE SHUTTER. `SAVE AND RUN` is the only control on a
// library screen that can start the engine, sitting on a sheet designed to be
// fast. It must ask first, and the question must carry the numbers an operator
// checks before walking away -- so "it asked" is not enough to pass here.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";
import { Simulate } from "react-dom/test-utils";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  return Promise.resolve()
    .then(fn)
    .then(() => { passed++; })
    .catch((e) => { failed++; failures.push(`x ${name}: ${(e as Error).message}`); });
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
  same(a: unknown, b: unknown, msg?: string) {
    const A = JSON.stringify(a), B = JSON.stringify(b);
    if (A !== B) throw new Error(`${msg ?? ""} expected ${B}, got ${A}`);
  },
};

const { useStore } = await import("../../../store");
const { api } = await import("../../../api");
const QuickFlow = (await import("../QuickFlow")).default;
const { raHms, decDms, targetFromEntry, quickPayload, OSC_LABEL, DEFAULT_SUBS } =
  await import("../QuickFlow");

// -------------------------------------------------- the pure half, no DOM needed
await test("RA and Dec reach the TARGET node in the format it stores", () => {
  // NGC 6946, from the catalogue row this sheet is handed.
  assert.equal(raHms(20.581111), "20h 34m 52s");
  assert.equal(decDms(60.153889), "+60° 09′ 14″");
  // The sign is always written: a dec is the one coordinate that carries one,
  // and an unsigned string reads as positive by luck rather than by statement.
  assert.equal(decDms(-5.391111), "-05° 23′ 28″");
  // Rounding must not produce 60 seconds or 60 minutes.
  assert.equal(raHms(23.99999), "00h 00m 00s");
  assert.equal(decDms(41.99999), "+42° 00′ 00″");
});

await test("a catalog row becomes name + both coordinates", () => {
  const t = targetFromEntry({
    id: "NGC6946", name: "NGC 6946", type: "galaxy",
    ra_hours: 20.581111, dec_deg: 60.153889, mag: 8.8, size_arcmin: 11,
  } as any);
  assert.same(t, { name: "NGC 6946", ra: "20h 34m 52s", dec: "+60° 09′ 14″" },
    "to_plan reads ra/dec and never the name, so a target with only a name "
    + "slews to the TARGET node's shipped M31:");
});

await test("the payload carries only the ticked filters' exposures", () => {
  const body = quickPayload({
    target: { name: "M 42", ra: "05h 35m 17s", dec: "-05° 23′ 28″" },
    subs: 12,
    filters: ["L", "Ha"],
    // The whole wheel is on screen; only two rows are in the night.
    exposures: { L: 45, R: 60, G: 60, B: 60, Ha: 300 },
    guided: false,
    run: true,
  });
  assert.same(body.filters, ["L", "Ha"]);
  assert.same(body.exposures, { L: 45, Ha: 300 },
    "an exposure for a filter nobody ticked is a number with nothing to spend "
    + "it on, and it reads as though the filter were in the night:");
  assert.equal(body.guided, false);
  assert.equal(body.run, true);
  assert.equal(body.subs, 12);
});

await test("the one-channel payload sends no filters and one exposure", () => {
  const body = quickPayload({
    target: { name: "M 42", ra: "05h 35m 17s", dec: "-05° 23′ 28″" },
    subs: 30, filters: [], exposures: { [OSC_LABEL]: 90 },
    guided: true, run: false,
  });
  assert.same(body.filters, [],
    "empty is the ANSWER for a rig with no wheel, not a missing one:");
  assert.same(body.exposures, { [OSC_LABEL]: 90 });
});

// ------------------------------------------------------------------- harness
type Posted = { path: string; body: any };
let posted: Posted[] = [];
let confirms: any[] = [];
let confirmAnswer = true;

const RIG_WHEEL = {
  names: ["L", "R", "G", "B", "S", "Ha", "Oiii", "DARK"],
  opaque: [false, false, false, false, false, false, false, true],
};

let root: ReturnType<typeof createRoot> | null = null;
function unmount() {
  if (!root) return;
  const r = root; root = null;
  act(() => { r.unmount(); });
}

function setUp(wheel: any = RIG_WHEEL) {
  unmount();
  posted = [];
  confirms = [];
  confirmAnswer = true;
  const st = useStore.getState() as any;
  useStore.setState({
    flows: { ...st.flows, ui: { ...st.flows.ui, quickOpen: true, highlightId: null } },
    principal: { role: "admin", email: "t@t",
                 caps: ["control.capture", "control.mount"] },
    status: { ...(st.status ?? {}), filterwheel: wheel },
    // The sheet asks before it starts a run; the fixture answers.
    pushConfirm: async (req: any) => { confirms.push(req); return confirmAnswer; },
    flowsLoadLibrary: async () => {},
  } as any);
  (api as any).post = async (path: string, body?: any) => {
    posted.push({ path, body });
    return { flow: { id: "quick-1", name: "Quick: NGC 6946" }, started: !!body?.run,
             run: { started: true, flow_id: "quick-1", frames: 60, unmapped: [] } };
  };
}

async function mount() {
  const host = document.getElementById("root")!;
  unmount();
  host.innerHTML = "";
  root = createRoot(host);
  await act(async () => { root!.render(React.createElement(QuickFlow)); });
  return host;
}

const buttons = () =>
  Array.from(document.querySelectorAll("button")) as HTMLElement[];
const byText = (re: RegExp) =>
  buttons().find((b) => re.test((b.textContent || "").trim())) ?? null;

/** Pick a target the way the catalog dropdown does. The sheet's own picker
 *  debounces a network search; this drives the same `onPick` payload straight
 *  through the field it fills, which is the part under test here. */
async function pickTarget() {
  const search = document.querySelector(
    'input[placeholder^="Search catalog"]') as HTMLInputElement;
  assert.ok(search, "no catalog search on the sheet");
  (api as any).get = async () => ({
    results: [{ id: "NGC6946", name: "NGC 6946", type: "galaxy",
                ra_hours: 20.581111, dec_deg: 60.153889, mag: 8.8,
                size_arcmin: 11 }],
    notes: [],
  });
  await act(async () => {
    search.value = "NGC 6946";
    Simulate.change(search);
  });
  // 250 ms debounce inside CatalogSearch.
  await act(async () => { await new Promise((r) => setTimeout(r, 320)); });
  const hit = buttons().find((b) => /NGC 6946/.test(b.textContent || ""));
  assert.ok(hit, "the catalog result never rendered");
  await act(async () => { hit!.click(); });
}

const tick = async (filter: string) => {
  const box = document.querySelector(
    `input[aria-label="${filter} in the cycle"]`) as HTMLInputElement;
  assert.ok(box, `no ${filter} row on the sheet`);
  await act(async () => { Simulate.change(box); });
};

const setExposure = async (filter: string, secs: string) => {
  const box = document.querySelector(
    `input[aria-label="${filter} exposure, seconds"]`) as HTMLInputElement;
  assert.ok(box, `no ${filter} exposure box`);
  await act(async () => { box.value = secs; Simulate.change(box); });
};

// --------------------------------------------------------------------- tests
await test("the filter rows are the rig's wheel, blackout slot excluded", async () => {
  setUp();
  await mount();
  const rows = Array.from(document.querySelectorAll('input[type="checkbox"]'))
    .map((b) => (b as HTMLElement).getAttribute("aria-label"));
  assert.same(rows, ["L in the cycle", "R in the cycle", "G in the cycle",
                     "B in the cycle", "S in the cycle", "Ha in the cycle",
                     "Oiii in the cycle"],
    "the rows are not this rig's wheel in slot order (a blackout slot passes "
    + "no light, so a Light frame through it is a black frame):");
});

await test("SAVE refuses until there is a target, and says why", async () => {
  setUp();
  await mount();
  const save = byText(/^SAVE$/)!;
  assert.ok(save, "no SAVE button");
  await act(async () => { save.click(); });
  assert.equal(posted.length, 0, "it saved a flow with no target");
  const toasts = (useStore.getState() as any).toasts ?? [];
  assert.ok(toasts.some((t: any) => /target/i.test(t.title ?? "")),
    "the blocked press did nothing and said nothing");
});

await test("it posts the answers the operator gave, not the defaults", async () => {
  setUp();
  await mount();
  await pickTarget();

  // Move every answer off its default.
  const subs = document.querySelector(
    'input[aria-label="Subs per filter"]') as HTMLInputElement;
  assert.ok(subs, "no subs field");
  assert.equal(subs.value, String(DEFAULT_SUBS));
  await act(async () => { subs.value = "12"; Simulate.change(subs); });

  for (const f of ["R", "G", "B", "S", "Oiii"]) await tick(f);   // leaves L + Ha
  await setExposure("Ha", "300");
  await act(async () => { byText(/^Guide/)!.click(); });          // guiding OFF

  await act(async () => { byText(/^SAVE$/)!.click(); });
  assert.equal(posted.length, 1, `expected one POST, saw ${posted.length}`);
  assert.ok(/\/flows\/quick$/.test(posted[0].path),
    `posted to ${posted[0].path} instead of the quick route`);
  const body = posted[0].body;
  assert.same(body.target,
    { name: "NGC 6946", ra: "20h 34m 52s", dec: "+60° 09′ 14″" },
    "the target did not travel intact:");
  assert.equal(body.subs, 12, `sent ${body.subs} subs`);
  assert.same(body.filters, ["L", "Ha"],
    "the ticked set was not what reached the server:");
  assert.same(body.exposures, { L: 60, Ha: 300 },
    "the exposures the sheet DISPLAYED must be the ones it sends:");
  assert.equal(body.guided, false, "the guide toggle was ignored");
  assert.equal(body.run, false, "SAVE started a run");
});

await test("a rig with no wheel offers one channel, not seven guesses", async () => {
  setUp({ names: [], opaque: [] });
  await mount();
  assert.equal(document.querySelectorAll('input[type="checkbox"]').length, 0,
    "checkboxes for filters this rig cannot be shown to have");
  assert.ok(document.querySelector("[data-quick-osc]"), "no one-channel row");
  await pickTarget();
  await setExposure(OSC_LABEL, "90");
  await act(async () => { byText(/^SAVE$/)!.click(); });
  const body = posted[0].body;
  assert.same(body.filters, []);
  assert.same(body.exposures, { [OSC_LABEL]: 90 });
});

await test("SAVE AND RUN asks first, with the numbers in the question", async () => {
  setUp();
  await mount();
  await pickTarget();
  confirmAnswer = false;
  await act(async () => { byText(/SAVE AND RUN/)!.click(); });
  assert.equal(confirms.length, 1,
    "the only control on the library that can open the shutter started a run "
    + "without asking");
  const q = confirms[0];
  assert.ok(/\b70\b/.test(q.title),
    `the question does not say how big the night is: ${q.title}`);
  assert.ok(/NGC 6946/.test(q.title),
    `the question does not name the target: ${q.title}`);
  assert.equal(posted.length, 0,
    "CANCEL on the confirmation still saved and started the flow");
});

await test("answering the confirmation runs it, once", async () => {
  setUp();
  await mount();
  await pickTarget();
  await act(async () => { byText(/SAVE AND RUN/)!.click(); });
  assert.equal(posted.length, 1, `expected one POST, saw ${posted.length}`);
  assert.equal(posted[0].body.run, true, "confirmed the run and did not ask for it");
});

await test("a saved flow is handed back to the library, highlighted", async () => {
  setUp();
  await mount();
  await pickTarget();
  await act(async () => { byText(/^SAVE$/)!.click(); });
  const ui = (useStore.getState() as any).flows.ui;
  assert.equal(ui.quickOpen, false, "the sheet stayed open over its own result");
  assert.equal(ui.highlightId, "quick-1",
    "the library reloads under the operator and nothing says which row is theirs");
});

await test("a run refused AFTER the save is not reported as a lost flow", async () => {
  // The server saves first and reports `saved: true` plus the id inside the
  // refusal, because throwing the save away would discard work the operator
  // asked for. Read off the decoded BODY, not the message: FastAPI nests the
  // payload under `detail` and the human sentence is all that survives into
  // `Error.message`, so "a sequence is already running" (a bare string detail)
  // carries no trace of the save at all.
  setUp();
  await mount();
  await pickTarget();
  const { ApiError } = await import("../../../api");
  (api as any).post = async () => {
    throw new ApiError("a sequence is already running", 409, false, undefined,
                       undefined,
                       { detail: { detail: "a sequence is already running",
                                   flow_id: "quick-9", saved: true } });
  };
  await act(async () => { byText(/SAVE AND RUN/)!.click(); });
  const st = useStore.getState() as any;
  assert.equal(st.flows.ui.quickOpen, false,
    "the flow WAS saved; keeping the sheet open asks the operator to make it "
    + "again and they end up with two");
  assert.equal(st.flows.ui.highlightId, "quick-9",
    "the saved flow is in the library and nothing points at it");
  const toast = (st.toasts ?? []).at(-1);
  assert.ok(/did not start/i.test(toast?.title ?? ""),
    `a refused RUN was reported as a failed SAVE: ${toast?.title}`);
});

await test("a failed save keeps the sheet and the answers", async () => {
  setUp();
  await mount();
  await pickTarget();
  (api as any).post = async () => { throw new Error("boom"); };
  await act(async () => { byText(/^SAVE$/)!.click(); });
  assert.equal((useStore.getState() as any).flows.ui.quickOpen, true,
    "the sheet closed on a failed save, losing the operator's answers");
});

// -------------------------------------------------------------------- report
unmount();
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nquickFlowDom.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  process.exitCode = 1;
}
