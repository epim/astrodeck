// sendToWizardSheet.test.tsx - Send to Flow Wizard's stepped sheet MOUNTED on
// the real store, and its classic host (#196; spec 2026-09-23 flows mosaic,
// Revision 2 ruling 4, D13, D-FU-2, section 8 S6).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/wizard/__tests__/sendToWizardSheet.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE. wizardModel.test.ts holds the pure rules. This
// file holds that the SHEET obeys them with a real store and a real
// flowsSlice under it, graded on the requests that reach the network:
//
//   1. THE DOOR STAYS LAZY. Importing `index.ts` or the classic host resolves
//      no SendToWizardSheet.tsx; loading it does (the positive control).
//   2. THE WALK. The recorded prefill shows what arrived and asks nothing of
//      it; FILTERS and GUIDING are asked; GENERATE posts EXACTLY ONE `POST
//      /api/flows/wizard`, whose body is the route's recorded request
//      (server/tests/fixtures/wizard_mosaic_answer.json), however often it is
//      pressed; the review prints the recorded compile's numbers and the
//      doctor's verdict; RUN opens the saved flow and posts its `/run`
//      through `runAnsweringQuestions`; OPEN IN EDITOR opens it and hands the
//      host its id; EDIT FRAMING hands the host the way back.
//   3. WHAT IS MISSING IS ASKED: a target with no name or coordinates, a
//      mosaic with no angle, and a rig with no camera field (which is offered
//      one target, and then sends no grid).
//   4. THE REVIEW IS THE SERVER'S, and RUN IS LOCKED ON A LOSS with its
//      reason on screen, graded on requests: a locked RUN posts no `/run`.
//   5. A FLOW OPEN WITH EDITS is saved before the wizard's flow replaces it.
//
// Every mutant below was run in a private scratch copy of ui/
// (scratchpad/S6-WIZ-UI-mut), never in the shared tree (#254), and the
// failure it produced is quoted verbatim.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------ resolve recorder first
// Installed before ANY import below, so the first import of the door is the
// first time the process resolves it and everything it drags in.
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
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.WebSocket = class { close() {} addEventListener() {} send() {} };

// ------------------------------------------------------------ the network
// Every request is recorded with its decoded body, and answered by `answer`
// below. A request nothing answers gets a 404, and is still recorded, so a
// test can assert it was never made.
interface Call { method: string; url: string; body: any }
let calls: Call[] = [];
type Reply = { status: number; body: unknown } | Promise<{ status: number; body: unknown }>;
let answer: (c: Call) => Reply | null = () => null;
win.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const c: Call = {
    method: init?.method ?? "GET",
    url: String(url),
    body: init?.body !== undefined ? JSON.parse(init.body) : undefined,
  };
  calls.push(c);
  const r = await (answer(c) ?? { status: 404, body: { detail: "not in this fixture" } });
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
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame", "Image", "URL", "fetch", "Blob", "WebSocket",
  "location", "history",
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
/** A case that mounts nothing (the lazy door's, which run before React is
 *  imported, so the resolve recorder sees the first import of each module). */
