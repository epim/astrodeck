// nowUnreadablePlan.test.tsx - SESSION / NOW's TONIGHT'S LIST with a saved
// plan file that no longer reads as a plan (#378). MOUNTED.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/now/__tests__/nowUnreadablePlan.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// GRADED ON THE SERVER'S RECORDED ANSWER, READ, NOT COPIED:
// server/tests/fixtures/plan_list_unreadable.json is a real `GET /api/plans`
// for one good plan and one hand-edited so that its first step's frame_type is
// 'Snapshot' (#334). The unreadable row carries `status: "unreadable"` and the
// server's reason, and none of a plan's numbers. Since S7 (#478) the recording
// also holds a file that is not JSON, listed last; `BAD` is the first
// unreadable row, the Snapshot plan's, and no pin here moved with it.
//
// The list is the phone's only way to start a saved plan (D-FU-3), so a row
// on it is a promise that RUN starts a night. Read as a plan, the unreadable
// file offered RUN, and RUN's first step (`GET /api/plans/{id}`) answers 422:
// a button that fails every time, on a row whose sub-line said "saved plan".
// Owner list item 9's rule for every reader of an unreadable file is that it
// is shown, never hidden, so the row stays on the list, with no verb, and its
// sub-line is the reason.
//
// NAMED MUTANTS, each run in a private copy of ui/ (scratchpad
// S5-PLANS-UI/mut), never in the shared tree; the observed failure is quoted
// at the test it turned red.
//   M1  "row read as a plan"          `planUnreadableReason` answers null
//   M1n "row read as a plan" (Now)    NowEmpty never marks an unreadable plan
//   M8  "tonight line kept"           the row keeps its tonight line
//   M9  "verb kept"                   the row keeps its RUN button
//   M11 "reason dropped" (Now)        the sub-line says unreadable and not why

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
// Everything answers false: the PHONE layout, which is the one this list is for.
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
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

// ------------------------------------------------------------- the fixture
const { readFileSync } = await import("node:fs");
const FIXTURE = JSON.parse(readFileSync(
  new URL("../../../../../../../server/tests/fixtures/plan_list_unreadable.json", import.meta.url),
  "utf8") as string);
const LIST: any[] = FIXTURE.list;
const GOOD = LIST.find((r) => r.status === undefined);
const BAD = LIST.find((r) => r.status === "unreadable");
if (!GOOD || !BAD) throw new Error("the fixture no longer holds one good and one unreadable row");
const REASON: string = BAD.unreadable;

// ------------------------------------------------------------- the fake rig
// The recorded 422 answers the unreadable plan's GET, as the server does, so a
// RUN pressed on it would fail here exactly as it does on the rig.
function answer(url: string): { status: number; body: unknown } {
  if (url === FIXTURE.get.url) return { status: FIXTURE.get.status, body: FIXTURE.get.body };
  if (url.includes("/api/plans")) return { status: 200, body: LIST };
  if (url.includes("/api/reports")) return { status: 200, body: [] };
  if (url.includes("/api/sessions")) return { status: 200, body: { sessions: [] } };
  if (url.includes("/api/flows")) return { status: 200, body: [] };
  return { status: 200, body: { ok: true } };
}
g.fetch = async (url: any) => {
  const a = answer(String(url));
  return {
    ok: a.status < 400, status: a.status, statusText: a.status < 400 ? "OK" : "Error",
    headers: { get: () => "application/json" },
    json: async () => a.body,
    text: async () => JSON.stringify(a.body),
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { NowEmpty } = await import("../NowEmpty");
const { planMeta } = await import("../runnableList");
const { TONIGHT_PER_FLOW } = await import("../tonightVerdict");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const rowOf = (id: string) => container.querySelector(`[data-runnable="plan"][data-runnable-id="${id}"]`) as any;
const buttonsIn = (el: any): string[] =>
  [...el.querySelectorAll("button")].map((b: any) => (b.textContent || "").trim());

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

async function mount(principal: unknown): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    const st = useStore.getState() as any;
    useStore.setState({
      principal,
      sequence: { state: "idle" },
      resumeArm: null,
      safety: { connected: true, streak: 0, reading: null },
      site: null,
      masters: [],
      toasts: [],
      status: {
        connected: { camera: { connected: true, name: "sim" } },
        looping: false, busy_lanes: [],
      },
      flows: {
        ...st.flows, cards: [], libraryLoaded: true, libraryError: null,
        record: null, run: { ...st.flows.run, phase: "idle" },
      },
    } as never);
  });
  await act(async () => { root.render(createElement(NowEmpty)); });
  await settle();
}

// ==================================================================== tests

await mount(OPERATOR);

