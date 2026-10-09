// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6NowRetriesATimeout.test.tsx -- the Now screen's one-shot reads ask a
// timeout again by itself, and its tonight verdicts share ONE retry budget and
// stop at the first failure that survives it (#859).
//
//   Run directly:  npx tsx src/next/hubs/session/now/__tests__/p6NowRetriesATimeout.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Each `GET /api/flows/{id}/tonight` is one astropy pass per target on the
// rig's CPU (TONIGHT_RESOLVE_CAP). Before #859 a timeout was cached as "tonight
// could not be read" for the life of the page; retried per flow it would have
// been up to 20 heavy reads through a stall. Harness from
// nowRunOpensThePressedFlow.test.tsx with five flows; `/tonight` is scripted
// per id and a timeout is thrown the way a browser's AbortSignal does. The
// retry wait runs at once.
//
// Named mutants (each turns the case beside it red):
//   (a)  useReportList calls listReports without retryTransient (the error and
//        RETRY show at once).
//   (b)  tonightCache.set restored in the transient-failure path.
//   V1   delete the `return` after a transient failure (flows 2-5 each asked).
//   V1b  `delaysMs: LOAD_RETRY_DELAYS_MS` without `.slice(used)`.
//   V2   drop `phase.current = "failed"` (no re-ask).
// Fix round 1 (each a re-ask or a retry no case pinned before):
//   NL   delete useFlowLibrary's useRetryOnReturn (sessionData.ts): a library
//        load that settled as failed is not asked again on tab-visible.
//   UX1  drop the `!libraryLoading` guard in useFlowLibrary's mount effect:
//        Now and the compact SessionColumn start two loads (two GETs).
//   UX7  usePlanLibrary calls listPlans directly (no retryTransient): one
//        timeout shows "Saved plans are missing from this list".
//   NP   delete usePlanLibrary's useRetryOnReturn: a failed plans read is not
//        asked again on tab-visible.
//   UX11 delete useReportList's useRetryOnReturn: a failed report list is not
//        asked again on tab-visible.

/* eslint-disable @typescript-eslint/no-explicit-any */
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
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const IDS = ["f1", "f2", "f3", "f4", "f5"];
const card = (id: string, lastRun: number): any => ({
  id, name: `FLOW ${id}`, folder: "My flows", tagline: "", readonly: false,
  stages: 2, wires: 1, last_run: lastRun, last_result: "ok", updated_ts: 2,
});
const ANSWER_REASON = "No observatory site is set.";

let asked: string[] = [];
/** How the n-th `/tonight` of flow `id` (0-based) answers: "timeout" or "answer". */
let tonightStep: (id: string, n: number) => "timeout" | "answer" = () => "answer";
/** How the n-th `/api/reports` (0-based) answers. */
let reportsStep: (n: number) => "timeout" | "answer" = () => "answer";
/** How the n-th `/api/flows` and `/api/plans` (0-based) answer. */
let flowsStep: (n: number) => "timeout" | "answer" = () => "answer";
let plansStep: (n: number) => "timeout" | "answer" = () => "answer";

