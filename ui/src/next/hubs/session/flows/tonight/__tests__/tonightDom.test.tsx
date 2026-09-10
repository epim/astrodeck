// tonightDom.test.tsx - the TONIGHT sheet, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/session/flows/tonight/__tests__/tonightDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. IT RENDERED, FROM THE SERVER'S OWN TIMES. A timeline that silently drew
//      nothing would let every assertion below pass over a blank page, so the
//      first test names the sheet marker AND a mark placed from the stubbed
//      dusk/dawn - the DUSK label and a target block.
//   2. FOUR VIEWS OF ONE PAYLOAD ARE NOT FOUR QUESTIONS. Switching tabs must
//      not re-ask `/tonight`. The legacy panel refetched on OPEN; a rebuild
//      that refetched on TAB would hit the rig three times for one reading and,
//      worse, could show two different nights in two tabs.
//   3. THE CAP IS PART OF THE PRODUCT. The route is gated on
//      `view.site_derived` because an audit recovered the observatory to 2.9 km
//      from three viewer-legal requests. A role without it sees the sheet, sees
//      the sentence, and NOTHING leaves the browser.
//   4. A TRANSPORT FAILURE IS NOT A REFUSAL. `tonightError` is the request not
//      arriving; it renders the server's own message and a RETRY that actually
//      re-asks.
//   5. HONESTY ABOUT A MISSING INSTANT. With `dusk_unix: null` the parked line
//      keeps the true half of the sentence and drops the clock - an operator
//      cannot tell an invented dusk from a measured one.
//   6. `banked === null` IS NOT ZERO. "not counted" and "0/45 cycles" are two
//      different nights and must not render alike.
//
// Convention: shell-and-tests.md section 4.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The sheet's root imports its area stylesheet. Node has no idea what a `.css`
// file is, so a synchronous load hook answers with an empty module - the same
// stub `shellDom.test.tsx` and `hubBoundary.test.tsx` install.
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
  matches: false,
  addEventListener() {}, removeEventListener() {},
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
// Real-shaped instants: a night that runs 20:00 -> 05:00 local on the box that
// runs this file, so the geometry has a span to lay marks across whatever the
// timezone is.
const DAY = Math.floor(Date.now() / 86400000) * 86400;
const DUSK = DAY + 20 * 3600;
const DARK_START = DUSK + 40 * 60;
const DARK_END = DAY + 28 * 3600;
const DAWN = DARK_END + 40 * 60;

const TONIGHT_OK = {
  ok: true,
  reason: "",
  night: {
    dusk_unix: DUSK, dawn_unix: DAWN,
    dark_start_unix: DARK_START, dark_end_unix: DARK_END,
  },
  flats: { start_unix: DUSK, end_unix: DUSK + 20 * 60 },
  moon: { illumination: 0.71, rise_unix: DUSK + 2 * 3600, set_unix: null },
  brief: "Waits for dusk, shoots 12 L on NGC 7331, then flats.",
  targets: [
    {
      label: "NGC 7331",
      window: { start_unix: DARK_START, end_unix: DARK_END },
      curve: [[DARK_START, 41], [DARK_START + 3600, 58], [DARK_END, 33]],
      meridian_flip_unix: DARK_START + 2 * 3600,
    },
  ],
  story: [
    { t_unix: DUSK, label: "", msg: "Dusk. The dome opens and the flats window starts.", tone: "text" },
    { t_unix: null, label: "BUDGET", msg: "3.1 h of the 12 h L goal is already banked.", tone: "dim" },
    { t_unix: null, label: "\u2014", msg: "No horizon polyline is set for this site.", tone: "warn" },
  ],
  campaign: {
    is_campaign: true, has_pool: true, has_ledger: true, quota: 45,
    note: "Shortfall first: the engine re-reads the ledger at each dusk.",
    members: [
      { name: "NGC 7331", banked: 23, quota: 45, done: false, pct: 51 },
      { name: "NGC 604", banked: null, quota: 45, done: false, pct: null },
    ],
  },
};

/** The same night with NO dusk. `tonight.py` answers this whenever the site is
 *  unset or the sun never reaches the twilight angle, and it is the payload the
 *  honesty test needs. */
