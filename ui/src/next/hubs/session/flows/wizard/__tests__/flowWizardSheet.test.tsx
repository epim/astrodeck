// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowWizardSheet.test.tsx - the #/next `flowWizard` sheet, its door and its
// registry entry (#196; spec 2026-09-23 flows mosaic, Revision 2 ruling 4,
// D13, D-FU-2, section 8 S6).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/flows/wizard/__tests__/flowWizardSheet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. THE REGISTRY STAYS LAZY. `wizard/reg.ts` is reached from the entry
//      chunk through `session/sheets/index.ts`, and the sheet it names brings
//      the stepped wizard. Graded at RUN TIME: a resolve hook records every
//      module the process resolves, so importing the registry must resolve
//      neither the adapter nor the shared door nor the sheet, and loading the
//      adapter must still not resolve the sheet until it renders (lazy twice
//      over). The positive controls show the hook sees each module once it is
//      really asked for.
//   2. THE PREFILL IS IN THE ROUTE. `openFlowWizard` puts the framing in the
//      hash beside the door's own params, and the sheet mounted by the real
//      `SheetHost` from that hash shows what arrived.
//   3. WHAT DIFFERS FROM THE CLASSIC HOST: EDIT FRAMING and CLOSE pop the
//      sheet back to the door, and only while it is on top; OPEN IN EDITOR
//      goes to the route a new flow opens at; a started run goes to
//      Session - Now.
//   4. NO FORK. The adapter mounts the shared sheet through its lazy door.
//
// Every mutant below was run in a private scratch copy of ui/
// (scratchpad/S6-WIZ-UI-mut), never in the shared tree (#254), and the
// failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------ resolve recorder first
const resolved: string[] = [];
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    resolve(specifier: string, context: any, nextResolve: any) {
      const r = nextResolve(specifier, context);
      resolved.push(String(r.url));
      return r;
    },
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// @ts-ignore  node built-ins; tsx supplies them at runtime
const { readFileSync } = await import("node:fs");

// ---------------------------------------------------------------- jsdom next
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
let viewportW = 1024;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? viewportW >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.URL.createObjectURL = () => "blob:stub";
win.URL.revokeObjectURL = () => {};
win.WebSocket = class { close() {} addEventListener() {} send() {} };

interface Call { method: string; url: string; body: any }
let calls: Call[] = [];
let answer: (c: Call) => { status: number; body: unknown } | null = () => null;
win.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const c: Call = {
    method: init?.method ?? "GET", url: String(url),
    body: init?.body !== undefined ? JSON.parse(init.body) : undefined,
  };
  calls.push(c);
  const r = answer(c) ?? { status: 404, body: { detail: "not in this fixture" } };
  return {
    ok: r.status >= 200 && r.status < 300, status: r.status, statusText: String(r.status),
    headers: { get: () => "application/json" },
    json: async () => r.body,
  };
};
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLSelectElement",
  "HTMLTextAreaElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch",
  "Blob", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

