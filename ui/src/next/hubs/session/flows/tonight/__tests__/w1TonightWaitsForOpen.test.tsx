// w1TonightWaitsForOpen.test.tsx - TONIGHT resolves and draws only the flow
// its own route asked for, not whatever the canvas still has open (#553).
//
//   Run directly:  npx tsx src/next/hubs/session/flows/tonight/__tests__/w1TonightWaitsForOpen.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. The sheet's effect was `void flowsOpen(wantId)`, and
// `flowsOpen` swallows its own failure, leaving the record that was open
// before in place. Nothing compared `record.id` to `wantId` before reading
// `flowId`, fetching `/tonight` for it and drawing the answer - so a failed
// read of flow B under a Tonight link naming B resolved and drew flow A's
// night, read-only, but under a header claiming to be about B. The fix:
// `mine` (`wantId === "" || record?.id === wantId`), through `openFlowById`,
// gates the fetch AND every render branch; while it is false the sheet shows
// a waiting card instead.
//
// RE-PINNED IN BACKLOG WP-08 (#553, 2026-09-30): `mount()` now opens the
// sheet with `?open=`, not `?id=`. The sheet read `params.id`, which no
// caller in the running app ever sent - FlowStagesPhoneSheet's TONIGHT row
// (and every other row out of that sheet) sends `?open=`, the same param
// name FlowsCanvasHost, FlowsScreen and FlowFrameSheet all read - so every
// case in this file passed against a route shape the deep link it was
// written to catch never actually used. The cases themselves, and the
// defect each guards, are unchanged.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
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

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fake rig
const A_ID = "flow-a";
const B_ID = "flow-b";
const B_REASON = "no flow named flow-b";
/** Flow A's own cached refusal, already sitting in the store when the sheet
 *  mounts (as if it had been read once already) - the text the bug would leak
 *  under flow B's route if `mine` did not gate the render. */
const A_REFUSAL_TEXT = "A made-up refusal sentence that belongs only to flow A";

const A_RECORD = {
  id: A_ID, name: "M31 LRGB", folder: "My flows", tagline: "", readonly: false,
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [], edges: [] },
};
const B_RECORD = {
  id: B_ID, name: "M33 Ha", folder: "My flows", tagline: "", readonly: false,
  created_ts: 3, updated_ts: 4, last_run: null, last_result: "",
  graph: { nodes: [], edges: [] },
};
const B_TONIGHT = {
  ok: true, reason: "",
  night: { dusk_unix: 1000, dawn_unix: 2000, dark_start_unix: 1200, dark_end_unix: 1800 },
  flats: { start_unix: 1000, end_unix: 1100 },
  moon: { illumination: 0.2, rise_unix: null, set_unix: null },
  brief: "Flow B's own brief sentence.",
  targets: [], story: [],
  campaign: { is_campaign: false, has_pool: false, has_ledger: false, quota: null, note: "", members: [] },
};

/** When set, `GET /api/flows/flow-b` answers this status/body instead of 200. */
let bFails: { status: number; detail: string } | null = null;
/** While true, `GET /api/flows/flow-b` never resolves on its own - the read
 *  the "still loading" case holds open, released by draining this array. */
let holdB = false;
let holdBResolvers: ((v: unknown) => void)[] = [];
const asked: string[] = [];

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
  if (u === `/api/flows/${B_ID}` && method === "GET") {
    if (holdB) return new Promise((resolve) => { holdBResolvers.push(resolve); });
    return bFails ? reply(bFails.status, { detail: bFails.detail }) : reply(200, B_RECORD);
  }
  if (u === `/api/flows/${A_ID}` && method === "GET") return reply(200, A_RECORD);
  if (u === `/api/flows/${B_ID}/tonight` && method === "GET") return reply(200, B_TONIGHT);
  if (/\/tonight$/.test(u)) return reply(200, { ok: false, reason: "no such flow" });
  if (u === "/api/flows/compile") return reply(200, { plan: {}, structural: [], issues: [], unmapped: [] });
  return reply(200, { ok: true });
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FlowTonightSheet, TONIGHT_OPENING } = await import("../TonightSheet");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const settle = async (n = 8): Promise<void> => {
  for (let i = 0; i < n; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);

const ADMIN_CAPS = ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"];

/** Flow A open, with a cached (refused) tonight already in the store - the
 *  fixture the "leak" assertion needs. */
function seed(): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: "admin", email: null, caps: ADMIN_CAPS } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      status: {} as never,
      equipConnected: true,
      wsPhase: "up",
      resumeArm: null as never,
      flows: {
        ...s.flows,
        record: A_RECORD as never,
        tonight: { ok: false, reason: A_REFUSAL_TEXT } as never,
        tonightLoading: false,
        tonightError: null,
        calHealth: null,
        compiled: null,
        compiling: false,
        ui: { ...s.flows.ui, tonightTab: "timeline" },
      } as never,
    } as never);
  });
}

