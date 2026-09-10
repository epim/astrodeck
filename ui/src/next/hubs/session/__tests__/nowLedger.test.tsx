// nowLedger.test.tsx - `banked_h: null` is not `banked_h: 0`, on screen.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/nowLedger.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// THE DISTINCTION THIS FILE EXISTS FOR is a month of somebody's nights.
// "0 of 45 banked" says the rig looked and found nothing - a reason to re-plan.
// "not counted" says nobody has looked - a reason to go and find the ledger.
// The server sends `banked_h: null` for the second and `0` for the first
// (`flows/tonight.py` `_budget`), and a client that renders both as an empty bar
// at 0% has thrown away the only thing that told them apart.
//
// So: two mounts, two payloads, and the assertions are on what a person would
// actually SEE - the right-hand text, whether a fill element exists at all, and
// whether the server's own "no ledger" sentence is on the card.
//
// The viewer case is here too, because the route is `view.site_derived`-gated:
// an audit of this codebase recovered the observatory to 2.9 km from three
// viewer-legal requests, so a viewer must get NO ledger and NO request.

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
win.WebSocket = class { close() {} send() {} addEventListener() {} removeEventListener() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "requestAnimationFrame",
  "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
const asks: string[] = [];
let tonightBudget: unknown[] = [];

const SESSION = {
  id: "sess-1", schema_version: 3, name: "M31 LRGB",
  created_ts: 1, updated_ts: 2, status: "active",
  nights: ["night-2"], auto_resume: true,
  origin: "flow", origin_id: "flow-1",
  plan: {
    name: "M31 LRGB", guide: true, dither_every: 1, dither_pixels: null,
    autofocus_every: 0, cool_to: -10, cool_timeout_s: 600,
    apply_filter_offsets: null, refocus_on_temp_delta_c: null, meridian_flip: true,
    recover_guiding: null, hfr_reject_factor: 0, park_when_done: true,
    warm_cooler_when_done: true, count_mode: "accepted",
    targets: [{
      id: "t1", name: "M31", ra_hours: 0.71, dec_deg: 41.2,
      center: true, autofocus_first: true, calibration: false,
      steps: [
        { id: "s-L", filter: "L", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 12, frame_type: "Light" },
        { id: "s-R", filter: "R", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 12, frame_type: "Light" },
      ],
    }],
  },
  frames: [
    { id: "f1", ts: 1, night: "night-2", target_id: "t1", step_id: "s-L", thumb: null, metrics: {}, auto_accepted: true, override: null },
  ],
};

g.fetch = async (url: any, init: any) => {
  asks.push(`${(init?.method ?? "GET").toUpperCase()} ${String(url)}`);
  const u = String(url);
  const body = u.includes("/api/sessions/sess-1") ? SESSION
    : u.includes("/api/sessions") ? { sessions: [{ id: "sess-1", name: "M31 LRGB", status: "active", created_ts: 1, updated_ts: 2, nights: 2, accepted: 1, total: 24, auto_resume: true }] }
      : u.includes("/tonight") ? { ok: true, reason: "", budget: tonightBudget, night: { dusk_unix: 1, dawn_unix: 2, dark_start_unix: 1, dark_end_unix: 2 } }
        : u.includes("/api/reports") ? []
          : u.includes("/api/flows") ? [{ id: "flow-1", name: "M31 LRGB", folder: "", tagline: "", readonly: false, stages: 7, wires: 6, last_run: 1, last_result: "ok", updated_ts: 1 }]
            : { ok: true };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => "",
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { CampaignLedger } = await import("../now/CampaignLedger");
const { resetCampaignForTests, NO_LEDGER_NOTE } = await import("../now/useCampaign");
const { resetSessionDataForTests } = await import("../now/sessionData");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function seed(principal: unknown = OPERATOR): void {
  useStore.setState({
    principal,
    equipConnected: true,
    wsPhase: "up",
    status: { connected: {}, looping: false },
    sequence: {
      state: "running", target: "M31", plan_name: "M31 LRGB", target_index: 0,
      session: { id: "sess-1", name: "M31 LRGB", count_mode: "accepted", accepted: 1, target: "M31" },
      progress: { frames_done: 1, frames_total: 24, percent: 4, elapsed_s: 300, rejected: 0 },
    },
    flows: {
      ...(useStore.getState() as any).flows,
      cards: [{ id: "flow-1", name: "M31 LRGB", folder: "", tagline: "", readonly: false, stages: 7, wires: 6, last_run: 1, last_result: "ok", updated_ts: 1 }],
      libraryLoaded: true, libraryError: null,
    },
  } as never);
}

async function mount(): Promise<{ root: ReturnType<typeof createRoot> }> {
  const root = createRoot(container);
  await act(async () => { root.render(createElement(CampaignLedger)); });
  await settle();
  return { root };
}

// ------------------------------------------------------- 1. NO LEDGER (null)
tonightBudget = [
  { filter: "L", goal_h: 6, banked_h: null, tonight_h: 1, has_ledger: false },
  { filter: "R", goal_h: 2, banked_h: null, tonight_h: 0.5, has_ledger: false },
];
resetCampaignForTests();
resetSessionDataForTests();
seed();
let mounted = await mount();

await testAsync("the card rendered at all - the precondition for the two below", async () => {
  assert(byId("now-campaign-ledger") != null,
    "no campaign ledger: the flow-id resolution or the tonight fetch is broken, not the null rule");
  assert(byId("ledger-row-L") != null, "no L row");
});

await testAsync("banked_h: null renders a DASH, an outlined track and the server's sentence", async () => {
  const row = byId("ledger-row-L");
  eq(row.getAttribute("data-has-ledger"), "false", "the row does not know it has no ledger:");
  const text = row.textContent as string;
  assert(/- \/ 6 h/.test(text), `a null bank must read "- / 6 h"; got "${text}"`);
  assert(!/0\.0 \/ 6 h/.test(text), `a null bank rendered as a measured zero: "${text}"`);
  // No fill element INSIDE THE TRACK at all, not a fill at 0%: a bar at zero
  // is what a measured zero looks like, and these two must not look alike.
  const track = row.querySelector('[role="img"]');
  assert(track != null, "the row drew no bar track at all");
  eq(track.children.length, 0,
    "a null bank drew a fill element, which is what a measured 0 looks like");
  const note = byId("ledger-no-ledger-note");
  assert(note != null, "no ledger and no sentence saying so");
  eq(note.textContent, NO_LEDGER_NOTE, "the note is not the server's own sentence:");
});

await act(async () => { mounted.root.unmount(); });

// -------------------------------------------------------- 2. A MEASURED ZERO
tonightBudget = [
  { filter: "L", goal_h: 6, banked_h: 0, tonight_h: 1, has_ledger: true },
  { filter: "R", goal_h: 2, banked_h: 1.4, tonight_h: 0.5, has_ledger: true },
];
resetCampaignForTests();
resetSessionDataForTests();
seed();
mounted = await mount();

await testAsync("banked_h: 0 renders a real zero bar and NO no-ledger note", async () => {
  const row = byId("ledger-row-L");
  assert(row != null, "the L row vanished on the measured-zero payload");
  eq(row.getAttribute("data-has-ledger"), "true", "the row does not know it HAS a ledger:");
  const text = row.textContent as string;
  assert(/0\.0 \/ 6 h/.test(text), `a measured zero must read "0.0 / 6 h"; got "${text}"`);
  assert(!/- \/ 6 h/.test(text), `a measured zero rendered as "not counted": "${text}"`);
  const track = row.querySelector('[role="img"]');
  assert(track != null, "the row drew no bar track at all");
  assert(track.children.length > 0,
    "a measured zero drew no fill element, so it looks exactly like 'not counted'");
  assert(byId("ledger-no-ledger-note") == null,
    "the no-ledger note is on a card that HAS a ledger");
});

await testAsync("a banked row draws its fill, and the header counts the whole campaign", async () => {
  const r = byId("ledger-row-R");
  assert(/1\.4 \/ 2 h/.test(r.textContent as string), `R row: "${r.textContent}"`);
  const card = byId("now-campaign-ledger").textContent as string;
  assert(/of 8 h/.test(card), `the header did not sum the goals: "${card.slice(0, 200)}"`);
  assert(/night 2 of ~/.test(card),
    `the derived night total must carry a tilde: "${card.slice(0, 200)}"`);
});

await testAsync("the quota rows are read-only and say what each rule MEANS", async () => {
  const q = byId("now-quotas");
  assert(q != null, "no quota block beside the ledger");
  const text = q.textContent as string;
  assert(/counting accepted frames - rejects do not use up the count/.test(text),
    `the count mode does not explain itself: "${text.slice(0, 200)}"`);
  const edit = byId("quota-edit");
  eq(edit.getAttribute("aria-disabled"), "true",
    "the phone offers a plan editor it cannot open");
  assert(/tablet or desktop/.test(edit.getAttribute("title") ?? ""),
    "the locked edit chip does not say where the editor is");
});

await act(async () => { mounted.root.unmount(); });

// ------------------------------------------------------------- 3. the viewer
resetCampaignForTests();
resetSessionDataForTests();
seed(VIEWER);
const before = asks.filter((a) => a.includes("/tonight")).length;
mounted = await mount();

await testAsync("a viewer gets no ledger and asks the site-derived route nothing", async () => {
  eq(byId("now-campaign-ledger"), null, "a viewer was shown the campaign ledger");
  eq(asks.filter((a) => a.includes("/tonight")).length, before,
    "a viewer's mount requested the site-derived tonight route");
});

await act(async () => { mounted.root.unmount(); });

const total = passed + failed;
console.log(`nowLedger.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
