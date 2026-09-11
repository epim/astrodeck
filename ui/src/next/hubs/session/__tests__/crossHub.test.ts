// crossHub.test.ts - the data the SESSION hub hands the chrome on every OTHER
// hub (plan sections E.1-E.3).
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/crossHub.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
//   1. NO CAMPAIGN, NO STRIP. Nothing names a flow, so there is no campaign and
//      the strip is null. The failure this prevents is the one the shell's own
//      component comments name: a strip that invents "night 1 of 1" from a
//      session that never said so, on the line an operator reads to decide
//      whether a month of nights is on track.
//   2. THE TWO FORMS ARE THE TWO STATES. Parked prints the dusk it resumes at;
//      running prints the night number and the banked hours. Both are built from
//      `now/useCampaign.ts`'s single read, so the strip and the ledger card
//      cannot disagree.
//   3. THE DERIVED DENOMINATOR CARRIES ITS TILDE. There is no persisted planned
//      night count on the server, so `total` is a projection and the string says
//      so (plan deviation D2).
//   4. THE STRIP IS ABSENT ON THE SCREEN IT POINTS AT, and the incident banner
//      and tab dot are absent on the hub that already shows the incident card at
//      full size. Two announcements of one incident is how dismissing one of
//      them reads as resolved.
//
// The three hooks read the store and the route, so they are exercised through a
// probe component under `createRoot` + `act` - the same jsdom harness the DOM
// tests use (shell-and-tests.md section 4), in a `.ts` file via `createElement`.

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
const CARD = {
  id: "flow-m31", name: "M31 LRGB", folder: "My flows", tagline: "a campaign",
  readonly: false, stages: 9, wires: 8, last_run: 1_757_000_000,
  last_result: "ok", updated_ts: 1_757_000_500,
};

const ROW = {
  id: "s-camp", name: "M31 LRGB", status: "dormant",
  created_ts: 1_756_000_000, updated_ts: 1_757_000_000,
  nights: 2, accepted: 41, total: 120, auto_resume: true,
};

const SESSION = {
  id: "s-camp", schema_version: 1, name: "M31 LRGB",
  created_ts: ROW.created_ts, updated_ts: ROW.updated_ts,
  status: "dormant",
  origin: "flow", origin_id: "flow-m31",
  nights: ["2026-09-07", "2026-09-08"],
  auto_resume: true,
  plan: {
    name: "M31 LRGB", cool_to: -10, cool_timeout_s: 600,
    targets: [{
      id: "t1", name: "M31", steps: [
        { id: "st-L", filter: "L", exposure_s: 120, count: 30, gain: 100, offset: 10, binning: 1, frame_type: "Light" },
      ],
    }],
  },
  frames: [
    { id: "f1", ts: 1, night: "2026-09-08", target_id: "t1", step_id: "st-L", thumb: null, metrics: {}, auto_accepted: true, override: null },
  ],
};

/** The `budget` array is the ledger. `banked_h: null` is NOT zero: it says
 *  nobody has looked, and it must contribute nothing rather than a real 0. */
const TONIGHT_OK = {
  ok: true, reason: "",
  night: { dusk_unix: 1_757_100_000, dawn_unix: 1_757_130_000, dark_start_unix: 1_757_103_600, dark_end_unix: 1_757_126_000 },
  budget: [
    { filter: "L", goal_h: 6, banked_h: 2.5, tonight_h: 1, has_ledger: true },
    { filter: "Ha", goal_h: 6, banked_h: null, tonight_h: 1, has_ledger: false },
  ],
};

const asked: string[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  asked.push(`${init?.method ?? "GET"} ${url}`);
  const ok = (data: unknown) => ({
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
  });
  if (url === "/api/flows") return ok([CARD]);
  if (url === "/api/flows/folders") return ok([{ name: "My flows", count: 1, readonly: false }]);
  if (url === "/api/flows/flow-m31/tonight") return ok(TONIGHT_OK);
  if (url === "/api/sessions") return ok({ sessions: [ROW] });
  if (url === "/api/sessions/s-camp") return ok(SESSION);
  if (url === "/api/reports") return ok([]);
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({ detail: "no" }),
  };
};

// ------------------------------------------------------------------ imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { resetCampaignForTests } = await import("../now/useCampaign");
const { resetSessionDataForTests } = await import("../now/sessionData");
const cross = await import("../crossHub");

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

// -------------------------------------------------------------- pure, no DOM

test("firstSentence stops at the first stop", () => {
  eq(cross.firstSentence("Capture paused at the frame boundary. Guiding parked."),
    "Capture paused at the frame boundary.",
    "one sentence for a one-line banner - the card carries the rest");
  eq(cross.firstSentence("no full stop here"), "no full stop here",
    "and it never truncates to nothing");
});