async function plain(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
/** Each mounting case UNMOUNTS in `finally`: a failed assertion must not
 *  leave its sheet mounted, where the next mount would reuse its state. */
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { unmount(); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
function canon(v: any): any {
  if (Array.isArray(v)) return v.map(canon);
  if (v && typeof v === "object") {
    return Object.fromEntries(Object.keys(v).sort().map((k) => [k, canon(v[k])]));
  }
  return v;
}
const isSheet = (u: string) => /\/components\/flows\/wizard\/SendToWizardSheet\.tsx$/.test(u);
const short = (u: string) => u.replace(/^.*\/ui\/src\//, "");

// ============================================= 1. the door stays lazy
//
// MUTANT "sheet imported statically by the door" (index.ts:
// `import SendToWizardSheet from "./SendToWizardSheet";` and
// `SendToWizardSheetLazy = lazy(() => Promise.resolve({ default:
// SendToWizardSheet }))`).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x importing the door or the classic host resolves no sheet module: importing index.ts resolved the sheet - every door would pay for it:
//     expected ""
//     got      "components/flows/wizard/SendToWizardSheet.tsx"
//
// MUTANT "host imports the sheet" (SendToWizardHost.tsx mounts the default
// export of "./SendToWizardSheet" in place of the lazy door).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x importing the door or the classic host resolves no sheet module: importing SendToWizardHost.tsx resolved the sheet - the Atlas would pay for it:
//     expected ""
//     got      "components/flows/wizard/SendToWizardSheet.tsx"

resolved.length = 0;
const door = await import("../index");
const doorResolved = resolved.splice(0);
const hostModule = await import("../SendToWizardHost");
const hostResolved = resolved.splice(0);

await plain("importing the door or the classic host resolves no sheet module", () => {
  assert(doorResolved.some((u) => /\/components\/flows\/wizard\/index\.ts$/.test(u)),
    `the resolve hook did not see index.ts, so this test can see nothing: ${doorResolved.map(short).join(", ")}`);
  eq(doorResolved.filter(isSheet).map(short).join(", "), "",
    "importing index.ts resolved the sheet - every door would pay for it:");
  assert(hostResolved.some((u) => /SendToWizardHost\.tsx$/.test(u)), "the hook did not see the host");
  eq(hostResolved.filter(isSheet).map(short).join(", "), "",
    "importing SendToWizardHost.tsx resolved the sheet - the Atlas would pay for it:");
});

await plain("loading the door's module resolves the sheet (the recorder sees it)", async () => {
  resolved.length = 0;
  const mod = await door.loadSendToWizardSheet();
  const seen = resolved.splice(0);
  assert(seen.some(isSheet), `loadSendToWizardSheet resolved no SendToWizardSheet.tsx: ${seen.map(short).join(", ")}`);
  eq(typeof mod.default, "function", "the loader did not hand back a component");
  assert((door.SendToWizardSheetLazy as any).$$typeof, "SendToWizardSheetLazy is not a React.lazy component");
});

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { FLOWS_INIT } = await import("../../flowsSlice");
const sheetModule = await import("../SendToWizardSheet");
const Sheet = sheetModule.default;
const { DOCTOR_CLEAR, OPEN_FAILED, RUN_DID_NOT_START } = sheetModule;
const M = await import("../wizardModel");
type WizardPrefill = import("../wizardModel").WizardPrefill;

// ------------------------------------------------------------------ fixtures
function readJson(rel: string): any {
  try {
    return JSON.parse(readFileSync(new URL(rel, import.meta.url), "utf8") as string);
  } catch (e) {
    throw new Error(`cannot read ${rel}: ${(e as Error).message}`);
  }
}
const FX = readJson("../../../../../../server/tests/fixtures/wizard_mosaic_answer.json");
const ID = FX.answer.id as string;

const OPERATOR = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];
/** The fixture's camera: an IMX571 (3.76 um, 6248 x 4176) at 1000 mm, which
 *  `fovFromOptics` makes 1.346 x 0.900 deg, the field the recorded answer's
 *  TARGET carries. */
const OPTICS = { focal_length_mm: 1000, pixel_size_um: 3.76, sensor_width_px: 6248, sensor_height_px: 4176 };

/** The recorded request's prefill (see wizardModel.test.ts: no overlap, as
 *  the recorded request carries none). */
function fxPrefill(over: Partial<WizardPrefill> = {}): WizardPrefill {
  return {
    name: FX.request.target, ra: FX.request.ra, dec: FX.request.dec,
    angleMode: "Rotate to PA", paDeg: 30, rows: 2, cols: 3, overlapPct: null,
    skip: "2-3", fov: { xDeg: 1.346, yDeg: 0.9 },
    ...over,
  };
}

const RUN_OK = { started: true, flow_id: ID, frames: 200, unmapped: [] };

/** The routes the walk reaches, each answered from the recording. `compile`
 *  is the saved flow's check; `wizard` may be held open by a test. */
function routes(o: { compile?: unknown; wizard?: Reply } = {}): (c: Call) => Reply | null {
  return (c) => {
    if (c.method === "POST" && c.url.endsWith("/api/flows/wizard")) return o.wizard ?? { status: 200, body: FX.answer };
    if (c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/compile`)) return { status: 200, body: o.compile ?? FX.compile };
    if (c.method === "POST" && c.url.endsWith("/api/flows/compile")) return { status: 200, body: FX.compile };
    if (c.method === "GET" && c.url.endsWith(`/api/flows/${ID}`)) return { status: 200, body: FX.answer };
    if (c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/run`)) return { status: 200, body: RUN_OK };
    if (c.method === "PUT" && c.url.endsWith("/api/flows/other")) {
      return { status: 200, body: { ...OTHER, graph: c.body?.flow?.graph ?? OTHER.graph } };
    }
    return null;
  };
}

const OTHER = {
  id: "other", name: "Another flow", folder: "My flows", tagline: "", created_ts: 0, updated_ts: 0,
  last_run: null, last_result: "", readonly: false, graph: { nodes: [], edges: [] },
};

function setup(o: { optics?: boolean; camera?: boolean } = {}): void {
  useStore.setState({
    principal: { role: "operator", email: null, caps: OPERATOR },
    equipConnected: o.camera ?? true,
    status: {} as any,
    config: o.optics === false ? null : ({ optics: OPTICS } as any),
    flows: { ...FLOWS_INIT },
  } as any);
  calls = [];
  answer = routes();
}

// ------------------------------------------------------------------ mounting
const container = win.document.getElementById("root");
const root = createRoot(container);
let edits = 0;
let closes = 0;
let started = 0;
let opened: string[] = [];
function mount(prefill: WizardPrefill, withEdit = true): void {
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(Sheet, {
      prefill,
      onClose: () => { closes++; },
      ...(withEdit ? { onEditFraming: () => { edits++; } } : {}),
      onOpenInEditor: (id: string) => { opened.push(id); },
      onStarted: () => { started++; },
    }));
  });
}
function unmount(): void {
  act(() => { root.render(null); });
  edits = 0; closes = 0; started = 0; opened = [];
}
async function flush(ms = 5): Promise<void> {
  for (let i = 0; i < 3; i++) await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
}
const doc = win.document;
const q = (id: string) => doc.querySelector(`[data-testid="${id}"]`) as any;
const qa = (id: string) => Array.from(doc.querySelectorAll(`[data-testid="${id}"]`)) as any[];
const btn = (id: string) => (q(id)?.closest("button") ?? null) as any;
const locked = (b: any) => b?.getAttribute("aria-disabled") === "true";
const stepNow = () => q("wizard-step")?.getAttribute("data-step");
function click(el: any): void {
  assert(el, "no element to click");
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function typeInto(el: any, value: string): void {
  assert(el, "no input to type into");
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => { setter.call(el, value); el.dispatchEvent(new win.Event("input", { bubbles: true })); });
}
function next(): void {
  const b = btn("wizard-next");
  assert(b && !locked(b), `NEXT is locked on ${stepNow()}: ${b?.getAttribute("title")}`);
  click(b);
}
function tickLRGB(): void {
  for (const f of ["L", "R", "G", "B"]) click(q(`wizard-filter-${f}`));
}
/** The walk to the review with the recorded answers. */
function walkToReview(): void {
  next();                    // target -> framing
  next();                    // framing -> filters
  tickLRGB();
  next();                    // filters -> guiding
  next();                    // guiding -> review
  eq(stepNow(), "review", "the walk did not reach the review");
}
const wizardPosts = () => calls.filter((c) => c.method === "POST" && c.url.endsWith("/api/flows/wizard"));
const runPosts = () => calls.filter((c) => c.method === "POST" && /\/run$/.test(c.url));