const timedOut = () => { throw new DOMException("The operation timed out.", "TimeoutError"); };
g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  const reply = (status: number, data: unknown) => ({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  const tn = /^\/api\/flows\/([^/]+)\/tonight$/.exec(u);
  if (tn) {
    const n = asked.filter((a) => a === `GET ${u}`).length - 1;
    if (tonightStep(tn[1], n) === "timeout") timedOut();
    return reply(200, { ok: false, reason: ANSWER_REASON });
  }
  if (u.startsWith("/api/reports")) {
    const n = asked.filter((a) => a.startsWith("GET /api/reports")).length - 1;
    if (reportsStep(n) === "timeout") timedOut();
    return reply(200, []);
  }
  if (u === "/api/flows") {
    const n = asked.filter((a) => a === "GET /api/flows").length - 1;
    if (flowsStep(n) === "timeout") timedOut();
    return reply(200, IDS.map((id, i) => card(id, 100 - i)));
  }
  if (u.includes("/api/flows")) return reply(200, []);
  if (u.startsWith("/api/plans")) {
    const n = asked.filter((a) => a.startsWith("GET /api/plans")).length - 1;
    if (plansStep(n) === "timeout") timedOut();
    return reply(200, []);
  }
  if (u.includes("/api/sequence/recoverable")) return reply(200, { recoverable: false });
  return reply(200, { ok: true });
};

const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { FLOWS_INIT } = await import("../../../../../components/flows/flowsSlice");
const { setRetrySleepForTests } = await import("../../../../../lib/retryLoad");
const { resetTonightVerdictsForTests, NowEmpty } = await import("../NowEmpty");
const { TONIGHT_UNCHECKED } = await import("../tonightVerdict");
const { NowScreen } = await import("../NowScreen");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq(got: unknown, want: unknown, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
const settle = async (n = 12) => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount", "control.guide"],
};
const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const verdictOf = (id: string): string =>
  (container.querySelector(`[data-runnable-id="${id}"] .nx-runnable-verdict`)?.textContent ?? "") as string;
const tonightGets = (id?: string) =>
  asked.filter((a) => /^GET \/api\/flows\/[^/]+\/tonight$/.test(a)
    && (!id || a === `GET /api/flows/${id}/tonight`)).length;

setRetrySleepForTests(async () => {});

async function mount(opts: {
  resetCache?: boolean;
  /** Fields over the seeded flows slice. */
  flows?: Record<string, unknown>;
  /** Render this in place of the Now screen. */
  element?: unknown;
} = {}): Promise<void> {
  await act(async () => { root.render(null); });
  if (opts.resetCache !== false) resetTonightVerdictsForTests();
  asked = [];
  useStore.setState({
    principal: OPERATOR,
    authGate: "open",
    equipConnected: true,
    wsPhase: "up",
    wsConnected: true,
    telemetryStale: false,
    toasts: [],
    confirm: null,
    resumeArm: null,
    site: null,
    masters: [],
    safety: { connected: true, streak: 0, reading: { is_safe: true, reason: "", source: "sim", stale: false, ts: Date.now() / 1000 } },
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim mount" } },
      looping: false, busy_lanes: [],
    },
    sequence: { state: "idle" },
    flows: {
      ...FLOWS_INIT,
      ui: { ...FLOWS_INIT.ui },
      cards: IDS.map((id, i) => card(id, 100 - i)),
      libraryLoaded: true, libraryError: null,
      ...opts.flows,
    },
  } as never);
  await act(async () => { root.render((opts.element ?? createElement(NowScreen)) as any); });
  await settle();
  assert(byId("now-empty") != null, "no now-empty: the fixture is wrong, not the component");
  if (opts.flows || opts.element) return;
  assert(container.querySelector(`[data-runnable-id="f5"]`) != null,
    "the fifth flow's row is missing: the verdict counts below would be vacuous");
}
const flowsGets = () => asked.filter((a) => a === "GET /api/flows").length;
const plansGets = () => asked.filter((a) => a.startsWith("GET /api/plans")).length;
const reportsGets = () => asked.filter((a) => a.startsWith("GET /api/reports")).length;
const nowText = (): string => (byId("now-empty")?.textContent ?? "") as string;
const tabComesBack = async () => {
  await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
  await settle();
};

await test("(a) a report list that times out and then answers says it is retrying, then shows no RETRY", async () => {
  // The Now screen has a second, one-shot reader of /api/reports (useCampaign,
  // not part of #859), so the list is down until the retry's wait is
  // released: whichever reader asks first, NowEmpty's own first read times out.
  let down = true;
  reportsStep = () => (down ? "timeout" : "answer");
  tonightStep = () => "answer";
  let release!: () => void;
  const held = new Promise<void>((r) => { release = r; });
  setRetrySleepForTests(() => held);
  try {
    await mount();
    assert(/No answer from the rig yet\. Asking again by itself \(try 2 of 4\)\./.test(byId("now-empty").textContent),
      "the report line does not say it is asking again");
    assert(byId("now-reports-retry") == null, "RETRY shows while the app is already asking again");
    down = false;
    release();
    await settle();
    assert(byId("now-reports-retry") == null, "one timed-out read left RETRY on the report line");
    assert(!/Couldn.t read the list of session reports/.test(byId("now-empty").textContent),
      "the report error shows after the retry answered");
  } finally {
    reportsStep = () => "answer";
    setRetrySleepForTests(async () => {});
  }
});

await test("V1 through a total stall the verdicts cost four reads, all for the first flow", async () => {
  tonightStep = () => "timeout";
  await mount();
  eq(tonightGets(), 4, "tonight reads in all");
  eq(tonightGets("f1"), 4, "all of them for the first flow");
  assert(/^tonight could not be read: /.test(verdictOf("f1")),
    `the first flow says its read did not land: "${verdictOf("f1")}"`);
  for (const id of ["f2", "f3", "f4", "f5"]) {
    eq(verdictOf(id), TONIGHT_UNCHECKED, `flow ${id} was not asked and says so`);
  }
});