const TONIGHT_NO_DUSK = {
  ...TONIGHT_OK,
  night: { dusk_unix: null, dawn_unix: null, dark_start_unix: null, dark_end_unix: null },
};

const CAL_HEALTH = {
  rows: [
    { kind: "dark", label: "DARK", summary: "300s bin1 -10C", quantity: "0/20", verdict: "MISSING" },
    { kind: "flat", label: "FLAT", summary: "L bin1", quantity: "20/20", verdict: "OK" },
  ],
  planned: true,
  counts_masters_only: true,
  assumed: { offset: 30, temp_c: -10 },
};

const FLOW_RECORD = {
  id: "quick-m31", name: "Quick \u00b7 M31 LRGB", folder: "My flows",
  tagline: "12 subs each of L, R, G, B", readonly: false,
  graph: { nodes: [], edges: [] },
  last_run: null, last_result: "", updated_ts: 1_757_000_500,
};

const asked: { url: string; method: string }[] = [];
/** Flipped by the transport-failure test so the SAME url starts failing. */
let tonightFails = false;
/** Which payload `/tonight` answers with, so one file can cover both nights. */
let tonightBody: unknown = TONIGHT_OK;

g.fetch = async (url: string, init?: { method?: string }) => {
  const method = init?.method ?? "GET";
  asked.push({ url, method });
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });

  if (/\/tonight$/.test(url)) {
    if (tonightFails) {
      return {
        ok: false, status: 503, statusText: "Service Unavailable",
        headers: { get: () => "application/json" },
        json: async () => ({ detail: "the rig is not answering" }),
      };
    }
    return ok(tonightBody);
  }
  if (url.startsWith("/api/calibration/health")) return ok(CAL_HEALTH);
  if (/^\/api\/flows\/[^/]+$/.test(url)) return ok(FLOW_RECORD);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../../store");