const isAdapter = (u: string) => /\/flows\/wizard\/FlowWizardSheet\.tsx$/.test(u);
const isSharedDoor = (u: string) => /\/components\/flows\/wizard\/index\.ts$/.test(u);
const isSheet = (u: string) => /\/components\/flows\/wizard\/SendToWizardSheet\.tsx$/.test(u);
const short = (u: string) => u.replace(/^.*\/ui\/src\//, "");

// ============================================= 1. the registry stays lazy
//
// MUTANT "static import in reg.ts" (reg.ts gains
// `import { FlowWizardSheet } from "./FlowWizardSheet";` and its loader
// becomes `() => Promise.resolve({ default: FlowWizardSheet })`).
// Observed ("flowWizardSheet.test: 6/9 passed"):
//   x importing the registry resolves neither the adapter nor the wizard: importing wizard/reg.ts resolved these - the entry chunk pays for the wizard to register one name:
//     expected ""
//     got      "next/hubs/session/flows/wizard/FlowWizardSheet.tsx, components/flows/wizard/index.ts"
//   x loading the adapter resolves the shared door, and still not the sheet: loading resolved no FlowWizardSheet.tsx:
//   x reg.ts imports nothing but types, loads the adapter with import(), and names the adapter's own key: reg.ts has a static import:
//     expected []
//     got      ["./FlowWizardSheet"]
//
// MUTANT "session registry imports the barrel" (session/sheets/index.ts
// takes `flowWizardSheets` from "../flows/wizard", the area barrel).
// Observed ("flowWizardSheet.test: 7/9 passed"):
//   x importing the session hub's registry resolves no wizard module either: the session registry resolved these, and it is in the entry chunk:
//     expected ""
//     got      "next/hubs/session/flows/wizard/FlowWizardSheet.tsx, components/flows/wizard/index.ts"
//   x loading the adapter resolves the shared door, and still not the sheet: the adapter did not reach the shared sheet's door
//
// MUTANT "wizard half not spread" (session/sheets/index.ts loses
// `...flowWizardSheets`).
// Observed ("flowWizardSheet.test: 4/9 passed"):
//   x importing the session hub's registry resolves no wizard module either: precondition: the session registry has no flowWizard entry
//   x the Sky door opens the wizard over the sky with the framing in the route, and the sheet shows it: timed out waiting for the wizard
//   x EDIT FRAMING pops the wizard back to the door, and CLOSE pops nothing once another sheet is on top: timed out waiting for the wizard
//   x OPEN IN EDITOR goes to the new flow's route, on the canvas at desktop and the stage list on a phone: timed out waiting for the wizard
//   x a started run goes to Session - Now: timed out waiting for the wizard
//
// MUTANT "sheet imported statically by the adapter" (FlowWizardSheet.tsx
// imports the default of "components/flows/wizard/SendToWizardSheet"
// and mounts it in place of the lazy door).
// Observed ("flowWizardSheet.test: 7/9 passed"):
//   x loading the adapter resolves the shared door, and still not the sheet: loading the adapter resolved the sheet itself (components/flows/wizard/SendToWizardSheet.tsx) before anything rendered it - a door that imports openFlowWizard would pay for the whole wizard
//   x the adapter mounts the shared sheet through its lazy door, with no fork: FlowWizardSheet.tsx does not mount SendToWizardSheetLazy from the shared door: ["react","../../../../../components/flows/wizard/SendToWizardSheet","../../../../../components/flows/wizard/wizardModel","../../../../breakpoint","../../../../router","../../../../ui","../../../sheets","../create/wizard"]

resolved.length = 0;
const { flowWizardSheets } = await import("../reg");
const regResolved = resolved.splice(0);
const { sheets: sessionSheets } = await import("../../../sheets/index");
const sessionResolved = resolved.splice(0);

await test("importing the registry resolves neither the adapter nor the wizard", () => {
  assert(regResolved.some((u) => /\/flows\/wizard\/reg\.ts$/.test(u)),
    `the resolve hook did not see wizard/reg.ts, so this test can see nothing: ${regResolved.map(short).join(", ")}`);
  const heavy = regResolved.filter((u) => isAdapter(u) || isSharedDoor(u) || isSheet(u));
  eq(heavy.map(short).join(", "), "",
    "importing wizard/reg.ts resolved these - the entry chunk pays for the wizard to register one name:");
  eq(regResolved.map(short).join(", "), "next/hubs/session/flows/wizard/reg.ts",
    "wizard/reg.ts resolved more than itself:");
});

await test("importing the session hub's registry resolves no wizard module either", () => {
  assert(sessionResolved.some((u) => /\/session\/sheets\/index\.ts$/.test(u)),
    "the resolve hook did not see session/sheets/index.ts");
  assert(sessionSheets.flowWizard != null, "precondition: the session registry has no flowWizard entry");
  const heavy = sessionResolved.filter((u) => isAdapter(u) || isSharedDoor(u) || isSheet(u));
  eq(heavy.map(short).join(", "), "", "the session registry resolved these, and it is in the entry chunk:");
});

await test("loading the adapter resolves the shared door, and still not the sheet", async () => {
  // Nothing above has imported the adapter, so this load is the first time
  // the process resolves it and everything it drags in.
  resolved.length = 0;
  const mod = await flowWizardSheets.flowWizard.load();
  const seen = resolved.splice(0);
  eq(typeof mod.default, "function", "the loader did not hand back a component");
  // The positive control: the hook sees the adapter the moment it is asked for.
  assert(seen.some(isAdapter), `loading resolved no FlowWizardSheet.tsx: ${seen.map(short).join(", ")}`);
  const sheet = seen.filter(isSheet);
  assert(sheet.length === 0,
    `loading the adapter resolved the sheet itself (${sheet.map(short).join(", ")}) before anything rendered it - `
    + "a door that imports openFlowWizard would pay for the whole wizard");
  assert(seen.some(isSharedDoor), "the adapter did not reach the shared sheet's door");
});

await test("reg.ts imports nothing but types, loads the adapter with import(), and names the adapter's own key", async () => {
  const src = (readFileSync(new URL("../reg.ts", import.meta.url), "utf8") as string)
    .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  const statics = Array.from(src.matchAll(/^import\s+(?!type\s)[^;]*?from\s+"([^"]+)";/gm)).map((m) => m[1]);
  eq(statics, [], "reg.ts has a static import:");
  assert(/import\("\.\/FlowWizardSheet"\)/.test(src), "reg.ts does not load the adapter with import()");
  const { FLOW_WIZARD_SHEET } = await import("../FlowWizardSheet");
  eq(Object.keys(flowWizardSheets), [FLOW_WIZARD_SHEET], "reg.ts's literal key is not the adapter's FLOW_WIZARD_SHEET:");
});

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FLOWS_INIT } = await import("../../../../../../components/flows/flowsSlice");
const { resetRouterCacheForTests, useRoute, currentRoute } = await import("../../../../../router");
const { SheetHost } = await import("../../../../../shell/SheetHost");
const { FLOW_WIZARD_SHEET, closeWizard, flowWizardParams, openFlowWizard } = await import("../FlowWizardSheet");
type WizardPrefill = import("../../../../../../components/flows/wizard/wizardModel").WizardPrefill;