await test("V2 the tab coming back re-asks the stopped loop, and it goes on past the first flow", async () => {
  // Continues from V1's mount: the loop stopped on f1.
  tonightStep = () => "answer";
  asked = [];
  await act(async () => { document.dispatchEvent(new win.Event("visibilitychange")); });
  await settle();
  assert(tonightGets("f1") === 1, `flow f1 was not asked again (asked ${tonightGets("f1")})`);
  assert(tonightGets("f2") === 1, `the loop did not go on to flow f2 (asked ${tonightGets("f2")})`);
  assert(verdictOf("f1").includes(ANSWER_REASON), `flow f1 shows "${verdictOf("f1")}"`);
});

await test("(b) a transient failure is not cached: a remount asks again and shows the answer", async () => {
  tonightStep = () => "timeout";
  await mount();
  assert(/^tonight could not be read: /.test(verdictOf("f1")), "precondition: f1 failed");
  tonightStep = () => "answer";
  await mount({ resetCache: false });
  assert(tonightGets("f1") === 1, `the remount did not ask f1 again (asked ${tonightGets("f1")})`);
  assert(verdictOf("f1").includes(ANSWER_REASON),
    `the remount showed "${verdictOf("f1")}", not the answer`);
});

await test("V1b the budget is shared: retries spent on one flow are gone for the next", async () => {
  tonightStep = (id, n) => (id === "f1" ? (n < 3 ? "timeout" : "answer") : "timeout");
  await mount();
  eq(tonightGets("f1"), 4, "f1: three timeouts and the answer");
  eq(tonightGets("f2"), 1, "f2 gets no retries: the budget is spent");
  eq(tonightGets(), 5, "five tonight reads in all, then the loop stops");
  assert(verdictOf("f1").includes(ANSWER_REASON), `f1 shows "${verdictOf("f1")}"`);
});

await test("NL Now's library: a load that settled as failed is asked once more when the tab comes back", async () => {
  tonightStep = () => "answer";
  flowsStep = () => "timeout";
  try {
    await mount({ flows: { libraryLoaded: false, cards: [] } });
    eq(flowsGets(), 4, "precondition: the mount's load tried four times");
    const f = useStore.getState().flows;
    assert(!!f.libraryError && !f.libraryLoading, "precondition: the load settled as failed");
    flowsStep = () => "answer";
    asked = [];
    await tabComesBack();
    eq(flowsGets(), 1, "the tab coming back asks the library once");
    assert(container.querySelector(`[data-runnable-id="f5"]`) != null, "and the rows come back");
  } finally {
    flowsStep = () => "answer";
  }
});

await test("UX1 Now and the compact SessionColumn mounted together load the library once", async () => {
  const pair = createElement(Fragment, null,
    createElement(NowEmpty), createElement(NowEmpty, { compact: true }));
  await mount({ flows: { libraryLoaded: false, cards: [] }, element: pair });
  eq(flowsGets(), 1, "two instances mounted in one commit started one load");
});

await test("UX7 a plans read that times out once and then answers never says plans are missing", async () => {
  plansStep = (n) => (n === 0 ? "timeout" : "answer");
  try {
    await mount();
    eq(plansGets(), 2, "the plans read was asked again by itself");
    assert(!/Saved plans are missing/.test(nowText()),
      "one timed-out plans read said the saved plans are missing");
  } finally {
    plansStep = () => "answer";
  }
});

await test("NP a plans read that failed is asked once more when the tab comes back", async () => {
  plansStep = () => "timeout";
  try {
    await mount();
    eq(plansGets(), 4, "precondition: four tries");
    assert(/Saved plans are missing/.test(nowText()), "precondition: the plans line says they are missing");
    plansStep = () => "answer";
    asked = [];
    await tabComesBack();
    eq(plansGets(), 1, "the tab coming back asks the plans once");
    assert(!/Saved plans are missing/.test(nowText()), "the plans line outlived the answer");
  } finally {
    plansStep = () => "answer";
  }
});

await test("UX11 a report list that failed is asked once more when the tab comes back", async () => {
  reportsStep = () => "timeout";
  try {
    await mount();
    assert(byId("now-reports-retry") != null, "precondition: the report list settled as failed, with RETRY");
    reportsStep = () => "answer";
    asked = [];
    await tabComesBack();
    eq(reportsGets(), 1, "the tab coming back asks the report list once");
    assert(byId("now-reports-retry") == null, "the report error outlived the answer");
  } finally {
    reportsStep = () => "answer";
  }
});

await act(async () => { root.render(null); });
await act(async () => { root.unmount(); });
setRetrySleepForTests(null);

console.log(`p6NowRetriesATimeout.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
