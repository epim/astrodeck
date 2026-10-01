// w5DisarmedWarning.test.tsx - WP-65, the UI half of WP-31 (a): the #/next
// Now empty state names every session a run start or resume disarmed.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/now/__tests__/w5DisarmedWarning.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// BACKLOG RULING D-04 (owner-approved 2026-09-30): "Keep the singleton. WP-31
// adds a warning line naming each disarmed session and a `disarmed` list in
// the start response, and WP-65 (wave 5) shows it in the UI." #595 is the
// incident the ruling answers: starting a run once silently took the owner's
// explicitly-armed mosaic's auto-resume with it, and nothing on screen said
// so until a manual read of `/api/sessions` caught it, five minutes late.
//
// THIS SCREEN MAKES TWO OF THE FIVE CALLS THAT CAN NOW ANSWER `disarmed`
// (CONTEXT in the work package): `POST /api/sequence/start` (`runPlan`) and
// `POST /api/sessions/{id}/resume` (`resumeArmed`, via `resumeSession`). The
// other three - `POST /api/flows/{id}/run`, `POST /api/sequence/recover` and
// `PATCH /api/sessions/{id}` - are not called from this file, so they are out
// of scope here (see this WP's return for why the flow-run path is blocked
// on a file this package does not own).
//
// NAMED MUTANTS, each a one-line change under which this file was run from a
// byte backup of NowEmpty.tsx and restored byte-identical afterwards (sha256
// compared, and grepped to confirm the mutant text was gone).
//
//   M-resume: `if (disarmed && disarmed.length > 0)` -> `if (false && ...)`
//             inside `resumeArmed`'s `.then`. Observed, 3/4 passed:
//     x RESUME that disarms another session shows a warning naming it
//       (#595, D-04): no new disarmed-warning toast after a resume that
//       disarmed one: []
//
//   M-start:  `if (res.disarmed && res.disarmed.length > 0)` -> `if (false
//             && ...)` inside `runPlan`'s try. Observed, 3/4 passed:
//     x RUN on a plan that disarms another session shows a warning naming it
//       (#595, D-04): no new disarmed-warning toast after a start that
//       disarmed one: []

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

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];

// A made-up plan and a made-up flow, never the real site (project rule: no
// real latitude, longitude or label in test fixtures).
const PLAN_ROW = {
  id: "p1", name: "Comet watch", frames: 10, integration_min: 30, targets: 1, mtime: 1,
};
const PLAN = {
  name: "Comet watch",
  guide: false, dither_every: 0, dither_pixels: null, autofocus_every: 0,
  cool_to: null, cool_timeout_s: 600, apply_filter_offsets: null,
  refocus_on_temp_delta_c: null, meridian_flip: false, recover_guiding: null,
  hfr_reject_factor: null, park_when_done: false, warm_cooler_when_done: false,
  targets: [{
    id: "pt1", name: "NGC 6960", ra_hours: 20.76, dec_deg: 30.72,
    center: true, autofocus_first: false, calibration: false,
    steps: [{
      id: "ps1", filter: "L", exposure_s: 180, gain: 100, offset: 30,
      binning: 1, count: 24, frame_type: "Light",
    }],
  }],
};
function card(id: string, name: string): any {
  return {
    id, name, folder: "", tagline: "", readonly: false, stages: 2, wires: 1,
    last_run: null, last_result: null, updated_ts: 1,
  };
}

/** `resumeArmed` names the ARMED session (`resumeArm.armed.id`), which is
 *  this fixture's "sess-9" - never the row's own flow id. */
const ARMED = {
  id: "sess-9", name: "Orion flow run", owed: 5, accepted: 0, total: 5,
  origin: "flow", origin_id: "f1",
};

// Each endpoint answers `disarmed` only on its FIRST call, so the same
// mounted screen can also prove the negative: a later answer with no
// `disarmed` key adds no second warning (guards against "always warn").
let resumeCalls = 0;
let startCalls = 0;

function answer(url: string, method: string): any {
  if (method === "POST" && url.includes("/api/sessions/sess-9/resume")) {
    resumeCalls++;
    const out: any = { resumed: true, remaining: 5 };
    if (resumeCalls === 1) out.disarmed = [{ id: "sess-2", name: "Veil mosaic" }];
    return out;
  }
  if (method === "POST" && url.includes("/api/sequence/start")) {
    startCalls++;
    const out: any = { started: true, frames: 10 };
    if (startCalls === 1) out.disarmed = [{ id: "sess-3", name: "Deep sky log" }];
    return out;
  }
  if (url.includes("/api/reports")) return [];
  if (url.includes("/api/sessions")) return { sessions: [] };
  if (url.includes("/api/plans/p1")) return PLAN;
  if (url.includes("/api/plans")) return [PLAN_ROW];
  if (url.includes("/api/flows/f1/tonight")) return { ok: false, reason: "No observatory site is set." };
  if (url.includes("/api/flows")) return [];
  return { ok: true };
}