function readJson(rel: string): any {
  return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8") as string);
}
const FX = readJson("../../../../../../../../server/tests/fixtures/wizard_mosaic_answer.json");
const ID = FX.answer.id as string;
const OPERATOR = ["view.status", "view.preview", "control.mount", "control.capture"];
const OPTICS = { focal_length_mm: 1000, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176 };
const PREFILL: WizardPrefill = {
  name: "M31", ra: FX.request.ra, dec: FX.request.dec, angleMode: "Rotate to PA", paDeg: 30,
  rows: 2, cols: 3, overlapPct: 25, skip: "2-3", fov: { xDeg: 1.346, yDeg: 0.9 },
};

function routesAll(c: Call): { status: number; body: unknown } | null {
  if (c.method === "POST" && c.url.endsWith("/api/flows/wizard")) return { status: 200, body: FX.answer };
  if (c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/compile`)) return { status: 200, body: FX.compile };
  if (c.method === "POST" && c.url.endsWith("/api/flows/compile")) return { status: 200, body: FX.compile };
  if (c.method === "GET" && c.url.endsWith(`/api/flows/${ID}`)) return { status: 200, body: FX.answer };
  if (c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/run`)) {
    return { status: 200, body: { started: true, flow_id: ID, frames: 200, unmapped: [] } };
  }
  return null;
}

function seed(): void {
  act(() => {
    useStore.setState({
      principal: { role: "operator", email: null, caps: OPERATOR } as never,
      authGate: "open",
      wsConnected: false,
      wsPhase: "up",
      equipConnected: true,
      status: { connected: { camera: { connected: true } } } as never,
      config: { optics: OPTICS } as never,
      sequence: { state: "idle" } as never,
      flows: { ...FLOWS_INIT } as never,
    } as never);
  });
  calls = [];
  answer = routesAll;
}

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
let phone = false;
function Host(): any {
  const r = useRoute();
  return createElement(SheetHost as any, { route: r, phone });
}
const doc = win.document;
const q = (id: string): any => doc.querySelector(`[data-testid="${id}"]`);
const btn = (id: string): any => q(id)?.closest("button") ?? null;
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
async function until(what: string, cond: () => boolean): Promise<void> {
  for (let i = 0; i < 400; i++) {
    if (cond()) return;
    await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
  }
  throw new Error(`timed out waiting for ${what}`);
}
async function settle(): Promise<void> {
  for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
}
async function mountAt(hash: string, asPhone = false): Promise<void> {
  phone = asPhone;
  viewportW = asPhone ? 390 : 1024;
  await act(async () => { root.unmount(); });
  win.location.hash = hash;
  resetRouterCacheForTests();
  root = createRoot(container);
  await act(async () => { root.render(createElement(Host)); });
}
async function unmountAll(): Promise<void> {
  await act(async () => { root.unmount(); });
  root = createRoot(container);
}
const hash = (): string => String(win.location.hash);
function nextStep(): void {
  const b = btn("wizard-next");
  assert(b && b.getAttribute("aria-disabled") !== "true", `NEXT is locked: ${b?.getAttribute("title")}`);
  click(b);
}
const stepNow = (): string | null => q("wizard-step")?.getAttribute("data-step") ?? null;
/** Press NEXT until the wizard is on `step`, and say which steps it passed.
 *  Every press must LEAVE the step it was on and the loop is bounded, so a step
 *  that is never reached fails here by name and the walk does not count presses
 *  (#720: adding NIGHT and RESUME to the wizard, backlog WP-100 / #196, turned
 *  two hard-coded presses into red cases that had nothing to do with either). */