await testAsync("precondition and control: the list is up, and the good plan's row RUNS", () => {
  assert(byId("now-empty") != null, "no now-empty: the fixture is wrong, not the component");
  assert(byId("now-runnables") != null, "no TONIGHT'S LIST card");
  const good = rowOf(GOOD.id);
  assert(good != null, "the good plan is missing from the list");
  const run = byId(`run-plan-${GOOD.id}`);
  assert(run != null, "the good plan has no RUN");
  eq(run.textContent, "RUN", "the good plan's verb:");
  eq(run.getAttribute("aria-disabled"), null, "the operator's RUN is locked - the rig in this test is wrong");
  const meta = good.querySelector(".nx-runnable-meta");
  eq(meta?.textContent, planMeta(GOOD), "the good plan's numbers:");
  assert(good.textContent.includes(TONIGHT_PER_FLOW), "the good plan lost its tonight line");
});

await testAsync("the unreadable file is on the list, never hidden", () => {
  // M1n "row read as a plan" (Now), observed (1/5 passed; the next two cases
  // and the viewer's failed with it). M1 and M9 failed the same four cases,
  // this one in the same words:
  //   x the unreadable file is on the list, never hidden: the row offers:
  //     expected
  //     got      RUN
  const row = rowOf(BAD.id);
  assert(row != null, "the unreadable plan is not on the list");
  eq(buttonsIn(row).join(" | "), "", "the row offers:");
});

await testAsync("it offers no RUN, and its sub-line is the reason as sent", () => {
  // M1 "row read as a plan", observed (1/5 passed; M1n and M9 the same):
  //   x it offers no RUN, and its sub-line is the reason as sent: RUN is
  //   offered on a file the server answers 422 for
  //     expected null
  //     got      [object HTMLButtonElement]
  // M11 "reason dropped" (Now), observed (2/5 passed; the whole-text case and
  // the viewer's reason line failed the same way):
  //   x it offers no RUN, and its sub-line is the reason as sent: the reason
  //   line:
  //     expected unreadable: fails validation: targets.0.steps.0.frame_type:
  //     frame type 'Snapshot' is not one of Light, Dark, Bias, Flat (in any case)
  //     got      unreadable
  const row = rowOf(BAD.id);
  assert(row != null, "precondition: the unreadable plan is not on the list");
  eq(byId(`run-plan-${BAD.id}`), null, "RUN is offered on a file the server answers 422 for");
  const why = byId(`plan-unreadable-${BAD.id}`);
  assert(why != null, "the row does not say why it has no RUN");
  eq(why.textContent, `unreadable: ${REASON}`, "the reason line:");
});

await testAsync("nothing on it reads as a plan: no numbers, no \"saved plan\", no tonight line", () => {
  // M1 and M1n, observed (the row as the list drew it before #378's client):
  //   x nothing on it reads as a plan: no numbers, no "saved plan", no
  //   tonight line: the row describes the file as a saved plan: NGC 7000
  //   Hasaved plantonight is worked out per flowRUN
  // M8 "tonight line kept", observed (4/5 passed; reason cut at "..."):
  //   x nothing on it reads as a plan: no numbers, no "saved plan", no
  //   tonight line: the row carries a tonight line for a plan nobody can
  //   read: NGC 7000 Haunreadable: fails validation: ...(in any case)tonight
  //   is worked out per flow
  // M9 "verb kept", observed (reason cut at "..."):
  //   x ... the row's whole text:
  //     expected NGC 7000 Haunreadable: fails validation: ...(in any case)
  //     got      NGC 7000 Haunreadable: fails validation: ...(in any case)RUN
  const row = rowOf(BAD.id);
  assert(row != null, "precondition: the unreadable plan is not on the list");
  const t = row.textContent as string;
  assert(!/undefined|NaN/.test(t), `the row reads fields the file does not have: ${t}`);
  assert(!t.includes("saved plan"), `the row describes the file as a saved plan: ${t}`);
  assert(!t.includes(TONIGHT_PER_FLOW), `the row carries a tonight line for a plan nobody can read: ${t}`);
  eq(t, `${BAD.name}unreadable: ${REASON}`, "the row's whole text:");
});

await testAsync("a viewer sees the same row and the same reason, and the good plan's RUN locked", async () => {
  await mount(VIEWER);
  const row = rowOf(BAD.id);
  assert(row != null, "a viewer must still see the file");
  eq(buttonsIn(row).join(" | "), "", "the viewer's unreadable row offers:");
  eq(byId(`plan-unreadable-${BAD.id}`)?.textContent, `unreadable: ${REASON}`,
    "the viewer's reason line:");
  const run = byId(`run-plan-${GOOD.id}`);
  assert(run != null, "control: the viewer's good row lost its RUN instead of locking it");
  eq(run.getAttribute("aria-disabled"), "true", "control: the viewer's RUN is not locked:");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`nowUnreadablePlan: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