const { FlowTonightSheet } = await import("../TonightSheet");
const { TONIGHT_LOCK_REASON, resumesLine, storyStamp } = await import("../tonightModel");

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
const settle = async () => {
  for (let i = 0; i < 6; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const tid = (t: string): any => container.querySelector(`[data-testid="${t}"]`);
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
/** Every `/tonight` GET this file has seen, which is the number under test in
 *  the refetch and viewer cases. */
const tonightGets = () => asked.filter((a) => /\/tonight$/.test(a.url)).length;
const tabButton = (label: string): any => {
  const opts = Array.from(
    container.querySelectorAll('[data-testid="tonight-tab"] [role="radio"]'),
  ) as any[];
  const hit = opts.find((o) => (o.textContent as string).includes(label));
  if (!hit) throw new Error(`no ${label} tab rendered`);
  return hit;
};

const ADMIN = [
  "view.status", "view.preview", "view.site_derived", "control.capture", "control.mount",
];

function seed(caps: string[], extra: Record<string, unknown> = {}): void {
  act(() => {
    const s = useStore.getState();
    useStore.setState({
      principal: { role: caps.includes("view.site_derived") ? "operator" : "viewer", email: null, caps } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      status: {} as never,
      equipConnected: true,
      wsPhase: "up",
      resumeArm: null as never,
      flows: {
        ...s.flows,
        record: FLOW_RECORD as never,
        tonight: null,
        tonightLoading: false,
        tonightError: null,
        calHealth: null,
        compiled: null,
        compiling: false,
        ui: { ...s.flows.ui, tonightTab: "timeline" },
      } as never,
      ...extra,
    } as never);
  });
}

async function mount(): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  await act(async () => {
    root.render(createElement(FlowTonightSheet as any, { params: {}, depth: 0 }));
  });
  await settle();
}

// ============================================ 1. it rendered, from real times

tonightBody = TONIGHT_OK;
seed(ADMIN);
await mount();

test("precondition: the sheet rendered and the timeline drew the server's night", () => {
  assert(tid("session-flow-tonight") != null,
    "no session-flow-tonight marker - the fixture is wrong, not the sheet");
  assert(tid("tonight-tab") != null, "the four tabs never rendered");
  const tl = tid("tonight-timeline");
  assert(tl != null, "the TIMELINE tab drew nothing at all");
  // Marks, not just a frame: an empty <svg> would pass a marker-only assertion.
  assert(tl.querySelectorAll("rect").length >= 2,
    "the twilight band and the target block are both missing - the geometry drew no rects");
  assert(tl.querySelector("path") != null,
    "the target's altitude arc never drew, so `curve` is not reaching the geometry");
  assert(/DUSK/.test(tl.textContent), "the DUSK label is not in the HTML label layer");
  assert(/DAWN/.test(tl.textContent), "the DAWN label is not in the HTML label layer");
  assert(/NGC 7331/.test(tl.textContent), "the target block carries no label");
});

test("the header live line states the instants the night hangs off", () => {
  const head = container.querySelector(".nx-sheet-live") as any;
  assert(head != null, "no live line on the sheet header");
  assert(/dusk \d\d:\d\d/.test(head.textContent), `dusk is missing: "${head.textContent}"`);
  assert(/dawn \d\d:\d\d/.test(head.textContent), `dawn is missing: "${head.textContent}"`);
});

test("the moon legend carries the measured illumination, not a fixture", () => {
  assert(/moon up \(71%\)/.test(tid("tonight-timeline").textContent),
    "the legend must read the payload's illumination");
});

// ================================== 2. four views of one payload, one request

await testAsync("switching tabs does not re-ask the rig", async () => {
  const before = tonightGets();
  eq(before >= 1, true, "precondition: the sheet asked once when it opened");

  click(tabButton("STORY"));
  await settle();
  assert(tid("tonight-story") != null, "STORY never rendered");

  click(tabButton("CAMPAIGN"));
  await settle();
  assert(tid("tonight-campaign") != null, "CAMPAIGN never rendered");

  click(tabButton("TIMELINE"));
  await settle();

  eq(tonightGets(), before,
    "a tab is a view of the payload already in hand - refetching could show two "
    + "different nights in two tabs");
});

test("STORY renders the server's rows, labels and all, with no em-dash", () => {
  click(tabButton("STORY"));
  const story = tid("tonight-story");
  assert(/Waits for dusk/.test(story.textContent), "the server's brief is missing");
  assert(/BUDGET/.test(story.textContent), "an untimed BUDGET row must keep its label");
  assert(/No horizon polyline/.test(story.textContent), "the warn row never rendered");
  assert(!story.textContent.includes("\u2014"),
    "an em-dash reached the screen; the placeholder label must be a hyphen");
});

test("storyStamp keeps a label, formats a time, and never invents one", () => {
  eq(storyStamp({ t_unix: null, label: "BUDGET" }), "BUDGET", "a label wins");
  eq(storyStamp({ t_unix: null, label: "" }), "-", "an untimed unlabelled row is a hyphen");
  eq(/^\d\d:\d\d$/.test(storyStamp({ t_unix: DUSK, label: "" })), true,
    "a timed row is a clock time");
});

// ======================================= 3. banked null is not banked zero

test("a member with no ledger reads 'not counted' and draws no fill", () => {
  click(tabButton("CAMPAIGN"));
  const counted = tid("tonight-member-NGC 7331");
  const uncounted = tid("tonight-member-NGC 604");
  assert(counted != null && uncounted != null, "both pool members must render");
  assert(/23\/45 cycles/.test(counted.textContent), "the counted member states its cycles");
  assert(/not counted/.test(uncounted.textContent),
    "a null `banked` must say nobody looked, never '0/45'");
  eq(uncounted.querySelector(".nx-tn-member-fill"), null,
    "an unmeasured member must draw an empty track, not a bar sitting at 0%");
  eq(uncounted.querySelector("[role=progressbar]").getAttribute("aria-valuenow"), null,
    "and must announce no value either - '0' for 'unknown' is the same lie in audio");
});

// ================================= 4. the PLAN tab and its library health

await testAsync("PLAN says why there is no plan, and reads the calibration library", async () => {
  click(tabButton("PLAN"));
  await settle();
  const plan = tid("tonight-plan");
  assert(plan != null, "the PLAN tab never rendered");
  assert(/has not compiled to a plan/.test(plan.textContent),
    "with no compile the tab must say what is missing, not show an empty block");

  const cal = tid("flow-calibration");
  assert(cal != null, "LIBRARY HEALTH is missing from the PLAN tab");
  assert(/MISSING/.test(cal.textContent) && /300s bin1/.test(cal.textContent),
    "the health rows never rendered from the stub");
  assert(/Counts stacked masters only - a row reads MISSING/.test(cal.textContent),
    "the masters-only flag must be a sentence, or a row reads MISSING where subs exist");
  assert(!cal.textContent.includes("\u2014"),
    "the shared legacy sentence still carries an em-dash into the new UI");
});

// ======================================= 5. a transport failure, and RETRY

await testAsync("tonightError renders the server's message and RETRY re-asks", async () => {
  tonightFails = true;
  seed(ADMIN);
  await mount();

  const err = tid("tonight-error");
  assert(err != null, "a failed request must render its own state");
  assert(/could not be read from the rig/.test(err.textContent),
    "the transport failure has to say the request did not arrive");
  assert(/503|not answering/.test(err.textContent),
    `the server's own message has to survive: "${err.textContent}"`);

  tonightFails = false;
  const before = tonightGets();
  click(tid("tonight-retry"));
  await settle();
  eq(tonightGets(), before + 1, "RETRY must actually re-ask the rig");
  assert(tid("tonight-timeline") != null, "and the answer has to replace the error");
});

// ============================= 6. a missing dusk drops the clause, not the line

await testAsync("with no dusk the parked line keeps the sentence and drops the time", async () => {
  tonightBody = TONIGHT_NO_DUSK;
  seed(ADMIN, {
    resumeArm: {
      armed: { id: "sess-9", origin: "flow", origin_id: "quick-m31", auto_resume: true },
    },
  });
  await mount();

  const line = tid("tonight-parked");
  assert(line != null, "a parked campaign must say so on every tab");
  eq(line.textContent, resumesLine(null), "the sentence is the shared one");
  assert(/resumes by itself at dusk\.$/.test(line.textContent),
    `no time may be invented: "${line.textContent}"`);
  assert(!/\d\d:\d\d/.test(line.textContent), "a clock time appeared out of a null dusk");

  // ...and the same line WITH a dusk carries the clock, so the assertion above
  // is not passing because the clause never renders at all.
  assert(/at dusk \d\d:\d\d\.$/.test(resumesLine(DUSK)),
    "a known dusk must print its time - otherwise the null case proves nothing");

  assert(/no dusk and dawn to lay a timeline between/.test(tid("tonight-timeline").textContent),
    "and the timeline says which two instants it is missing");
});

// =============================== 7. the viewer sees it, locked, and asks nothing

await testAsync("a viewer without view.site_derived is told why, and nothing is requested", async () => {
  tonightBody = TONIGHT_OK;
  seed(["view.status", "view.preview"]);
  const before = asked.length;
  await mount();

  assert(tid("session-flow-tonight") != null, "the sheet must still render");
  assert(tid("tonight-tab") != null, "the four tabs must still be on screen, not hidden");

  eq(container.textContent.includes(TONIGHT_LOCK_REASON), true,
    "the sentence has to be readable without a hover a touch screen cannot perform");
  eq(TONIGHT_LOCK_REASON,
    "Tonight is worked out from the observatory site, so it needs operator or admin access.",
    "the sentence names the site AND the roles, from accessPhrase");

  const group = tid("tonight-tab");
  eq(group.getAttribute("aria-disabled"), "true",
    "the tabs must be honest-disabled, never the native `disabled` attribute");
  assert(container.querySelector("[disabled]") == null,
    "nothing on this sheet may carry the native disabled attribute");

  eq(asked.length, before, "a locked Tonight must not reach the network at all");

  click(tabButton("STORY"));
  await settle();
  eq(asked.length, before, "and pressing a locked tab must not either");
  eq(useStore.getState().flows.ui.tonightTab, "timeline",
    "a locked tab must not change the tab under the reader");
});

await act(async () => { root.unmount(); });
root = createRoot(container);

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`tonightDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