// ============================================================ 2. the walk
//
// MUTANT "prefilled skip not sent" (wizardModel.ts wizardBody: the line
// `if (!blank(p.skip)) body.skip = p.skip;` deleted).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x the recorded prefill walks to one POST whose body is the recorded request: the posted body is not the recorded request:
//     expected {"angle_mode":"Rotate to PA","cols":3,"cycle_plan":"L 60, R 60, G 60, B 60","cycles":10,"dec":"+41° 30' 00\"","guiding":true,"kind":"Mosaic","options":["HFR watchdog"],"pa_deg":30,"ra":"00h 43m 15.0s","rows":2,"skip":"2-3","target":"M31"}
//     got      {"angle_mode":"Rotate to PA","cols":3,"cycle_plan":"L 60, R 60, G 60, B 60","cycles":10,"dec":"+41° 30' 00\"","guiding":true,"kind":"Mosaic","options":["HFR watchdog"],"pa_deg":30,"ra":"00h 43m 15.0s","rows":2,"target":"M31"}
//
// MUTANT "a second press posts again" (SendToWizardSheet.tsx generate: the
// `|| inFlight.current` guard removed and the GENERATE reason's
// `generating ? BUSY` arm removed).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x GENERATE pressed twice while the first is out posts once: two presses posted twice:
//     expected 1
//     got      2

await test("the recorded prefill walks to one POST whose body is the recorded request", async () => {
  setup();
  mount(fxPrefill());
  eq(stepNow(), "target", "the sheet did not open on TARGET");
  assert(q("send-to-wizard-sheet"), "the sheet's view marker is missing");
  // What arrived is shown, tagged, and never asked.
  for (const k of ["name", "ra", "dec"]) assert(q(`wizard-arrived-${k}`), `the arrived ${k} is not shown`);
  eq(qa("wizard-ask-name").length + qa("wizard-ask-ra").length + qa("wizard-ask-dec").length, 0,
    "an arrived target field was asked again");
  assert(q("wizard-arrived-ra").textContent.includes(FX.request.ra), "the RA shown is not the prefill's");
  next();
  eq(stepNow(), "framing", "NEXT did not reach FRAMING");
  assert(q("wizard-grid").textContent.includes("3 x 2 panels"), `grid line: ${q("wizard-grid")?.textContent}`);
  assert(q("wizard-skip").textContent.includes("2-3"), "the skipped panel is not shown");
  assert(q("wizard-arrived-angle").textContent.includes("rotate to 30.0 deg"), "the arrived angle is not shown");
  eq(qa("wizard-ask-pa").length, 0, "an arrived angle was asked again");
  assert(q("wizard-rig-field").textContent.includes("1.35 x 0.90 deg"), "the rig's field is not shown");
  eq(qa("wizard-field-changed").length, 0, "one camera read as two");
  click(q("wizard-edit-framing"));
  eq(edits, 1, "EDIT FRAMING did not hand the host the way back");
  next();
  eq(stepNow(), "filters", "NEXT did not reach FILTERS");
  assert(locked(btn("wizard-next")), "FILTERS opened with a filter already chosen for the operator");
  eq(q("wizard-missing").textContent, M.NEED_FILTER, "the missing line is not the NEXT lock's reason");
  assert(q("wizard-no-wheel"), "the assumed wheel is not said to be assumed");
  tickLRGB();
  eq(q("wizard-ask-cycles").value, "10", "subs of each did not start at the default");
  next();
  eq(stepNow(), "guiding", "NEXT did not reach GUIDING");
  eq(q("wizard-guiding-on").getAttribute("aria-checked"), "true", "guiding did not start on");
  next();
  eq(stepNow(), "review", "NEXT did not reach REVIEW");
  eq(wizardPosts().length, 0, "something was generated before GENERATE");
  click(btn("wizard-generate"));
  await flush();
  eq(wizardPosts().length, 1, "GENERATE did not make exactly one request");
  const body = wizardPosts()[0].body;
  eq(canon(body), canon(FX.request), "the posted body is not the recorded request:");
  eq(JSON.stringify(body), JSON.stringify(FX.request), "the posted body's keys are not in the recorded order:");
  assert(q("wizard-saved").textContent.includes("Saved as M31"), "the review does not say what was saved");
});

