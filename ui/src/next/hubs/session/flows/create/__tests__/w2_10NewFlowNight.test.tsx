// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w2_10NewFlowNight.test.tsx - NEW FLOW asks how the night ends and whether to
// auto-resume (#718, WP 2.10, owner ruling D-G), #/next sheet, MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx <this file>
//
// WHAT IS GUARDED
//
//   1. THE ANSWERS TRAVEL. The test reads the request body: a handler that
//      shows the two questions and posts the defaults gets a valid 200
//      describing a different night. Auto-resume Off and a Clock time stop at
//      03:30 are the NON-default answers, so a body that drops either fails.
//   2. UNANSWERED IS TODAY'S BODY. A sheet nobody touched posts exactly the
//      three keys it always did.
//   3. THE CHOICES ARE THE STEPPED SHEET'S, not a second list: the rendered
//      stop options are `DUSK_STOPS` and the rendered resume options are
//      `AUTO_RESUME_CHOICES`.
//   4. A CLOCK TIME STOP WITH NO TIME CANNOT BE GENERATED, and says why.

/* eslint-disable @typescript-eslint/no-explicit-any */

{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  return {
    matches: m ? 1024 >= Number(m[1]) : false,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  };
};
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const GENERATED = {
  id: "gen-1", name: "Deep-sky: M16", folder: "My flows", tagline: "generated",
  readonly: false, graph: { nodes: [], edges: [] },
  last_run: null, last_result: "", updated_ts: 1_757_000_000,
};

const asked: { url: string; method: string; body: any }[] = [];
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method, body });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  if (url === "/api/flows/wizard" && method === "POST") return ok(GENERATED);
  if (url === "/api/flows" && method === "GET") return ok([]);
  if (url === "/api/flows/folders") return ok([{ name: "My flows", count: 1, readonly: false }]);
  if (url === "/api/flows/compile" && method === "POST") {
    return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  }
  if (/^\/api\/flows\/[^/]+$/.test(url) && method === "GET") return ok(GENERATED);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { resetRouterCacheForTests } = await import("../../../../../router");