function nextUntil(step: string): string[] {
  const visited: string[] = [];
  for (let i = 0; i < 12 && stepNow() !== step; i++) {
    const was = stepNow();
    nextStep();
    assert(stepNow() !== was, `NEXT did not leave ${was}`);
    visited.push(String(stepNow()));
  }
  assert(stepNow() === step, `NEXT never reached ${step}: stopped on ${stepNow()} (passed ${visited.join(", ")})`);
  return visited;
}
async function walkAndGenerate(): Promise<void> {
  nextUntil("filters");
  for (const f of ["L", "R", "G", "B"]) click(q(`wizard-filter-${f}`));
  const passed = nextUntil("review");
  assert(passed.includes("night") && passed.includes("resume"),
    `the walk to REVIEW did not pass the NIGHT and RESUME steps: ${passed.join(", ")}`);
  click(btn("wizard-generate"));
  await until("the saved flow's review", () => q("wizard-saved") !== null && btn("wizard-run") !== null);
  await settle();
}

// ============================================= 2. the prefill is in the route
//
// MUTANT "door drops its own params" (FlowWizardSheet.tsx flowWizardParams
// returns `wizardParams(prefill)` alone).
// Observed ("flowWizardSheet.test: 8/9 passed"):
//   x the Sky door opens the wizard over the sky with the framing in the route, and the sheet shows it: the door's own params did not survive the opening:
//     expected "keep"
//     got      undefined

await test("the Sky door opens the wizard over the sky with the framing in the route, and the sheet shows it", async () => {
  seed();
  await mountAt("#/sky?lens=keep");
  act(() => { openFlowWizard(PREFILL); });
  const r = currentRoute();
  eq([r.hub, r.sheets], ["sky", [FLOW_WIZARD_SHEET]], "the wizard did not open over the sky");
  eq(r.params.lens, "keep", "the door's own params did not survive the opening:");
  eq(r.params.wz_skip, "2-3", "the framing is not in the route");
  await until("the wizard", () => q("send-to-wizard-sheet") !== null);
  assert(q("session-flow-wizard"), "the sheet's own frame is missing");
  assert((q("wizard-arrived-name")?.textContent ?? "").includes("M31"), "the route's name did not arrive");
  // A second opening replaces the first opening's framing, keeping the door's.
  eq(flowWizardParams({ ...PREFILL, skip: "" }, r.params).wz_skip, undefined,
    "a second opening kept the first opening's skip");
  await unmountAll();
});

// ============================================= 3. what differs from classic
//
// MUTANT "unguarded pop" (FlowWizardSheet.tsx closeWizard calls
// `nav.back()` with no top-of-stack check).
// Observed ("flowWizardSheet.test: 8/9 passed"):
//   x EDIT FRAMING pops the wizard back to the door, and CLOSE pops nothing once another sheet is on top: a close from under another sheet popped that sheet:
//     expected "#/sky/flowWizard/coords?wz_angle=Rotate+to+PA&wz_cols=3&wz_dec=%2B41%C2%B0+30%27+00%22&wz_fovx=1.346&wz_fovy=0.9&wz_name=M31&wz_overlap=25&wz_pa=30&wz_ra=00h+43m+15.0s&wz_rows=2&wz_skip=2-3"
//     got      "#/sky/flowWizard?wz_angle=Rotate+to+PA&wz_cols=3&wz_dec=%2B41%C2%B0+30%27+00%22&wz_fovx=1.346&wz_fovy=0.9&wz_name=M31&wz_overlap=25&wz_pa=30&wz_ra=00h+43m+15.0s&wz_rows=2&wz_skip=2-3"