await test("GENERATE pressed twice while the first is out posts once", async () => {
  setup();
  let release: (v: { status: number; body: unknown }) => void = () => {};
  const held = new Promise<{ status: number; body: unknown }>((r) => { release = r; });
  answer = routes({ wizard: held });
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  click(btn("wizard-generate"));
  await flush();
  eq(wizardPosts().length, 1, "two presses posted twice:");
  await act(async () => { release({ status: 200, body: FX.answer }); await Promise.resolve(); });
  await flush();
  eq(wizardPosts().length, 1, "the answer to the first press posted again");
});

// ============================================================ 3. the review
//
// MUTANT "review computes subs locally" (wizardModel.ts reviewLines:
// `r.subs_per_panel` replaced by `r.steps * (r.rounds ?? 1)` and
// `r.subs_total` by `r.panels * r.steps * (r.rounds ?? 1)`).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x the review prints the compile's numbers as they came: the review did not print the compile's numbers:
//     expected ["5 panels: 37 subs per panel, 185 in all","0.5 h per panel, 2.5 h in all"]
//     got      ["5 panels: 40 subs per panel, 200 in all","0.5 h per panel, 2.5 h in all"]

// MUTANT "close while generating" (SendToWizardSheet.tsx: `closeReason`
// is always null).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x CLOSE refuses while GENERATE is out, and closes once it has answered: CLOSE is open while GENERATE is out

await test("CLOSE refuses while GENERATE is out, and closes once it has answered", async () => {
  setup();
  let release: (v: { status: number; body: unknown }) => void = () => {};
  const held = new Promise<{ status: number; body: unknown }>((r) => { release = r; });
  answer = routes({ wizard: held });
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  assert(locked(btn("wizard-close")), "CLOSE is open while GENERATE is out");
  click(btn("wizard-close"));
  eq(closes, 0, "CLOSE closed the sheet while GENERATE was saving a flow");
  await act(async () => { release({ status: 200, body: FX.answer }); await Promise.resolve(); });
  await flush();
  assert(q("wizard-saved"), "the saved flow was not shown once GENERATE answered");
  click(btn("wizard-close"));
  eq(closes, 1, "CLOSE did not close once GENERATE had answered");
});