const { FlowNewSheet } = await import("../wizard");
const model = await import("../wizardModel");
const stepped = await import("../../../../../../components/flows/wizard/wizardModel");

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
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const typeInto = (el: any, value: string): void => {
  act(() => {
    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
/** One option of a Segmented group, by the value it carries. */
const option = (group: string, value: string): any =>
  tid(group)?.querySelector(`[data-value="${value}"]`);
const optionValues = (group: string): string[] =>
  Array.from(tid(group).querySelectorAll("[data-value]")).map((b: any) => b.getAttribute("data-value"));
const selected = (group: string): string =>
  tid(group).querySelector('[data-selected="true"]').getAttribute("data-value");
const wizardPosts = (): { body: any }[] =>
  asked.filter((a) => a.method === "POST" && a.url === "/api/flows/wizard");

function route(): void {
  win.history.replaceState(null, "", "http://local/#/session/flows/flowNew");
  resetRouterCacheForTests();
}

async function freshSheet(): Promise<void> {
  asked.length = 0;
  route();
  act(() => {
    useStore.setState({
      principal: { role: "admin", email: null, caps: ["control.capture"] } as never,
      authGate: "open",
      toasts: [] as never,
      flows: { ...useStore.getState().flows, record: null } as never,
    } as never);
  });
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  await act(async () => { root.render(createElement(FlowNewSheet)); });
  await settle();
}

// ------------------------------------------------------------- the model

test("the choices are the stepped sheet's own constants, not a copy", () => {
  assert(model.DUSK_STOPS === stepped.DUSK_STOPS, "DUSK_STOPS is a second list");
  assert(model.AUTO_RESUME_CHOICES === stepped.AUTO_RESUME_CHOICES,
    "AUTO_RESUME_CHOICES is a second list");
  eq(model.CLOCK_TIME, stepped.CLOCK_TIME, "CLOCK_TIME drifted");
});

test("the opening answers are DUSK WINDOW's defaults: Dawn, auto-resume on", () => {
  eq(model.NIGHT_DEFAULTS.stop, "Dawn", "the default stop");
  eq(model.NIGHT_DEFAULTS.autoResume, true, "the default auto-resume");
});

test("wizardBody sends the night's answers only when they are not the defaults", () => {
  const d = model.NIGHT_DEFAULTS;
  eq(JSON.stringify(model.wizardBody("Deep-sky target", ["Guiding"], " M16 ", d)),
    JSON.stringify({ kind: "Deep-sky target", options: ["Guiding"], target: "M16" }),
    "an unanswered sheet posts the three keys it always did");
  const off = model.wizardBody("Deep-sky target", [], "M16", { ...d, autoResume: false });
  eq(off.auto_resume, "Off", "auto-resume Off travels");
  eq(off.stop, undefined, "and the untouched stop does not");
  const clock = model.wizardBody("Deep-sky target", [], "M16",
    { ...d, stop: "Clock time", stopClock: " 03:30 " });
  eq(clock.stop, "Clock time", "a Clock time stop travels");
  eq(clock.stop_clock, "03:30", "with its time, trimmed");
  const stale = model.wizardBody("Deep-sky target", [], "M16", { ...d, stopClock: "03:30" });
  eq(stale.stop_clock, undefined, "a time typed under Clock time and left behind a Dawn stop is not sent");
});

test("a Clock time stop with no valid time is a gap, and nothing else is", () => {
  const d = model.NIGHT_DEFAULTS;
  eq(model.nightGap(d), null, "the defaults have no gap");
  eq(model.nightGap({ ...d, stop: "Clock time", stopClock: "" }), stepped.NEED_STOP_CLOCK,
    "Clock time with no time");
  eq(model.nightGap({ ...d, stop: "Clock time", stopClock: "25:00" }), stepped.NEED_STOP_CLOCK,
    "Clock time with a time that is not on the clock");
  eq(model.nightGap({ ...d, stop: "Clock time", stopClock: "03:30" }), null, "a valid time");
});

// ------------------------------------------------------------- the sheet

await freshSheet();

test("precondition: the two new questions rendered with real controls", () => {
  assert(tid("session-flow-new") != null, "no session-flow-new marker - the fixture is wrong");
  assert(tid("flow-wizard-night") != null, "the night-end question is missing");
  assert(tid("flow-wizard-resume") != null, "the auto-resume question is missing");
  eq(optionValues("flow-wizard-stop").join("|"), stepped.DUSK_STOPS.join("|"),
    "the stop options are the stepped sheet's");
  eq(optionValues("flow-wizard-resume-choice").join("|"), stepped.AUTO_RESUME_CHOICES.join("|"),
    "the resume options are the stepped sheet's");
  eq(selected("flow-wizard-stop"), "Dawn", "the stop opens on Dawn");
  eq(selected("flow-wizard-resume-choice"), "On", "auto-resume opens On");
  assert(tid("flow-wizard-stop-clock") == null, "the clock is asked only beside Clock time");
});

await testAsync("an unanswered sheet posts exactly the three keys it always did", async () => {
  click(tid("flow-new-generate"));
  await settle();
  eq(wizardPosts().length, 1, "one wizard request");
  eq(Object.keys(wizardPosts()[0].body).sort().join(","), "kind,options,target",
    "an unanswered night and resume add nothing to the body");
});

await testAsync("auto-resume Off travels in the request body", async () => {
  await freshSheet();
  click(option("flow-wizard-resume-choice", "Off"));
  await settle();
  eq(selected("flow-wizard-resume-choice"), "Off", "the choice shows");
  click(tid("flow-new-generate"));
  await settle();
  eq(wizardPosts().length, 1, "one wizard request");
  eq(wizardPosts()[0].body.auto_resume, "Off", "the body carries the operator's auto-resume answer");
  eq(wizardPosts()[0].body.stop, undefined, "and no stop nobody chose");
});

await testAsync("a Clock time stop travels with its time", async () => {
  await freshSheet();
  click(option("flow-wizard-stop", "Clock time"));
  await settle();
  typeInto(tid("flow-wizard-stop-clock"), "03:30");
  click(option("flow-wizard-resume-choice", "Off"));
  await settle();
  click(tid("flow-new-generate"));
  await settle();
  const body = wizardPosts()[0]?.body;
  eq(body?.stop, "Clock time", "the stop is the one on screen");
  eq(body?.stop_clock, "03:30", "the time is the one typed");
  eq(body?.auto_resume, "Off", "and auto-resume rides with them");
});

await testAsync("a Clock time stop with no time cannot be generated, and says why", async () => {
  await freshSheet();
  click(option("flow-wizard-stop", "Clock time"));
  await settle();
  click(tid("flow-new-generate"));
  await settle();
  eq(wizardPosts().length, 0, "nothing was posted for a stop with no time");
  const toasts = (useStore.getState() as any).toasts as { title?: string }[];
  assert(toasts.some((t) => t.title === stepped.NEED_STOP_CLOCK),
    `the press did not say why: ${JSON.stringify(toasts.map((t) => t.title))}`);
});

await testAsync("going back to Dawn drops the stop from the body", async () => {
  await freshSheet();
  click(option("flow-wizard-stop", "Clock time"));
  await settle();
  typeInto(tid("flow-wizard-stop-clock"), "03:30");
  click(option("flow-wizard-stop", "Dawn"));
  await settle();
  assert(tid("flow-wizard-stop-clock") == null, "the clock goes with the Clock time choice");
  click(tid("flow-new-generate"));
  await settle();
  eq(Object.keys(wizardPosts()[0].body).sort().join(","), "kind,options,target",
    "a stop put back to Dawn is the default again");
});

const total = passed + failed;
console.log(`w2_10NewFlowNight.test (next): ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