// ------------------------------------------------------------- mounted hooks

const container = win.document.getElementById("root") as any;
const settle = async () => {
  for (let i = 0; i < 10; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

/** What the last render of the probe saw. */
let seen: {
  camp: ReturnType<typeof cross.useSessionCampaign>;
  strip: ReturnType<typeof cross.useCampaignStrip>;
  dot: ReturnType<typeof cross.useSessionDot>;
  banners: ReturnType<typeof cross.useSessionBanners>;
} = { camp: null, strip: null, dot: null, banners: [] };

/** Far enough before the fixture's dusk that "tonight" is still ahead. */
const NOW = 1_757_090_000_000;

function Probe(): null {
  seen = {
    camp: cross.useSessionCampaign(),
    strip: cross.useCampaignStrip(),
    dot: cross.useSessionDot(NOW),
    banners: cross.useSessionBanners(NOW),
  };
  return null;
}

function seedIdle(): void {
  act(() => {
    useStore.setState({
      principal: {
        role: "admin", email: null,
        caps: ["view.status", "view.preview", "view.site_derived"],
      } as never,
      authGate: "open",
      sequence: { state: "idle" } as never,
      safety: null as never,
      status: null as never,
      wsPhase: "up", telemetryStale: false, wsLastEvent: NOW,
      mountOp: null as never, focus: null as never, lastAutofocusResult: null as never,
      guide: null as never, lastCaptureAtMs: null as never,
      resumeArm: null as never, lastReportId: null as never, weather: null as never,
    } as never);
  });
}

resetCampaignForTests();
resetSessionDataForTests();

const root = createRoot(container);
async function render(hash: string): Promise<void> {
  act(() => { win.location.hash = hash; });
  await act(async () => { root.render(createElement(Probe)); });
  await settle();
}

// ============================================ 1. nothing to say, nothing said

seedIdle();
await render("#/sky");

test("no campaign, no strip", () => {
  eq(seen.strip, null, "an idle rig with nothing naming a flow must produce no strip");
});

test("no incident, no dot and no banner", () => {
  eq(seen.dot, null, "a quiet rig has nothing to pulse about");
  eq(seen.banners.length, 0, "and nothing to announce");
});

// =================================== 2. a parked campaign, on another hub

await testAsync("a parked campaign prints the dusk form", async () => {
  act(() => {
    useStore.setState({
      resumeArm: {
        armed: {
          id: "s-camp", name: "M31 LRGB", owed: 58, accepted: 41, total: 99,
          origin: "flow", origin_id: "flow-m31",
        },
        hold: null,
      } as never,
    } as never);
  });
  await settle();

  assert(asked.includes("GET /api/flows/flow-m31/tonight"),
    "the ledger comes from the tonight route, and it is read once rather than polled");

  const strip = seen.strip;
  assert(strip != null, "a parked campaign is still a campaign and still needs its strip");
  assert(/^CAMPAIGN · M31 LRGB · parked · resumes at dusk \d{1,2}:\d{2}/.test(strip!.line),
    `the day-time form with the dusk the server sent, got "${strip!.line}"`);
  eq(strip!.tone, "accent2", "purple, per the design");

  // The same read is what the Flows list's RESUME row uses. RESUME is a SESSION
  // verb (`POST /api/sessions/{id}/resume`), so a parked campaign with no
  // session id would render a button that cannot name what it resumes.
  eq(seen.camp?.parked, true, "armed + dormant + nothing running IS parked");
  eq(seen.camp?.sessionId, "s-camp", "and RESUME knows which session to resume");
  eq(seen.camp?.live, false, "nothing is running");
});

// ============================= 3. the running form, and the tilde it carries

await testAsync("a running campaign prints night n of ~total and the banked hours", async () => {
  act(() => {
    useStore.setState({
      sequence: {
        state: "running", plan_name: "M31 LRGB",
        session: { id: "s-camp", name: "M31 LRGB", count_mode: "attempts", accepted: 41 },
      } as never,
    } as never);
  });
  await settle();

  const line = seen.strip?.line ?? "";
  assert(/^CAMPAIGN · M31 LRGB · night 2 of ~\d+ · \d+\.\d of 12 h banked$/.test(line),
    `the running form, got "${line}"`);
  assert(line.includes("of ~"),
    "the denominator is a projection and the tilde is what says so (deviation D2)");
  assert(!/parked/.test(line), "a running campaign is not parked");
  // The `banked_h: null` row contributes NOTHING - "not counted" is not zero,
  // and 2.5 of 12 rather than some larger number is the evidence.
  assert(/ 2\.5\d? of 12 h banked$/.test(line),
    `a null ledger row must not be folded in as a real zero-or-more, got "${line}"`);
});

await testAsync("Session - Now suppresses the strip, every other hub keeps it", async () => {
  await render("#/session/now");
  eq(seen.strip, null, "a link to where you already are is not information");
  await render("#/weather/conditions");
  assert(seen.strip != null, "and it comes back on every other hub");
});

// ================================ 4. the incident banner, only off the hub

await testAsync("an incident banners on another hub and not on Session", async () => {
  act(() => {
    useStore.setState({
      sequence: {
        state: "holding", hold: "clouds", plan_name: "M31 LRGB",
        session: { id: "s-camp", name: "M31 LRGB", count_mode: "attempts", accepted: 41 },
      } as never,
    } as never);
  });
  await settle();

  const inc = seen.banners.find((b) => b.id.startsWith("inc-"));
  assert(inc != null, "a cloud hold has to be announced on the hub the operator is on");
  eq(inc!.tone, "warn", "amber, per the design");
  assert(inc!.text.startsWith("CLOUD HOLD."), `the title leads, got "${inc!.text}"`);
  assert(!/\..*\./.test(inc!.text.slice(0, -1)),
    `one sentence of engine text, not the whole card: "${inc!.text}"`);
  eq(inc!.cta?.label, "session", "and the way to the full card");

  const dot = seen.dot;
  assert(dot != null, "the tab dot pulses in the same incident's colour");
  eq(dot!.color, "#ffb454", "cloud is amber");
  assert(/^Session - CLOUD HOLD/.test(dot!.label),
    "never colour alone - the tab carries the title as its label");

  await render("#/session/now");
  eq(seen.banners.find((b) => b.id.startsWith("inc-")) ?? null, null,
    "on the Session hub the incident card is already on screen at full size");
  eq(seen.dot, null, "and the tab you are already on does not need a dot");
});

// ==================================== 5. dismissing is per news, not per slot

await testAsync("a dismissed banner stays dismissed under its own id", async () => {
  await render("#/weather/conditions");
  const inc = seen.banners.find((b) => b.id.startsWith("inc-"));
  assert(inc != null, "precondition: the incident banner is up");
  act(() => { inc!.onDismiss(); });
  await settle();
  eq(seen.banners.find((b) => b.id.startsWith("inc-")) ?? null, null,
    "dismissing has to remove it");
});

act(() => { root.unmount(); });

// ========================= 6. the headers describe the code that exists now

await testAsync("no file in this area still claims it is NOT YET WIRED", async () => {
  // A header that says a module is unused is read as permission to leave it
  // unused. Both of these had already been wired - `shell/CampaignStrip.tsx`,
  // `shell/Banners.tsx`, `shell/TabBar.tsx` and `shell/Rail.tsx` all read this
  // module, and `IncidentStack.tsx` calls `capLockReason` - while the comments
  // still told the next reader the wiring was someone else's later task.
  const { readFileSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const { dirname, join } = await import("node:path");
  const here = dirname(fileURLToPath(import.meta.url));
  const files: [string, string][] = [
    ["crossHub.ts", join(here, "..", "crossHub.ts")],
    ["now/incidentActions.ts", join(here, "..", "now", "incidentActions.ts")],
  ];
  for (const [name, path] of files) {
    const src = readFileSync(path, "utf8");
    assert(!/NOT YET WIRED/.test(src),
      `${name} still carries a NOT YET WIRED header for code the shell already calls`);
    assert(!/does not call this yet/.test(src),
      `${name} still says a caller has not been written that has`);
  }

  // ...and the claim the headers make instead is checked, not asserted: the
  // shell really does read all three hooks from this module.
  const shell = join(here, "..", "..", "..", "shell");
  for (const [file, hook] of [
    ["CampaignStrip.tsx", "useCampaignStrip"],
    ["Banners.tsx", "useSessionBanners"],
    ["TabBar.tsx", "useSessionDot"],
    ["Rail.tsx", "useSessionDot"],
  ]) {
    const src = readFileSync(join(shell, file), "utf8");
    assert(src.includes(hook) && /hubs\/session\/crossHub/.test(src),
      `shell/${file} no longer reads ${hook} from crossHub - the header's claim is stale again`);
  }
  const stack = readFileSync(join(here, "..", "now", "IncidentStack.tsx"), "utf8");
  assert(/capLockReason/.test(stack),
    "IncidentStack.tsx stopped calling capLockReason, so a two-capability spec is "
    + "graded on one capability again");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`crossHub.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