await test("the review prints the recorded compile's numbers and the doctor's verdict", async () => {
  setup();
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  const checks = calls.filter((c) => c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/compile`));
  eq(checks.length, 1, "the saved flow was not checked by the server, once");
  eq(qa("wizard-review-line").map((l) => l.textContent),
    ["5 panels: 40 subs per panel, 200 in all", "0.67 h per panel, 3.33 h in all"], "the review's numbers:");
  eq(q("wizard-doctor-clear")?.textContent, DOCTOR_CLEAR, "a clean compile did not say the doctor raised nothing");
  eq(qa("wizard-run-reason").length, 0, `RUN is locked on a clean compile: ${q("wizard-run-reason")?.textContent}`);
  assert(!locked(btn("wizard-run")), "RUN is locked on a clean compile");
});

await test("the review prints the compile's numbers as they came", async () => {
  setup();
  const doctored = JSON.parse(JSON.stringify(FX.compile));
  Object.assign(doctored.readouts.n2, { subs_per_panel: 37, subs_total: 185, panel_s: 1800, total_s: 9000 });
  answer = routes({ compile: doctored });
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  eq(qa("wizard-review-line").map((l) => l.textContent),
    ["5 panels: 37 subs per panel, 185 in all", "0.5 h per panel, 2.5 h in all"],
    "the review did not print the compile's numbers:");
});

// ============================================================ 4. RUN
//
// MUTANT "RUN open on a loss" (wizardModel.ts runLock: the `const loss =
// f.losses[0]; if (loss) return ...` branch deleted).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x RUN is locked on a loss, says why, and a press posts no run: a loss left RUN open:
//     expected true
//     got      false

await test("RUN is locked on a loss, says why, and a press posts no run", async () => {
  setup();
  const lossy = { ...FX.compile, unmapped: [{ key: "g", detail: "GUIDE: settle 1.5 s is not carried", level: "warn" }] };
  answer = routes({ compile: lossy });
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  eq(locked(btn("wizard-run")), true, "a loss left RUN open:");
  assert((q("wizard-run-reason")?.textContent ?? "").includes("GUIDE: settle 1.5 s is not carried"),
    `the reason does not name the loss: ${q("wizard-run-reason")?.textContent}`);
  assert(q("wizard-loss"), "the loss is not listed with the doctor's findings");
  click(btn("wizard-run"));
  await flush();
  eq(runPosts().length, 0, "a locked RUN posted a run");
  eq(started, 0, "a locked RUN reported a start");
});

// MUTANT "run before the flow is open" (SendToWizardSheet.tsx run: the
// `if (!(await openSaved(saved.id))) return;` line deleted, so
// `flowsRun` runs whatever record the store holds).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x RUN opens the saved flow, posts its run through the one loop, and hands the host the start: RUN did not open the saved flow:

await test("RUN opens the saved flow, posts its run through the one loop, and hands the host the start", async () => {
  setup();
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  const before = calls.length;
  click(btn("wizard-run"));
  await flush();
  const after = calls.slice(before);
  const open = after.findIndex((c) => c.method === "GET" && c.url.endsWith(`/api/flows/${ID}`));
  const run = after.findIndex((c) => c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/run`));
  assert(open >= 0, `RUN did not open the saved flow: ${after.map((c) => `${c.method} ${c.url}`).join(", ")}`);
  assert(run > open, "RUN posted before the flow was open, so it would have run whatever was open");
  eq(after[run].body, {
    accept_unmapped: false, force: false, fresh: false, adopt: false, accept_dropped: false, accept_recount: false,
  }, "the run was not posted with every flag false, as flowsApi.run sends it");
  eq(useStore.getState().flows.record?.id, ID, "the store does not hold the saved flow");
  eq(started, 1, "a started run was not handed to the host");
});

await test("RUN is locked without a camera, and OPEN IN EDITOR opens the flow then hands the host its id", async () => {
  setup({ camera: false });
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  assert(locked(btn("wizard-run")), "RUN is open with no camera connected");
  click(btn("wizard-open-editor"));
  await flush();
  eq(useStore.getState().flows.record?.id, ID, "OPEN IN EDITOR did not open the saved flow in the store");
  eq(opened, [ID], "OPEN IN EDITOR did not hand the host the saved flow's id");
});

// Added by the S6-WIZ-UI verifier, each run in its own private scratch copy
// of ui/ (scratchpad/S6-WIZ-UI-verify-mut), never in the shared tree (#254).
//
// MUTANT "doctor's issues not shown" (SendToWizardSheet.tsx: the
// `findings.issues.map(...)` block of the review deleted). Every case above
// stayed green under it: none of their compiles carries an issue.
// Observed ("sendToWizardSheet.test: 21/22 passed"):
//   x the review lists the doctor's issues by level; a warning leaves RUN open, a danger locks it: the doctor's warning is not listed:
//     expected [["warn","WARN  Unguided 120 s subs will trail"]]
//     got      []
//
// MUTANT "the sheet drops the danger lock" (SendToWizardSheet.tsx runReason:
// a runLock answer naming a danger read as null). The loss case above stays
// green under it; wizardModel.test.ts grades runLock itself.
// Observed ("sendToWizardSheet.test: 21/22 passed"):
//   x the review lists the doctor's issues by level; a warning leaves RUN open, a danger locks it: a danger left RUN open:
//     expected true
//     got      false

await test("the review lists the doctor's issues by level; a warning leaves RUN open, a danger locks it", async () => {
  const issueRows = () => qa("wizard-issue").map((p) => [p.getAttribute("data-level"), p.textContent]);
  setup();
  answer = routes({ compile: { ...FX.compile, issues: [{ text: "Unguided 120 s subs will trail", level: "warn" }] } });
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  eq(issueRows(), [["warn", "WARN  Unguided 120 s subs will trail"]], "the doctor's warning is not listed:");
  eq(qa("wizard-doctor-clear").length, 0, "a compile with a warning said the doctor raised nothing");
  assert(!locked(btn("wizard-run")), `a warning locked RUN: ${q("wizard-run-reason")?.textContent}`);
  unmount();

  setup();
  answer = routes({ compile: { ...FX.compile, issues: [{ text: "Dome without a safety monitor", level: "danger" }] } });
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  eq(issueRows(), [["danger", "DANGER  Dome without a safety monitor"]], "the doctor's danger is not listed:");
  eq(locked(btn("wizard-run")), true, "a danger left RUN open:");
  assert((q("wizard-run-reason")?.textContent ?? "").includes("Dome without a safety monitor"),
    `the reason does not name the danger: ${q("wizard-run-reason")?.textContent}`);
  click(btn("wizard-run"));
  await flush();
  eq(runPosts().length, 0, "a RUN locked on a danger posted a run");
  eq(started, 0, "a RUN locked on a danger reported a start");
});

// MUTANT "RUN bypasses the question loop" (SendToWizardSheet.tsx run:
// `await flowsRun()` once in place of `runAnsweringQuestions`). The RUN case
// above stays green under it: a /run answered 200 at once asks nothing.
// Observed ("sendToWizardSheet.test: 21/22 passed"):
//   x RUN asks the server's question through the one loop, and re-posts carrying the answer: the server's question was not asked (1 run post(s))

await test("RUN asks the server's question through the one loop, and re-posts carrying the answer", async () => {
  setup();
  const base = routes();
  let runs = 0;
  answer = (c) => {
    if (c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/run`) && ++runs === 1) {
      // The server's own shape: FastAPI nests the payload under `detail`.
      return { status: 409, body: { detail: {
        code: "unmapped", detail: "parts of this flow do not survive the compile",
        unmapped: [{ key: "g", detail: "GUIDE: settle 1.5 s is not carried", level: "warn" }],
      } } };
    }
    return base(c);
  };
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  click(btn("wizard-run"));
  await flush();
  assert((useStore.getState() as any).confirm, `the server's question was not asked (${runPosts().length} run post(s))`);
  eq(runPosts().length, 1, "a second run was posted before the question was answered");
  eq(started, 0, "the host was told the run started before the question was answered");
  await act(async () => { useStore.getState().resolveConfirm(true); await Promise.resolve(); });
  await flush();
  eq(runPosts().map((c) => c.body?.accept_unmapped), [false, true], "the re-post did not carry the answer:");
  eq(started, 1, "the run started on the answer was not handed to the host");
});

// MUTANT "started read off a leftover phase" (SendToWizardSheet.tsx run: the
// start judged on `isRunPhaseLive(flows.run.phase)` alone, as the sheet
// first shipped). `flowsRun` writes that phase and nothing writes it back, so
// after one run on the page a refused start read as a started one.
// Observed ("sendToWizardSheet.test: 21/22 passed"):
//   x a RUN the server refuses is said, and never read as started off a leftover run phase: a refused run was handed to the host as started:
//     expected 0
//     got      1
//
// MUTANT "started whatever the answer" (SendToWizardSheet.tsx run: the start
// test `|| true`). Observed: the same failure, 21/22.

await test("a RUN the server refuses is said, and never read as started off a leftover run phase", async () => {
  setup();
  const base = routes();
  answer = (c) => (c.method === "POST" && c.url.endsWith(`/api/flows/${ID}/run`)
    ? { status: 409, body: { detail: "a sequence is already running" } }
    : base(c));
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  // An earlier run on this page left the phase live: `flowsRun` is its only
  // writer and nothing writes it back (NowEmpty's RUN_PHASE_GRACE_MS).
  act(() => {
    const st = useStore.getState();
    useStore.setState({ flows: { ...st.flows, run: { ...st.flows.run, phase: "running" } } } as any);
  });
  click(btn("wizard-run"));
  await flush();
  eq(runPosts().length, 1, "precondition: RUN did not post its run");
  eq(started, 0, "a refused run was handed to the host as started:");
  const toasts = JSON.stringify((useStore.getState() as any).toasts ?? []);
  assert(toasts.includes(RUN_DID_NOT_START), `no toast said the flow did not start: ${toasts}`);
});

// MUTANT "open drops the open flow's edits" (SendToWizardSheet.tsx openSaved: the
// dirty-save branch made unreachable (`if (false && ...)`)).
// Observed ("sendToWizardSheet.test: 16/18 passed"):
//   x a flow open with edits is saved before the wizard's flow replaces it: the open flow's edits were not saved first: GET /api/flows/minted-by-the-save, GET /api/flows/minted-by-the-save/progress, POST /api/flows/compile
//   x a flow whose edits will not save is not replaced, and nothing opens: the wizard's flow replaced a flow whose edits did not save
//     expected 0
//     got      1

await test("a flow open with edits is saved before the wizard's flow replaces it", async () => {
  setup();
  useStore.setState({ flows: { ...FLOWS_INIT, record: OTHER as any, graph: OTHER.graph as any, dirty: true } } as any);
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  const before = calls.length;
  click(btn("wizard-open-editor"));
  await flush();
  const after = calls.slice(before);
  const put = after.findIndex((c) => c.method === "PUT" && c.url.endsWith("/api/flows/other"));
  const get = after.findIndex((c) => c.method === "GET" && c.url.endsWith(`/api/flows/${ID}`));
  assert(put >= 0 && get > put, `the open flow's edits were not saved first: ${after.map((c) => `${c.method} ${c.url}`).join(", ")}`);
  eq(opened, [ID], "the saved flow did not open after the save");
});

await test("a flow whose edits will not save is not replaced, and nothing opens", async () => {
  setup();
  useStore.setState({ flows: { ...FLOWS_INIT, record: OTHER as any, graph: OTHER.graph as any, dirty: true } } as any);
  const base = routes();
  answer = (c) => (c.method === "PUT" ? { status: 500, body: { detail: "disk full" } } : base(c));
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  const before = calls.length;
  click(btn("wizard-open-editor"));
  await flush();
  const after = calls.slice(before);
  eq(after.filter((c) => c.method === "GET" && c.url.endsWith(`/api/flows/${ID}`)).length, 0,
    "the wizard's flow replaced a flow whose edits did not save");
  eq(useStore.getState().flows.record?.id, "other", "the open flow was replaced");
  eq(opened, [], "the host was told to open a flow that did not open");
  const toasts = (useStore.getState() as any).toasts ?? [];
  assert(JSON.stringify(toasts).includes(OPEN_FAILED), `no toast said the flow did not open: ${JSON.stringify(toasts)}`);
});

// ============================================================ 5. what is missing

await test("a target with no name or coordinates is asked for them, and NEXT waits", async () => {
  setup();
  mount(fxPrefill({ name: "", ra: "", dec: "" }));
  for (const k of ["name", "ra", "dec"]) assert(q(`wizard-ask-${k}`), `no ${k} was asked`);
  assert(locked(btn("wizard-next")), "NEXT is open with no target");
  eq(q("wizard-missing").textContent, M.NEED_NAME, "the missing line");
  typeInto(q("wizard-ask-name"), "NGC 7000");
  eq(q("wizard-missing").textContent, M.NEED_COORDS, "coordinates were not asked after the name");
  typeInto(q("wizard-ask-ra"), "20h 59m 17s");
  typeInto(q("wizard-ask-dec"), "+44 31 44");
  assert(!locked(btn("wizard-next")), "NEXT stayed locked with a whole target");
});

await test("a mosaic with no angle is asked for one, and the asked angle is posted", async () => {
  setup();
  mount(fxPrefill({ angleMode: null, paDeg: null }));
  next();
  eq(stepNow(), "framing", "NEXT did not reach FRAMING");
  assert(q("wizard-ask-pa") && q("wizard-angle-mode-0"), "no angle was asked");
  assert(locked(btn("wizard-next")), "NEXT is open with no angle");
  eq(q("wizard-missing").textContent, M.NEED_ANGLE, "the missing line");
  click(q("wizard-angle-mode-1"));
  typeInto(q("wizard-ask-pa"), "12.5");
  next();
  tickLRGB();
  next();
  next();
  click(btn("wizard-generate"));
  await flush();
  const body = wizardPosts()[0]?.body;
  eq([body?.angle_mode, body?.pa_deg], ["Camera fixed at PA", 12.5], "the asked angle was not posted");
});

// MUTANT "single target sends the grid" (wizardModel.ts wizardBody: `mosaic =
// isMosaicFraming(p)`, which ignores PLAN AS ONE TARGET).
// Observed ("sendToWizardSheet.test: 17/18 passed"):
//   x a rig with no camera field is offered one target, and then posts no grid: a mosaic planned as one target was posted as a mosaic
//     expected "Deep-sky target"
//     got      "Mosaic"

await test("a rig with no camera field is offered one target, and then posts no grid", async () => {
  setup({ optics: false });
  mount(fxPrefill());
  next();
  eq(q("wizard-missing").textContent, M.NEED_OPTICS, "no optics was not said");
  eq(qa("wizard-ask-pa").length + qa("wizard-arrived-angle").length, 0, "an angle was shown for a grid that cannot exist");
  assert(locked(btn("wizard-next")), "NEXT is open on a mosaic with no field");
  click(q("wizard-single-target"));
  assert(q("wizard-planned-single"), "taking the offer does not say what it means");
  next();
  tickLRGB();
  next();
  next();
  click(btn("wizard-generate"));
  await flush();
  const body = wizardPosts()[0]?.body ?? {};
  eq(body.kind, M.KIND_DEEP_SKY, "a mosaic planned as one target was posted as a mosaic");
  for (const k of ["rows", "cols", "skip", "angle_mode", "pa_deg"]) {
    assert(!(k in body), `the one-target body carries ${k}: ${JSON.stringify(body)}`);
  }
});

// MUTANT "field change never said" (wizardModel.ts fieldChanged: returns null
// whatever the two fields are). The walk above holds the control (one camera,
// no line); nothing held the line itself.
// Observed ("sendToWizardSheet.test: 21/22 passed"):
//   x a framing made at another camera's field says so on FRAMING: the rig's other field was not said on FRAMING: ""

await test("a framing made at another camera's field says so on FRAMING", async () => {
  setup();
  // The same camera at 500 mm: twice the field the door framed with.
  useStore.setState({ config: { optics: { ...OPTICS, focal_length_mm: 500 } } } as any);
  mount(fxPrefill());
  next();
  const line = q("wizard-field-changed")?.textContent ?? "";
  assert(line.includes("1.35 x 0.90 deg") && line.includes("2.69 x 1.80 deg"),
    `the rig's other field was not said on FRAMING: ${JSON.stringify(line)}`);
});

await test("a single target is never asked an angle, and says where its angle is set", async () => {
  setup();
  mount(fxPrefill({ rows: 1, cols: 1, skip: "" }));
  next();
  eq(qa("wizard-ask-pa").length, 0, "a single target was asked an angle");
  assert(q("wizard-one-target-angle"), "a single target framed at an angle does not say where it is set");
  assert(!locked(btn("wizard-next")), "NEXT is locked on a single target");
});

// #460 (the S5/S6 integration). Both doors send a single frame's commanded PA
// with NO angle mode, and the note keyed on the mode alone, so the PA was
// dropped without a word. Mutants run in a private copy of ui/
// (scratchpad/S5-FINAL-INTEG-ui-mut), each from a byte backup restored with
// its sha256 checked.
// MUTANT "a framed PA without a mode is dropped" (SendToWizardSheet.tsx:
// `arrivedAngle` keyed on the mode alone again). Observed
// ("sendToWizardSheet.test: 23/24 passed", with the #458 case below):
//   x a single frame a door sends at a PA with no mode says the PA it was framed at (#460): a PA dialled on a single frame was dropped without a word: ""
// MUTANT "the note drops the number" (`oneTargetAngleNote` returns
// ONE_TARGET_ANGLE whatever arrived). Observed ("23/24 passed"):
//   x a single frame a door sends at a PA with no mode says the PA it was framed at (#460): a PA dialled on a single frame was dropped without a word: "A single target is planned at any angle: set its camera angle on the TARGET in the editor."
// MUTANT "any PA field is an angle" (`arrivedAngle` true whenever the prefill
// has a `paDeg` key, a null one included). Observed ("23/24 passed"):
//   x a single frame a door sends at a PA with no mode says the PA it was framed at (#460): a single frame that arrived with no angle got the angle note
//     expected 0
//     got      1

await test("a single frame a door sends at a PA with no mode says the PA it was framed at (#460)", async () => {
  setup();
  mount(fxPrefill({ rows: 1, cols: 1, skip: "", angleMode: null, paDeg: 30 }));
  next();
  const note = q("wizard-one-target-angle")?.textContent ?? "";
  assert(note.includes("Framed at PA 30.0 deg") && note.includes("on the TARGET in the editor"),
    `a PA dialled on a single frame was dropped without a word: ${JSON.stringify(note)}`);
  eq(qa("wizard-ask-pa").length, 0, "a single target was asked an angle");
  // Control: a single frame with no PA and no mode has no angle to speak of.
  unmount();
  setup();
  mount(fxPrefill({ rows: 1, cols: 1, skip: "", angleMode: null, paDeg: null }));
  next();
  eq(qa("wizard-one-target-angle").length, 0, "a single frame that arrived with no angle got the angle note");
});

// #458 (the S5/S6 integration). GENERATE saved a flow and left the store's
// library as it was, so the Flows screen and the first-run guide's "Pick a
// target" step, which counts saved flows since S6 sent it here, never saw it.
// Mutants in scratchpad/S5-FINAL-INTEG-ui-mut, as above.
// MUTANT "GENERATE leaves the library stale" (the refresh after a saved
// answer removed). Observed ("sendToWizardSheet.test: 23/24 passed"):
//   x a saved flow refreshes the library the store holds, and a refused answer does not (#458): a saved flow did not refresh the library:
//     expected 1
//     got      0
// MUTANT "the library read whatever the answer" (the refresh moved into the
// `finally`). Observed ("23/24 passed"):
//   x a saved flow refreshes the library the store holds, and a refused answer does not (#458): a refused answer read the library:
//     expected 0
//     got      1

await test("a saved flow refreshes the library the store holds, and a refused answer does not (#458)", async () => {
  const CARD = {
    id: ID, name: "saved by the wizard", folder: "My flows", tagline: "", created_ts: 0, updated_ts: 0,
    last_run: null, last_result: "", readonly: false,
  };
  const library = (c: Call): Reply | null => {
    if (c.method === "GET" && c.url.endsWith("/api/flows")) return { status: 200, body: [CARD] };
    if (c.method === "GET" && c.url.endsWith("/api/flows/folders")) return { status: 200, body: [] };
    return null;
  };
  const libraryReads = () => calls.filter((c) => c.method === "GET" && c.url.endsWith("/api/flows"));
  setup();
  answer = (c) => library(c) ?? routes()(c);
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  eq(libraryReads().length, 1, "a saved flow did not refresh the library:");
  const lib = useStore.getState().flows;
  eq([lib.libraryLoaded, lib.cards.map((c: any) => c.id)], [true, [ID]],
    "the store's library does not hold the flow GENERATE saved:");
  // Control: a refused answer saved nothing, and reads no library.
  unmount();
  setup();
  answer = (c) => library(c) ?? (c.url.endsWith("/api/flows/wizard")
    ? { status: 422, body: { detail: { detail: "refused", code: "invalid_wizard_answer" } } }
    : routes()(c));
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  eq(libraryReads().length, 0, "a refused answer read the library:");
});

await test("a refused answer is shown with the server's words, and nothing reads as saved", async () => {
  setup();
  answer = (c) => (c.url.endsWith("/api/flows/wizard")
    ? { status: 422, body: { detail: { detail: "cycle_plan: 'Lum' is not a filter on this wheel", code: "invalid_wizard_answer" } } }
    : routes()(c));
  mount(fxPrefill());
  walkToReview();
  click(btn("wizard-generate"));
  await flush();
  assert((q("wizard-generate-error")?.textContent ?? "").includes("'Lum' is not a filter on this wheel"),
    `the refusal is not shown in the server's words: ${q("wizard-generate-error")?.textContent}`);
  eq(qa("wizard-saved").length + qa("wizard-run").length, 0, "a refused answer reads as a saved flow");
});

// ============================================================ 6. the classic host

await test("the classic host mounts the shared sheet lazily, and its EDIT FRAMING closes the wizard", async () => {
  setup();
  let hostCloses = 0;
  act(() => { root.render(null); });
  act(() => {
    root.render(createElement(hostModule.SendToWizardHost, {
      prefill: fxPrefill(), onClose: () => { hostCloses++; },
    }));
  });
  for (let i = 0; i < 20 && !q("send-to-wizard-sheet"); i++) await flush(10);
  assert(q("send-to-wizard-sheet"), "the host never mounted the shared sheet");
  next();
  click(q("wizard-edit-framing"));
  eq(hostCloses, 1, "the host's EDIT FRAMING did not close the wizard back to the framing");
  act(() => { root.render(createElement(hostModule.SendToWizardHost, { prefill: null, onClose: () => {} })); });
  eq(qa("send-to-wizard-sheet").length, 0, "a null prefill still shows a wizard");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`sendToWizardSheet.test: ${passed}/${total} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total };
export default result;