async function mount(id: string): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    // `?open=`, not `?id=` (backlog WP-08, #553): the same route param every
    // other sheet off this canvas carries, which is what FlowStagesPhoneSheet's
    // TONIGHT row actually sends. The sheet used to read `params.id` here,
    // which that row never sent, so the deep link this whole file tests never
    // named a flow in production even while this suite, naming it `id`, passed.
    root.render(createElement(FlowTonightSheet as any, { params: { open: id }, depth: 0 }));
  });
  await settle();
}

// ------------------------------------------------------------------ the cases

await test(
  "a failed read of flow B under B's Tonight link draws none of flow A's cached night",
  async () => {
    bFails = { status: 404, detail: B_REASON };
    try {
      seed();
      await mount(B_ID);

      assert(tid("session-flow-tonight") != null, "the sheet did not render");
      assert(asked.includes(`GET /api/flows/${B_ID}`), "premise: flow B's read was attempted");
      eq(useStore.getState().flows.record?.id, A_ID,
        "premise: the failed read left flow A open, as flowsOpen does");

      assert(tid("tonight-open-failed") != null,
        "no waiting/failed card: the sheet drew something in its place");
      assert(!/A made-up refusal sentence/.test(container.textContent),
        `flow A's cached refusal leaked onto a screen the link named flow B for: ${container.textContent}`);
      assert(new RegExp(B_REASON).test(container.textContent),
        `the failed card did not carry the server's own reason: ${container.textContent}`);
      assert(!asked.some((a) => /\/tonight$/.test(a)),
        `a /tonight read was made for the wrong flow while waiting: ${asked.join(", ")}`);
    } finally {
      bFails = null;
    }
  },
);

await test(
  "while the read is still out, the sheet says so and fetches nothing yet",
  async () => {
    holdB = true;
    try {
      seed();
      await mount(B_ID);

      assert(tid("tonight-opening") != null, "no loading card while the open is still out");
      assert(new RegExp(TONIGHT_OPENING).test(container.textContent),
        `the loading card did not say ${JSON.stringify(TONIGHT_OPENING)}: ${container.textContent}`);
      assert(!/A made-up refusal sentence/.test(container.textContent),
        "flow A's cached refusal leaked while flow B's open was still out");
      eq(useStore.getState().flows.record?.id, A_ID, "flow B has not landed yet, so A is still open");
    } finally {
      holdB = false;
      holdBResolvers.forEach((resolve) => resolve({
        ok: true, status: 200, statusText: "OK",
        headers: { get: () => "application/json" },
        json: async () => B_RECORD,
      }));
      holdBResolvers = [];
      await settle();
    }
  },
);

await test(
  "control: a read that lands opens flow B for real and resolves its own tonight",
  async () => {
    bFails = null;
    seed();
    await mount(B_ID);

    eq(useStore.getState().flows.record?.id, B_ID, "flow B must actually open when its read lands");
    assert(tid("tonight-open-failed") == null, "no failed card should remain once B has landed");
    assert(tid("tonight-opening") == null, "no loading card should remain once B has landed");
    assert(asked.includes(`GET /api/flows/${B_ID}/tonight`), "flow B's own tonight must be fetched");
    assert(/M33 Ha/.test(container.textContent), "the sheet must name flow B once it has landed");
  },
);

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w1TonightWaitsForOpen.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
