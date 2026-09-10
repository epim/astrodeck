// createDom.test.tsx - SESSION / FLOWS / CREATE: the guided wizard and the
// quick flow, MOUNTED (wave R7 task T-R7-4, rows A16 and A17).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/create/__tests__/createDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. THEY RENDERED, WITH REAL CONTROLS. Both sheets are asserted by marker AND
//      by a control built from the fixture, so nothing below can pass over a
//      blank page.
//   2. THE WIZARD OPENS WHAT IT GENERATED. `POST /api/flows/wizard` generates
//      AND saves; a sheet that posted and then closed would leave the operator
//      looking at the library wondering which of thirty cards is theirs. The
//      test follows the id all the way into `flows.record`.
//   3. THE THREE ANSWERS TRAVEL. Not "a request went out" - the body, because
//      the failure worth guarding is a handler that posts the DEFAULTS and gets
//      a perfectly valid 200 describing a different night.
//   4. ONE CHANNEL IS AN ANSWER, NOT A GAP (D-SKY-3). With `status.filterwheel`
//      absent the sheet renders ONE channel, and the frame arithmetic on screen
//      is the arithmetic in the payload: `filters: []`, and subs x 1.
//   5. A VIEWER SEES BOTH SHEETS, LOCKED, with the capability named on screen
//      (not only in a `title` a touch screen cannot show) and nothing fired.
//   6. A FAILED SAVE KEEPS THE SHEET. `QUICK_SAVE_FAILED` is enqueued and the
//      route does not change - closing on failure would throw away the target,
//      the ticks and the subs the operator just set.
//   7. THE HELPER COPIES DO NOT DRIFT. `create/quickPayload.ts` owns `next`'s
//      copy of five pure helpers whose originals live inside the 497-line legacy
//      component; both are imported here and graded on the same inputs.
//
// Convention: shell-and-tests.md section 4 (jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `create/index.ts` is this area's single CSS import site (the same contract
// `NextApp` has for `next.css`). Node has no idea what a `.css` file is, so a
// load hook answers with an empty module - which keeps both facts true at once:
// the area has one style entry point, and that entry point is still importable
// in a test.
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

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = (q: string) => {
  const m = /min-width:\s*(\d+)px/.exec(q);
  // Tablet: these two sheets are the flows library's creation door, which the
  // wave-1 plan puts at 768 px and up.
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

// ------------------------------------------------------------- the fake rig
const M31 = {
  id: "M31", name: "Andromeda Galaxy", type: "Galaxy",
  ra_hours: 0.712_305_5, dec_deg: 41.268_75, mag: 3.4, size_arcmin: 178, alt: 52,
};

const GENERATED = {
  id: "gen-1", name: "Deep-sky: M16", folder: "My flows", tagline: "generated",
  readonly: false, graph: { nodes: [], edges: [] },
  last_run: null, last_result: "", updated_ts: 1_757_000_000,
};

const QUICK_FLOW = { id: "quick-1", name: "Quick: Andromeda Galaxy" };

const asked: { url: string; method: string; body: any }[] = [];
/** Flipped by the failure test so exactly one `POST /api/flows/quick` refuses. */
let quickFails: null | "save" | "run" = null;

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(init.body) : undefined;
  asked.push({ url, method, body });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  const refuse = (status: number, data: unknown) => ({
    ok: false, status, statusText: "Conflict",
    headers: { get: () => "application/json" },
    json: async () => data,
  });

  if (url.startsWith("/api/catalog")) return ok({ results: [M31], notes: [] });
  if (url === "/api/flows/wizard" && method === "POST") return ok(GENERATED);
  if (url === "/api/flows/quick" && method === "POST") {
    if (quickFails === "save") {
      return refuse(422, { detail: { detail: "the plan has no dusk window", code: "invalid" } });
    }
    if (quickFails === "run") {
      // The server SAVED and then refused to start: `saved` and `flow_id` ride
      // in the refusal's own payload, nested under `detail` the way FastAPI
      // sends it.
      return refuse(409, {
        detail: {
          detail: "a sequence is already running", code: "busy",
          saved: true, flow_id: "quick-1",
        },
      });
    }
    return ok({ flow: QUICK_FLOW, started: !!body?.run, run: { started: true, flow_id: "quick-1", frames: 10, unmapped: [] } });
  }
  if (url === "/api/flows" && method === "POST") return ok({ ...GENERATED, id: "blank-1" });
  if (url === "/api/flows" && method === "GET") return ok([]);
  if (url === "/api/flows/folders") return ok([{ name: "My flows", count: 1, readonly: false }]);
  if (url === "/api/flows/compile" && method === "POST") {
    return ok({ plan: {}, structural: [], issues: [], unmapped: [] });
  }
  if (/^\/api\/flows\/[^/]+$/.test(url) && method === "GET") return ok(GENERATED);
  return refuse(404, { detail: "no" });
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { resetRouterCacheForTests } = await import("../../../../../router");
const { FlowNewSheet } = await import("../wizard");
const { FlowQuickSheet, NO_FILTER_REASON } = await import("../quick");
const area = await import("../index");
const mine = await import("../quickPayload");
const legacy = await import("../../../../../../components/flows/QuickFlow");

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
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const container = win.document.getElementById("root") as any;
let root = createRoot(container);
const settle = async (): Promise<void> => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
/** The catalogue search debounces 250 ms, so the zero-timeout settle above
 *  never reaches it. */
const settleMs = async (ms: number): Promise<void> => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
  await settle();
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const all = (t: string): any[] =>
  Array.from(container.querySelectorAll(`[data-testid="${t}"]`));
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
/** React binds `onBlur` to the native `focusout` at the root, not to `blur`
 *  (which does not bubble and never reaches the delegated listener). A test that
 *  dispatched `blur` would silently never commit the field. */
const blur = (el: any): void => {
  act(() => { el.dispatchEvent(new win.FocusEvent("focusout", { bubbles: true })); });
};
const posts = (path: string): { url: string; method: string; body: any }[] =>
  asked.filter((a) => a.method === "POST" && a.url === path);

/** The route the sheet host would have put us on. Set directly so `nav.back()`
 *  has a sheet to pop, which is what makes "it closed" and "it did NOT close"
 *  two different, observable answers. */
function route(sheet: string): void {
  win.history.replaceState(null, "", `http://local/#/session/flows/${sheet}`);
  resetRouterCacheForTests();
}

function seed(
  role: string,
  caps: string[],
  wheel?: { names: string[]; opaque?: boolean[]; narrowband?: boolean[] },
): void {
  act(() => {
    useStore.setState({
      principal: { role, email: null, caps } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      status: {
        connected: { camera: { connected: true } },
        // `filterwheel` ABSENT is the OSC case the D-SKY-3 test below needs; a
        // wheel is only published when one is connected.
        ...(wheel ? { filterwheel: wheel } : {}),
      } as never,
      equipConnected: true,
      wsPhase: "up",
      confirm: null as never,
      toasts: [] as never,
      flows: {
        ...useStore.getState().flows,
        record: null,
        ui: { ...useStore.getState().flows.ui, wizardOpen: true, quickOpen: true, highlightId: null },
      } as never,
    } as never);
  });
}

async function mount(Comp: any): Promise<void> {
  await act(async () => { root.unmount(); });
  root = createRoot(container);
  await act(async () => { root.render(createElement(Comp)); });
  await settle();
}

const ADMIN = ["view.status", "view.preview", "control.mount", "control.capture", "view.site_derived"];

// ======================================================= 1. the wizard rendered

route("flowNew");
seed("admin", ADMIN);
await mount(FlowNewSheet);

test("precondition: the wizard rendered all three questions and both buttons", () => {
  assert(tid("session-flow-new") != null,
    "no session-flow-new marker - the fixture is wrong, not the sheet");
  eq(all("flow-wizard-step").length, 3,
    "the wizard asks three questions; a missing one is a question the server never gets");
  eq(all("flow-wizard-auto").length, 6, "all six automation chips have to be offered");
  assert(tid("flow-new-generate") != null, "GENERATE FLOW is missing");
  assert(tid("flow-new-blank") != null, "START BLANK is missing - it is a real route, not a stub");
  assert(/Deep-sky target/.test(tid("flow-wizard-kind").textContent),
    "the kind options are the generator's own strings");
});

test("the two automation defaults start on, and nothing else does", () => {
  const on = all("flow-wizard-auto").filter((c) => c.getAttribute("data-active") === "true");
  eq(on.length, 2, "Guiding and HFR watchdog start on");
  eq(on.map((c) => c.textContent).sort().join("|"), "Guiding|HFR watchdog",
    "and they are those two, not two others");
});

test("the note says what GENERATE produces, with a hyphen and no em-dash", () => {
  const note = tid("flow-wizard-note").textContent as string;
  assert(/complete, valid graph - then/.test(note),
    `the legacy em-dash is a hyphen in the rebuild, got "${note}"`);
  assert(!/—/.test(container.textContent), "no em-dash may reach the screen");
});

// ============================================== 2. the wizard's three answers

await testAsync("GENERATE posts the three answers the sheet is holding, not defaults", async () => {
  // Change every answer, so a handler that posted its own defaults is visible.
  const kinds = tid("flow-wizard-kind").querySelectorAll('[role="radio"]');
  click(kinds[2]);                                    // EAA quick look
  click(all("flow-wizard-auto")[1]);                  // + Dusk flats
  typeInto(tid("flow-wizard-target"), "M16, M17");
  await settle();

  click(tid("flow-new-generate"));
  await settle();

  const sent = posts("/api/flows/wizard");
  eq(sent.length, 1, "GENERATE must issue exactly one wizard request");
  eq(sent[0].body?.kind, "EAA quick look", "the kind is the one on screen");
  eq(sent[0].body?.target, "M16, M17", "the target field travels, trimmed");
  eq((sent[0].body?.options ?? []).sort().join("|"), "Dusk flats|Guiding|HFR watchdog",
    "every ticked automation travels, and only the ticked ones");
});

test("the generated flow is OPENED, not merely saved", () => {
  eq(useStore.getState().flows.record?.id, "gen-1",
    "the wizard must open the flow it just generated - the id is the whole point of the route saving it");
  assert(asked.some((a) => a.url === "/api/flows/gen-1" && a.method === "GET"),
    "opening the flow loads the record");
  assert(posts("/api/flows/compile").length > 0, "and re-compiles it, which is what the canvas draws");
});

test("closing pops the sheet and disarms the legacy overlay flag", () => {
  assert(!win.location.hash.includes("flowNew"),
    "a successful generate closes the sheet");
  eq(useStore.getState().flows.ui.wizardOpen, false,
    "and clears flows.ui.wizardOpen, or the legacy FlowWizard is left armed behind it");
});

// ================================================== 3. START BLANK is real

await testAsync("START BLANK posts a two-node graph through the same save route", async () => {
  route("flowNew");
  seed("admin", ADMIN);
  await mount(FlowNewSheet);

  click(tid("flow-new-blank"));
  await settle();

  const sent = posts("/api/flows");
  eq(sent.length, 1, "START BLANK must post a real flow");
  const nodes = sent[0].body?.flow?.graph?.nodes ?? [];
  eq(nodes.length, 2, "an empty canvas gives the operator nothing to drag a wire from");
  eq(nodes.map((n: any) => n.type).join(","), "target,slew", "a TARGET and a SLEW, unwired");
  eq((sent[0].body?.flow?.graph?.edges ?? []).length, 0, "and no wires between them");
});

// ============================================ 4. a failed generate says so

await testAsync("a generate that fails toasts GENERATE_FAILED and keeps the sheet", async () => {
  route("flowNew");
  seed("admin", ADMIN);
  await mount(FlowNewSheet);
  const hash = win.location.hash;

  // The stub answers 404 for anything it does not know; point the wizard at it.
  const realFetch = g.fetch;
  g.fetch = async (url: string, init?: any) => {
    if (url === "/api/flows/wizard") {
      asked.push({ url, method: "POST", body: init?.body ? JSON.parse(init.body) : undefined });
      return {
        ok: false, status: 500, statusText: "Server Error",
        headers: { get: () => "application/json" },
        json: async () => ({ detail: "the generator raised" }),
      };
    }
    return realFetch(url, init);
  };
  click(tid("flow-new-generate"));
  await settle();
  g.fetch = realFetch;

  const toast = useStore.getState().toasts.at(-1);
  eq(toast?.title, area.GENERATE_FAILED, "the failure is named, once, from the shared constant");
  eq(win.location.hash, hash, "a failed generate must not close the sheet and lose the answers");
  assert(tid("session-flow-new") != null, "the sheet is still on screen");
});

// ================================================ 5. the quick sheet, one channel

await testAsync("precondition: with no filter wheel the quick sheet renders ONE channel", async () => {
  route("flowQuick");
  seed("admin", ADMIN);                       // no `wheel` -> status.filterwheel absent
  await mount(FlowQuickSheet);

  assert(tid("session-flow-quick") != null, "no session-flow-quick marker");
  const rows = all("quick-channel");
  eq(rows.length, 1, "no wheel is ONE channel, not a fallback list of seven the rig cannot shoot");
  eq(rows[0].getAttribute("data-channel"), "OSC", "and it is labelled, not named after a slot");
  assert(/one channel/.test(container.textContent),
    "the footer has to say WHY there is one row");
});

await testAsync("the frames on screen are subs x one channel, and the payload agrees", async () => {
  // Pick a target through the catalogue the sheet actually searches.
  const search = container.querySelector('[aria-label="Search the target catalog"]');
  assert(search != null, "precondition: the catalogue search is the target picker");
  typeInto(search, "M31");
  await settleMs(300);
  const hit = Array.from(container.querySelectorAll("button"))
    .find((b: any) => /Andromeda Galaxy/.test(b.textContent)) as any;
  assert(hit != null, "the stubbed catalogue row never reached the dropdown");
  click(hit);
  await settle();

  eq(tid("quick-target") != null, true, "the picked target replaces the search box");
  const summary = tid("quick-summary").textContent as string;
  assert(/^10 frames:/.test(summary),
    `10 subs on one channel is 10 frames, got "${summary}"`);

  click(tid("flow-quick-save"));
  await settle();

  const sent = posts("/api/flows/quick");
  eq(sent.length, 1, "SAVE must issue exactly one quick request");
  eq(sent[0].body?.subs, 10, "DEFAULT_SUBS travels");
  eq((sent[0].body?.filters ?? []).length, 0,
    "one channel posts NO filters - an invented slot name lands in the FITS header");
  eq(sent[0].body?.exposures?.OSC, 60,
    "the one channel's exposure still travels, keyed by the label");
  eq(sent[0].body?.run, false, "SAVE does not start a run");
  eq(sent[0].body?.target?.ra, "00h 42m 44s", "both coordinates travel, not just the name");
  eq(sent[0].body?.target?.dec, "+41° 16′ 07″", "and the dec carries its sign");
});

test("a saved quick flow is highlighted in the library that reloads under it", () => {
  eq(useStore.getState().flows.ui.highlightId, "quick-1",
    "the card has to say which of thirty rows is theirs");
  assert(asked.some((a) => a.url === "/api/flows" && a.method === "GET"),
    "the library reloads so the new flow is in it");
  assert(!win.location.hash.includes("flowQuick"), "and the sheet closes on success");
});

// ====================================== 6. a wheel is the rig's, and the sums move

await testAsync("with a wheel, unticking a filter changes the frames AND the payload", async () => {
  route("flowQuick");
  seed("admin", ADMIN, { names: ["L", "R", "G", "B"], opaque: [false, false, false, false] });
  await mount(FlowQuickSheet);

  eq(all("quick-channel").length, 4, "the rows are this rig's wheel, verbatim");
  const search = container.querySelector('[aria-label="Search the target catalog"]');
  typeInto(search, "M31");
  await settleMs(300);
  click(Array.from(container.querySelectorAll("button"))
    .find((b: any) => /Andromeda Galaxy/.test(b.textContent)) as any);
  await settle();

  assert(/^40 frames:/.test(tid("quick-summary").textContent),
    "10 subs of four filters is 40 frames");

  // Untick B.
  const ticks = all("quick-channel-tick");
  eq(ticks.length, 4, "every wheel row is tickable");
  click(ticks[3]);
  await settle();
  assert(/^30 frames:/.test(tid("quick-summary").textContent),
    "unticking a filter has to change the number on screen");

  // Retype one exposure, so the payload cannot be the defaults.
  const exp = all("quick-exposure")[0];
  typeInto(exp, "120");
  blur(exp);
  await settle();

  click(tid("flow-quick-save"));
  await settle();
  const sent = posts("/api/flows/quick");
  const last = sent[sent.length - 1];
  eq((last.body?.filters ?? []).join(","), "L,R,G", "only the ticked slots travel, in wheel order");
  eq(last.body?.exposures?.L, 120, "the typed exposure travels");
  assert(last.body?.exposures?.B === undefined,
    "an unticked slot has no exposure to spend - sending one would read as being in the night");
});

await testAsync("unticking every filter locks SAVE with the reason, and fires nothing", async () => {
  route("flowQuick");
  seed("admin", ADMIN, { names: ["L", "R"], opaque: [false, false] });
  await mount(FlowQuickSheet);
  // A target first: the reasons are ordered, and "pick a target" outranks "tick
  // a filter" because it is the earlier thing to do.
  const pick = container.querySelector('[aria-label="Search the target catalog"]');
  typeInto(pick, "M31");
  await settleMs(300);
  click(Array.from(container.querySelectorAll("button"))
    .find((b: any) => /Andromeda Galaxy/.test(b.textContent)) as any);
  await settle();
  for (const t of all("quick-channel-tick")) { click(t); await settle(); }

  const save = tid("flow-quick-save");
  eq(save.getAttribute("aria-disabled"), "true", "SAVE must be honest-disabled, never `disabled`");
  eq(save.getAttribute("title"), NO_FILTER_REASON, "and name what to do next");

  const before = asked.length;
  click(save);
  await settle();
  eq(asked.length, before, "a locked SAVE must not reach the network");
});

await testAsync("the narrowband word is the RIG's answer, not a guess from the name", async () => {
  route("flowQuick");
  // A wheel whose L slot really IS narrowband (a solar or comet filter named L
  // is not a fiction; the point is that the rig gets to say so). Name-based
  // guessing would print "broadband" here and the exposure default beside it
  // would then describe a different filter.
  seed("admin", ADMIN, {
    names: ["L", "Ha"], opaque: [false, false], narrowband: [true, false],
  });
  await mount(FlowQuickSheet);
  const rows = all("quick-channel");
  assert(/narrowband/.test(rows[0].textContent),
    "the rig said L is narrowband; the sheet must not overrule it from the name");
  assert(/broadband/.test(rows[1].textContent),
    "and it must not overrule the rig for Ha either");
});

// ========================================= 7. SAVE AND RUN asks, with the numbers

await testAsync("SAVE AND RUN raises the confirm carrying the frames, filters and guiding", async () => {
  route("flowQuick");
  seed("admin", ADMIN, { names: ["L", "R"], opaque: [false, false] });
  await mount(FlowQuickSheet);
  const search = container.querySelector('[aria-label="Search the target catalog"]');
  typeInto(search, "M31");
  await settleMs(300);
  click(Array.from(container.querySelectorAll("button"))
    .find((b: any) => /Andromeda Galaxy/.test(b.textContent)) as any);
  await settle();

  const before = posts("/api/flows/quick").length;
  click(tid("flow-quick-run"));
  await settle();

  const req = useStore.getState().confirm;
  assert(req != null, "SAVE AND RUN must ask before it opens the shutter");
  eq(req?.title, "Start 20 frames on Andromeda Galaxy now?",
    "the question carries the number of frames and the target");
  const scratch = win.document.createElement("div");
  win.document.body.appendChild(scratch);
  const probe = createRoot(scratch);
  await act(async () => { probe.render(req?.body as any); });
  assert(/10 subs each of L, R/.test(scratch.textContent), "the filters are in the question");
  assert(/Guided/.test(scratch.textContent), "and whether the guider is in the loop");
  await act(async () => { probe.unmount(); });

  eq(posts("/api/flows/quick").length, before,
    "nothing is posted while the question is still on screen");

  await act(async () => { useStore.getState().resolveConfirm(false); });
  await settle();
  eq(posts("/api/flows/quick").length, before, "CANCEL must be a real no");

  click(tid("flow-quick-run"));
  await settle();
  await act(async () => { useStore.getState().resolveConfirm(true); });
  await settle();
  const sent = posts("/api/flows/quick");
  eq(sent.length, before + 1, "confirming posts exactly once");
  eq(sent[sent.length - 1].body?.run, true, "and it is the RUN one");
});

// ============================================ 8. a failed save keeps the sheet

await testAsync("a refused save toasts QUICK_SAVE_FAILED and does NOT close the sheet", async () => {
  route("flowQuick");
  seed("admin", ADMIN);
  await mount(FlowQuickSheet);
  const search = container.querySelector('[aria-label="Search the target catalog"]');
  typeInto(search, "M31");
  await settleMs(300);
  click(Array.from(container.querySelectorAll("button"))
    .find((b: any) => /Andromeda Galaxy/.test(b.textContent)) as any);
  await settle();

  const hash = win.location.hash;
  quickFails = "save";
  click(tid("flow-quick-save"));
  await settle();
  quickFails = null;

  eq(useStore.getState().toasts.at(-1)?.title, area.QUICK_SAVE_FAILED,
    "the failure is named from the shared constant");
  eq(win.location.hash, hash,
    "a failed save must keep the sheet - closing throws away the target, the ticks and the subs");
  assert(tid("quick-target") != null, "and the picked target is still there");
});

await testAsync("a save that succeeded and a run that did not is reported as the RUN failing", async () => {
  quickFails = "run";
  click(tid("flow-quick-run"));
  await settle();
  await act(async () => { useStore.getState().resolveConfirm(true); });
  await settle();
  quickFails = null;

  eq(useStore.getState().toasts.at(-1)?.title, area.QUICK_RUN_FAILED,
    "the server saved first, so this is not a failure to save");
  eq(useStore.getState().flows.ui.highlightId, "quick-1",
    "and the flow it DID save still has to reach the library");
});

// ============================================== 9. the viewer sees them locked

await testAsync("a viewer sees the wizard, locked, with the capability on screen", async () => {
  route("flowNew");
  seed("viewer", ["view.status", "view.preview"]);
  await mount(FlowNewSheet);

  assert(tid("session-flow-new") != null, "a viewer must see the same sheet, not an empty screen");
  const gen = tid("flow-new-generate");
  const blank = tid("flow-new-blank");
  eq(gen.getAttribute("aria-disabled"), "true", "GENERATE is honest-disabled");
  eq(gen.getAttribute("title"), "Generating a flow needs operator or admin access.",
    "and names the capability, from accessPhrase - never a hand-written role");
  eq(blank.getAttribute("title"), "Creating a flow needs operator or admin access.",
    "START BLANK names its own verb");
  assert(container.textContent.includes("Creating a flow needs operator or admin access."),
    "the reason has to be readable without a hover a touch screen cannot perform");

  const before = asked.length;
  click(gen);
  click(blank);
  await settle();
  eq(asked.length, before, "a locked wizard must not reach the network");
});

await testAsync("a viewer sees the quick sheet, locked, and fires nothing", async () => {
  route("flowQuick");
  seed("viewer", ["view.status", "view.preview"]);
  await mount(FlowQuickSheet);

  assert(tid("session-flow-quick") != null, "a viewer must see the same sheet");
  eq(all("quick-channel").length, 1, "and the same channel rows");
  const save = tid("flow-quick-save");
  eq(save.getAttribute("aria-disabled"), "true", "SAVE is honest-disabled");
  eq(save.getAttribute("title"), "Creating a flow needs operator or admin access.",
    "with the capability named");
  eq(tid("flow-quick-run").getAttribute("title"),
    "Creating a flow needs operator or admin access.",
    "the outer wall wins: a viewer cannot save, so naming the mount capability would be the wrong refusal");
  assert(container.textContent.includes("Creating a flow needs operator or admin access."),
    "the reason is on the screen, not only in a tooltip");

  const before = asked.length;
  const confirmBefore = useStore.getState().confirm;
  click(save);
  click(tid("flow-quick-run"));
  await settle();
  eq(asked.length, before, "a locked SAVE must not reach the network");
  eq(useStore.getState().confirm, confirmBefore, "and SAVE AND RUN must not raise a dialog");
});

await testAsync("an operator without control.mount can SAVE but not RUN", async () => {
  route("flowQuick");
  seed("syncer", ["view.status", "view.preview", "control.capture"]);
  await mount(FlowQuickSheet);
  const search = container.querySelector('[aria-label="Search the target catalog"]');
  typeInto(search, "M31");
  await settleMs(300);
  click(Array.from(container.querySelectorAll("button"))
    .find((b: any) => /Andromeda Galaxy/.test(b.textContent)) as any);
  await settle();

  eq(tid("flow-quick-save").getAttribute("aria-disabled"), null,
    "control.capture is enough to SAVE a flow");
  eq(tid("flow-quick-run").getAttribute("title"),
    "Starting a run needs operator or admin access.",
    "starting one is a separate capability and says so");
});

// ==================================== 10. the registry, and the helper copies

test("the area registers exactly the two sheet names the cutover expects", () => {
  eq(Object.keys(area.flowCreateSheets).sort().join(","), "flowNew,flowQuick",
    "T-R7-20 imports this map; a renamed key is a sheet that silently is not in it");
  eq(area.flowCreateSheets.flowNew, FlowNewSheet, "flowNew is the wizard");
  eq(area.flowCreateSheets.flowQuick, FlowQuickSheet, "flowQuick is the quick sheet");
});

test("the six helpers sky/sheets/quick.tsx needs are exported from create/index.ts", () => {
  for (const name of ["quickPayload", "targetFromEntry", "raHms", "decDms",
    "QUICK_SAVE_FAILED", "QUICK_RUN_FAILED"]) {
    assert((area as any)[name] !== undefined, `create/index.ts must export ${name}`);
  }
});

test("no drift: next's copy of the helpers agrees with the legacy original", () => {
  eq(mine.QUICK_SAVE_FAILED, legacy.QUICK_SAVE_FAILED, "QUICK_SAVE_FAILED");
  eq(mine.QUICK_RUN_FAILED, legacy.QUICK_RUN_FAILED, "QUICK_RUN_FAILED");
  eq(mine.OSC_LABEL, legacy.OSC_LABEL, "OSC_LABEL");
  eq(mine.DEFAULT_SUBS, legacy.DEFAULT_SUBS, "DEFAULT_SUBS");
  for (const h of [0, 0.712_305_5, 12.999_999, 23.999_999_9, -1.5]) {
    eq(mine.raHms(h), legacy.raHms(h), `raHms(${h})`);
  }
  for (const d of [0, 41.268_75, -0.001, -59.999_999, 89.999_999]) {
    eq(mine.decDms(d), legacy.decDms(d), `decDms(${d})`);
  }
  eq(JSON.stringify(mine.targetFromEntry(M31 as any)),
    JSON.stringify(legacy.targetFromEntry(M31 as any)), "targetFromEntry");
  const args = {
    target: mine.targetFromEntry(M31 as any),
    subs: 7,
    filters: ["L", "Ha"],
    exposures: { L: 60, Ha: 180, B: 0 },
    guided: false,
    run: true,
  };
  eq(JSON.stringify(mine.quickPayload(args)), JSON.stringify(legacy.quickPayload(args)),
    "quickPayload");
  eq(JSON.stringify(mine.quickPayload({ ...args, filters: [] })),
    JSON.stringify(legacy.quickPayload({ ...args, filters: [] })),
    "quickPayload, one channel");
});

// ============================================== 11. every class it emits exists

await testAsync("every nx-create-* class the sheets emit has a rule in create.css", async () => {
  interface NodeFsLike { readFileSync(p: string, enc: string): string }
  const nodeImport = (s: string): Promise<unknown> =>
    (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
  const fs = (await nodeImport("node:fs")) as NodeFsLike;
  const read = (rel: string): string => {
    const u = new URL(rel, import.meta.url);
    return fs.readFileSync(decodeURIComponent(u.pathname).replace(/^\/([A-Za-z]:)/, "$1"), "utf8");
  };
  const css = read("../create.css");
  const src = read("../wizard.tsx") + read("../quick.tsx");
  const used = new Set((src.match(/nx-create-[a-z-]+/g) ?? []));
  assert(used.size >= 8, `the sheets should emit the area's own classes, found ${used.size}`);
  for (const cls of used) {
    // A rule BOUNDARY, not a substring: `.nx-create-row` is a prefix of
    // `.nx-create-rowname`, so `includes` would pass over a deleted rule that
    // happens to share a stem - which is exactly what the sabotage run found.
    assert(new RegExp(`\.${cls}(?![a-zA-Z0-9_-])`).test(css),
      `create.css has no rule for .${cls}`);
  }
  // The area file must not restyle a primitive out from under `next.css`.
  assert(!/\.nx-(?!create-)[a-z]/.test(css),
    "create.css may only define its own nx-create-* classes; next.css belongs to T-R7-0");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`createDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