await test("EDIT FRAMING pops the wizard back to the door, and CLOSE pops nothing once another sheet is on top", async () => {
  seed();
  await mountAt("#/sky");
  act(() => { openFlowWizard(PREFILL); });
  await until("the wizard", () => q("send-to-wizard-sheet") !== null);
  nextStep();
  click(q("wizard-edit-framing"));
  eq(currentRoute().sheets, [], "EDIT FRAMING did not pop the wizard back to the door");
  eq(hash(), "#/sky", "EDIT FRAMING did not land on the door's screen");
  // CLOSE is the same guarded pop.
  act(() => { openFlowWizard(PREFILL); });
  await until("the wizard again", () => q("send-to-wizard-sheet") !== null);
  click(q("wizard-close"));
  eq(hash(), "#/sky", "CLOSE did not pop the wizard back to the door");
  // The guard, graded directly: under a second sheet the router unmounts the
  // wizard, so only a close already in flight (a press answered late) can
  // reach it, and that close must pop nothing.
  act(() => { openFlowWizard(PREFILL); });
  act(() => { win.location.hash = hash().replace("/flowWizard", "/flowWizard/coords"); });
  resetRouterCacheForTests();
  await settle();
  eq(currentRoute().sheets, [FLOW_WIZARD_SHEET, "coords"], "precondition: another sheet is not on top");
  const before = hash();
  act(() => { closeWizard(); });
  eq(hash(), before, "a close from under another sheet popped that sheet:");
  // Control: the same call with the wizard on top pops it.
  act(() => { win.location.hash = hash().replace("/flowWizard/coords", "/flowWizard"); });
  resetRouterCacheForTests();
  act(() => { closeWizard(); });
  eq(currentRoute().sheets, [], "the guarded close did not pop the wizard from the top");
  await unmountAll();
});

await test("OPEN IN EDITOR goes to the new flow's route, on the canvas at desktop and the stage list on a phone", async () => {
  seed();
  await mountAt("#/sky");
  act(() => { openFlowWizard(PREFILL); });
  await until("the wizard", () => q("send-to-wizard-sheet") !== null);
  await walkAndGenerate();
  click(btn("wizard-open-editor"));
  await until("the editor's route", () => hash().startsWith("#/session/flows"));
  eq(hash(), `#/session/flows?open=${encodeURIComponent(ID)}`, "OPEN IN EDITOR at desktop");
  eq(useStore.getState().flows.record?.id, ID, "the flow was not open in the store before the route named it");
  await unmountAll();

  seed();
  await mountAt("#/sky", true);
  act(() => { openFlowWizard(PREFILL); });
  await until("the wizard on a phone", () => q("send-to-wizard-sheet") !== null);
  await walkAndGenerate();
  click(btn("wizard-open-editor"));
  await until("the phone editor's route", () => hash().startsWith("#/session/flows"));
  eq(hash(), `#/session/flows/flowStages?open=${encodeURIComponent(ID)}`, "OPEN IN EDITOR on a phone");
  await unmountAll();
});

await test("a started run goes to Session - Now", async () => {
  seed();
  await mountAt("#/sky");
  act(() => { openFlowWizard(PREFILL); });
  await until("the wizard", () => q("send-to-wizard-sheet") !== null);
  await walkAndGenerate();
  click(btn("wizard-run"));
  await until("Session - Now", () => hash().startsWith("#/session/now"));
  assert(calls.some((c) => c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/run`)), "no run was posted");
  await unmountAll();
});

// ============================================================ 4. no fork

await test("the adapter mounts the shared sheet through its lazy door, with no fork", () => {
  const src = readFileSync(new URL("../FlowWizardSheet.tsx", import.meta.url), "utf8") as string;
  assert(/import \{ SendToWizardSheetLazy \} from "\.\.\/\.\.\/\.\.\/\.\.\/\.\.\/components\/flows\/wizard";/.test(src),
    "FlowWizardSheet.tsx does not mount SendToWizardSheetLazy from the shared door: "
    + JSON.stringify(Array.from(src.matchAll(/^import\s[^;]*?from\s+"([^"]+)";/gm)).map((m) => m[1])));
  assert(!/<(?:Overlay|HonestButton)\b/.test(src), "the adapter draws wizard chrome of its own - a fork");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`flowWizardSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