g.fetch = async (url: any, init: any) => {
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  const u = String(url);
  asks.push({ url: u, method, body });
  const out = answer(u, method);
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => out, text: async () => JSON.stringify(out),
  };
};
const posts = (path: string) => asks.filter((a) => a.method === "POST" && a.url.includes(path));

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, Fragment, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { NowEmpty } = await import("../NowEmpty");
const { ConfirmCard } = await import("../../../../shell/ConfirmCard");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq_<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg}: expected ${String(want)}, got ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const click = async (el: any) => {
  assert(el != null, "click: the element is not there - the fixture is wrong, not the component");
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"],
};

/** The warning toasts naming a disarmed session - never the resume/run result
 *  toast ("Session resumed", "'Comet watch' ..."), which this file also
 *  checks is NOT where the names end up. */
function disarmedToasts(): Array<{ title: string; detail?: string }> {
  const toasts = (useStore.getState() as any).toasts as Array<{ level: string; title: string; detail?: string }>;
  return toasts.filter((t) => t.level === "warning" && /disarmed/i.test(t.title));
}

function seed(over: Record<string, unknown> = {}): void {
  const st = useStore.getState() as any;
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    sequence: { state: "idle" },
    resumeArm: { armed: ARMED, hold: null },
    safety: { connected: true, streak: 0, reading: null },
    site: null,
    masters: [],
    toasts: [],
    // The full shape `buildPreflight` reads (`runPlan`'s pre-flight, lib/
    // preflight.ts): a telescope left out of `connected` blocks the MOUNT row
    // and the confirm never reaches START, so this mirrors nowDom.test.tsx's
    // proven-working fixture rather than a trimmed one.
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim mount" } },
      looping: false,
      busy_lanes: [],
      disk: { free_gb: 210, low: false, critical: false },
      camera: {
        temperature: -10, can_cool: true, width: 100, height: 100, max_gain: 100,
        cooler: { on: true, power: 42, target_c: -10, at_target: true, can_report_power: true },
      },
      meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
    },
    flows: {
      ...st.flows, cards: [card("f1", "Orion mosaic")], libraryLoaded: true, libraryError: null,
      record: null, run: { ...st.flows.run, phase: "idle" },
    },
    ...over,
  } as never);
}

await act(async () => { root.render(createElement("div")); });
await act(async () => { seed(); });
await act(async () => {
  root.render(createElement(Fragment, null, createElement(NowEmpty), createElement(ConfirmCard)));
});
await settle();
await settle();

await testAsync(
  "precondition: the empty state is up, armed, and carries the RESUME row",
  async () => {
    assert(byId("now-empty") != null, "no now-empty: the fixture is wrong, not the component");
    assert(byId("run-flow-f1") != null, "no RESUME row for the armed flow");
    eq_(byId("run-flow-f1").textContent, "RESUME", "the armed row's verb");
  },
);

// ------------------------------------------------------------- 1. RESUME
await testAsync(
  "RESUME that disarms another session shows a warning naming it (#595, D-04)",
  async () => {
    const before = disarmedToasts().length;
    await click(byId("run-flow-f1"));

    eq_(posts("/api/sessions/sess-9/resume").length, 1, "resume posts:");
    const warnings = disarmedToasts();
    assert(warnings.length === before + 1,
      `no new disarmed-warning toast after a resume that disarmed one: ${JSON.stringify(warnings)}`);
    const w = warnings[warnings.length - 1];
    assert((w.detail ?? "").includes("Veil mosaic"),
      `the warning does not name the disarmed session: "${w.detail}"`);

    // The ordinary resume toast still fires, unreplaced by the warning.
    const toasts = (useStore.getState() as any).toasts as Array<{ title: string }>;
    assert(toasts.some((t) => t.title === "Session resumed"),
      "the resume's own success toast is missing");
  },
);

await testAsync(
  "a SECOND resume with no disarmed key adds no second warning (control)",
  async () => {
    const before = disarmedToasts().length;
    await click(byId("run-flow-f1"));

    eq_(posts("/api/sessions/sess-9/resume").length, 2, "resume posts:");
    eq_(disarmedToasts().length, before,
      "a resume that disarmed nobody still produced a disarmed-warning toast");
  },
);

// -------------------------------------------------------------- 2. RUN plan
await testAsync(
  "RUN on a plan that disarms another session shows a warning naming it (#595, D-04)",
  async () => {
    await act(async () => { seed({ resumeArm: null }); });
    await settle();
    const before = disarmedToasts().length;

    await click(byId("run-plan-p1"));
    await click(byId("confirm-yes"));

    eq_(posts("/api/sequence/start").length, 1, "sequence starts:");
    const warnings = disarmedToasts();
    assert(warnings.length === before + 1,
      `no new disarmed-warning toast after a start that disarmed one: ${JSON.stringify(warnings)}`);
    const w = warnings[warnings.length - 1];
    assert((w.detail ?? "").includes("Deep sky log"),
      `the warning does not name the disarmed session: "${w.detail}"`);
  },
);

act(() => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`w5DisarmedWarning: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
