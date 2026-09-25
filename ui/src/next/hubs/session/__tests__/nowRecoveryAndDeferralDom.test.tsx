// nowRecoveryAndDeferralDom.test.tsx - SESSION / NOW, MOUNTED, in the two
// states it used to show as nothing happening (#246, #244).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/session/__tests__/nowRecoveryAndDeferralDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// ONE CLASS, TWO INSTANCES: a server fact added for the operator with no screen
// behind it.
//
//   #246  While ResumeArm's recovery ladder solves and re-centres the mount
//         after a restart, `GET /api/sequence/resume-arm` says `recovering`
//         and `recovery: {step, session_id, session_name}`. Now showed RUN
//         ARMED over a rig slewing by itself.
//   #244  While a scheduler wait meets a cloudy sky with no target to hold
//         for, the engine publishes why no hold opened in
//         `sequence.sky.hold_deferred`. Now showed no cloud card at all.
//
// THE WHOLE SCREEN IS MOUNTED, NOT THE LIB. The cloud card's lines pass
// through two hands on their way to Now: `next/lib/incidents.ts` builds the
// card and `now/incidentActions.ts` `refineIncident` re-states it. The second
// used to overwrite every cloud card with "Capture paused at the frame
// boundary ... Watching the star count", so a lib-only test would pass while
// Now printed a hold that is not open. Every assertion runs after the screen
// marker and the block under test have been found.
//
// NAMED MUTANTS. Each was run from a byte copy of the file it mutates, and the
// file was restored byte-identical afterwards (sha256 checked). The observed
// failure is quoted at the test it turns red.
//   N1 "Now ignores recovering"             NowBanners.tsx never pushes the banner
//   N2 "ignore hold_deferred"               next/lib/incidents.ts never reads it
//   N3 "refine overwrites the deferral card" incidentActions.ts drops the guard
//   M3 "drop the live-run guard"            api/sessions.ts resumeRecoveryLine
//   M7 "show the stale hold while recovering (Now)" NowEmpty.tsx's hold line
//   S4 "no stop on Now"                     NowEmpty.tsx never renders STOP AUTO-RESUME
//   S5 "the stop is not locked for a viewer" NowEmpty.tsx's lockedReason made null
//   S6 "the wait sentence while recovering (Now)" NowEmpty.tsx's hint kept
//   S7 "the stop re-arms"                   stopResumeRecovery.ts sends auto_resume: true
//   B1 "the verdict back as ENGINE"         next/lib/incidents.ts swaps the two lines back

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

// ------------------------------------------------------------- the fake rig
const STACK = {
  enabled: true, target: "M31", seq: 7,
  channels: [{ channel: "L", frames: 2, integrated_s: 240, rejected: 0 }],
  frames: 2, integrated_s: 240, rejected: 0, mode: "mono", downsample: 2,
  has_image: false, render_age_s: 3,
  backfill: { running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0, channel: "", error: "", started_ts: null, finished_ts: null, available: 0 },
};

function answer(url: string): any {
  if (url.includes("/api/sessions/")) return { detail: "not in this test" };
  if (url.includes("/api/sessions")) return { sessions: [] };
  if (url.includes("/api/sequence/stack")) return STACK;
  if (url.includes("/api/sequence/recoverable")) return { recoverable: false };
  if (url.includes("/api/reports")) return [];
  if (url.includes("/api/plans")) return [];
  if (url.includes("/api/flows")) return [];
  return { ok: true };
}

/** Every request the screen sent, for the stop's cases (#246). */
const asked: { method: string; url: string; body: string | undefined }[] = [];
/** What `GET /api/sequence/resume-arm` answers once a stop has landed. */
const AFTER_STOP = { armed: null, hold: null, recovering: false, recovery: null };

g.fetch = async (url: any, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = init?.method ?? "GET";
  asked.push({ method, url: u, body: init?.body });
  const data = method === "PATCH" && u.endsWith("/api/sessions/sess-1")
    ? { id: "sess-1", status: "dormant", auto_resume: false, remaining: {} }
    : u.endsWith("/api/sequence/resume-arm") ? AFTER_STOP
      : answer(u);
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => "",
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { NowScreen } = await import("../now/NowScreen");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 3; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const text = () => (container.textContent || "") as string;

/** One labelled row of an incident card ("ENGINE" or "NEXT"), its value text. */
function incidentRow(card: any, key: string): string | null {
  for (const row of card.querySelectorAll(".nx-incident-row")) {
    if (row.querySelector(".nx-incident-key")?.textContent === key) {
      return row.querySelector(".nx-incident-val")?.textContent ?? "";
    }
  }
  return null;
}
const actionLabels = (card: any): string[] =>
  [...card.querySelectorAll("button[data-action]")].map((b: any) => b.textContent);

// ------------------------------------------------------------------ fixtures
const NOW = Date.now();
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture", "control.mount", "control.guide"],
};
const VIEWER = { role: "viewer", email: "v@rig", caps: ["view.status", "view.preview"] };
const ARMED = {
  id: "sess-1", name: "M31 LRGB", owed: 58, accepted: 132, total: 190,
  origin: "plan", origin_id: "",
};
const LADDER = { step: "solve", session_id: "sess-1", session_name: "M31 LRGB" };
const LINE = "Auto-resume is re-centring the mount for M31 LRGB (solve)";

/** Verbatim from `SequenceEngine._HOLD_DEFERRED`. */
const DEFERRED =
  "the frames say the sky has closed in, with no target set up to judge it "
  + "from, so no cloud hold is open; the next target's setup opens one if the "
  + "sky is still closed";
const SKY_TEXT = "cloudy, from a reading 40s old - 1 bright stars, low contrast";

function arm(over: Record<string, unknown> = {}): any {
  return { armed: ARMED, hold: null, recovering: false, recovery: null, ...over };
}

function cloudySky(holdDeferred: string | null, holding = false): any {
  return {
    cloudy: true, age_s: 40, score: 0.12, reason: SKY_TEXT, text: SKY_TEXT,
    holding, hold_deferred: holdDeferred,
    latest_frame: { cloudy: true, score: 0.12, reason: "1 bright stars, low contrast" },
  };
}

/** A run inside a scheduler wait: `running`, a schedule, no frame in flight. */
function waitingRun(sky: any, over: Record<string, unknown> = {}): any {
  return {
    state: "running",
    detail: "waiting for M31 to rise",
    target: "M31",
    target_index: 0,
    plan_name: "M31 LRGB",
    schedule: { state: "waiting", reason: "M31 is below its floor", eta_s: 1500 },
    progress: {
      frames_done: 3, frames_total: 18, percent: 17, elapsed_s: 3600, rejected: 0,
      eta_s: 5400, eta_confident: true, server_now_ms: NOW,
      current_exposure_s: 120, frame_started_at_ms: null,
    },
    sky,
    ...over,
  };
}

function seed(over: Record<string, unknown>): void {
  useStore.setState({
    principal: OPERATOR,
    equipConnected: true,
    wsPhase: "up",
    wsConnected: true,
    telemetryStale: false,
    wsLastEvent: NOW,
    lastCaptureAtMs: NOW,
    toasts: [],
    logs: [],
    safety: { connected: true, streak: 0, reading: { is_safe: true, reason: "", source: "sim", stale: false, ts: NOW / 1000 } },
    weather: null,
    resumeArm: null,
    focus: null,
    mountOp: null,
    lastAutofocusResult: null,
    guide: null,
    preview: null,
    status: {
      connected: { camera: { connected: true, name: "sim" }, telescope: { connected: true, name: "sim mount" } },
      looping: false,
      busy_lanes: [],
      disk: { free_gb: 210, low: false, critical: false },
      camera: {
        temperature: -10, can_cool: true, width: 100, height: 100, max_gain: 100,
        cooler: { on: true, power: 42, target_c: -10, at_target: true, can_report_power: true },
      },
    },
    sequence: { state: "idle" },
    flows: { ...(useStore.getState() as any).flows, cards: [], libraryLoaded: true, libraryError: null },
    ...over,
  } as never);
}

async function mount(over: Record<string, unknown>): Promise<void> {
  await act(async () => { root.render(createElement("div")); });
  seed(over);
  await act(async () => { root.render(createElement(NowScreen)); });
  await settle();
}

async function update(over: Record<string, unknown>): Promise<void> {
  await act(async () => { useStore.setState(over as never); });
  await settle();
}

// ============================================================ #246 recovering

await testAsync("precondition: Now is mounted, idle, with the RUN ARMED card for the armed session", async () => {
  await mount({ sequence: { state: "idle" }, resumeArm: arm() });
  assert(byId("session-now") != null, "no session-now marker - the fixture is wrong, not the screen");
  assert(byId("now-empty") != null, "the idle screen's card is not up");
  assert(/RUN ARMED/.test(byId("now-empty").textContent), "RUN ARMED is not shown for the armed session");
});

await testAsync("control: with recovering false Now has no recovery banner", async () => {
  eq(byId("banner-resume-recovering"), null, "a recovery banner with recovering false");
  assert(!/re-centring/.test(text()), "a re-centring claim on Now with recovering false");
});

await testAsync("while the ladder runs, Now says so in the server's words, over RUN ARMED", async () => {
  // N1 "Now ignores recovering", observed (9/12 passed; the stale-hold test's
  // precondition failed with it, and the terminal-state test as "the banner
  // is gated on idle, and a terminal state hid it"):
  //   x while the ladder runs, Now says so in the server's words, over RUN
  //   ARMED: no recovery banner on Now while recovering is true
  await update({ resumeArm: arm({ recovering: true, recovery: LADDER }) });
  const banner = byId("banner-resume-recovering");
  assert(banner != null, "no recovery banner on Now while recovering is true");
  // Exact: the step as sent, nothing positional added (#233).
  eq(banner.textContent, LINE, "the banner is not the server's session and step, verbatim:");
  const card = byId("now-empty");
  assert(card != null, "precondition: the RUN ARMED card went away");
  assert((banner.compareDocumentPosition(card) & 4) !== 0,
    "the banner is not placed ahead of the RUN ARMED card it names");
});

const STALE_HOLD = {
  reason: "M31 is below its start floor; not slewing yet",
  since: 1_700_000_000, retry_at: 1_700_000_600,
  session_id: "sess-1", session_name: "M31 LRGB", owed: 58,
};

await testAsync("control: an armed session held by a refusal says Holding and why on RUN ARMED", async () => {
  await update({ resumeArm: arm({ hold: STALE_HOLD }) });
  const card = byId("now-empty");
  assert(card != null, "precondition: the RUN ARMED card went away");
  assert(card.textContent.includes(`Holding: ${STALE_HOLD.reason}.`), `the hold is not shown while waiting: ${card.textContent}`);
});

await testAsync("while the ladder runs, RUN ARMED does not claim the previous attempt's hold", async () => {
  // M7 "show the stale hold while recovering (Now)", observed (11/12 passed):
  //   x while the ladder runs, RUN ARMED does not claim the previous attempt's
  //   hold: "Holding: ... It starts by itself when that clears" under a banner
  //   saying the ladder is moving the mount
  await update({ resumeArm: arm({ hold: STALE_HOLD, recovering: true, recovery: LADDER }) });
  assert(byId("banner-resume-recovering") != null, "precondition: no recovery banner");
  assert(!/Holding:/.test(byId("now-empty").textContent),
    "\"Holding: ... It starts by itself when that clears\" under a banner saying the ladder is moving the mount");
});

await testAsync("the banner is up after a night that ended on a safety stop, too", async () => {
  await update({
    sequence: { state: "aborted", end_reason: "unsafe", detail: "unsafe: rain", plan_name: "M31 LRGB" },
  });
  assert(byId("session-now") != null, "precondition: the screen is gone");
  eq(byId("banner-resume-recovering")?.textContent ?? null, LINE,
    "the banner is gated on idle, and a terminal state hid it");
});

await testAsync("control: a live run beside a stale recovering poll has no recovery banner", async () => {
  // M3 "drop the live-run guard", observed (11/12 passed):
  //   x control: a live run beside a stale recovering poll has no recovery
  //   banner: a re-centring banner over a run that is going
  await update({ sequence: waitingRun(cloudySky(null)) });
  assert(byId("now-run-header") != null, "precondition: the run header is not up");
  eq(byId("banner-resume-recovering"), null, "a re-centring banner over a run that is going");
});

// ======================================================= #246 the stop
//
// The banner says the mount is moving on its own; with the engine idle no run
// control is on screen. So the RUN ARMED card carries the session's own
// disarm, which stops the ladder recovering it (H2), for the session the
// ladder names.

await testAsync("control: with recovering false the RUN ARMED card offers no stop, and says when it starts", async () => {
  await mount({ sequence: { state: "idle" }, resumeArm: arm() });
  assert(byId("now-empty") != null && /RUN ARMED/.test(byId("now-empty").textContent),
    "precondition: the RUN ARMED card is not up");
  eq(byId("now-stop-recovery"), null, "a stop for a ladder that is not running");
  assert(byId("now-empty").textContent.includes("It starts by itself when its window opens."),
    "the waiting card lost its wait sentence");
});

await testAsync("while the ladder runs, RUN ARMED offers STOP AUTO-RESUME and drops the wait sentence", async () => {
  // S4 "no stop on Now", observed:
  //   (13/16 passed; the next two tests failed with it, the viewer's as "the stop
  //   vanished for a viewer")
  //   x while the ladder runs, RUN ARMED offers STOP AUTO-RESUME and drops the
  //   wait sentence: no stop on Now while the ladder moves the mount
  // S6 "the wait sentence while recovering (Now)", observed:
  //   (15/16 passed)
  //   x while the ladder runs, RUN ARMED offers STOP AUTO-RESUME and drops the
  //   wait sentence: "It starts by itself when its window opens" under a banner
  //   saying the window opened and the ladder is running
  await update({ resumeArm: arm({ recovering: true, recovery: LADDER }) });
  assert(byId("banner-resume-recovering") != null, "precondition: no recovery banner");
  const card = byId("now-empty");
  assert(card != null, "precondition: the RUN ARMED card went away");
  assert(!card.textContent.includes("It starts by itself when its window opens."),
    "\"It starts by itself when its window opens\" under a banner saying the window opened and the ladder is running");
  const stop = byId("now-stop-recovery");
  assert(stop != null, "no stop on Now while the ladder moves the mount");
  eq(stop.textContent, "STOP AUTO-RESUME", "the stop's label:");
  eq(stop.getAttribute("data-locked"), null, "an operator's stop is locked:");
});

await testAsync("STOP AUTO-RESUME disarms the session the ladder names, and the banner comes off", async () => {
  // S7 "the stop re-arms", observed:
  //   (15/16 passed; the classic Monitor's and the #/next Monitor's stop tests
  //   failed with it too)
  //   x STOP AUTO-RESUME disarms the session the ladder names, and the banner
  //   comes off: the disarm's body:
  //     expected {"auto_resume":false}
  //     got      {"auto_resume":true}
  asked.length = 0;
  await act(async () => { byId("now-stop-recovery").click(); });
  await settle();
  const patches = asked.filter((a) => a.method === "PATCH");
  eq(patches.length, 1, `PATCH requests sent (${JSON.stringify(asked)}):`);
  assert(patches[0].url.endsWith("/api/sessions/sess-1"),
    `the disarm went to ${patches[0].url}, not the session the ladder is recovering`);
  eq(patches[0].body, JSON.stringify({ auto_resume: false }), "the disarm's body:");
  assert(asked.some((a) => a.method === "GET" && a.url.endsWith("/api/sequence/resume-arm")),
    "the resume-arm route was not read again after the stop");
  eq(byId("banner-resume-recovering"), null, "the recovery banner outlived the stop");
});

await testAsync("a viewer sees the stop locked with its reason, and pressing it sends nothing", async () => {
  // S5 "the stop is not locked for a viewer", observed:
  //   (15/16 passed)
  //   x a viewer sees the stop locked with its reason, and pressing it sends
  //   nothing: a viewer's stop is not locked:
  await mount({ principal: VIEWER, sequence: { state: "idle" }, resumeArm: arm({ recovering: true, recovery: LADDER }) });
  const stop = byId("now-stop-recovery");
  assert(stop != null, "the stop vanished for a viewer: a permission changes whether it works, not whether it is there");
  eq(stop.getAttribute("data-locked"), "true", "a viewer's stop is not locked:");
  assert(/access/.test(stop.getAttribute("title") ?? ""),
    `the lock does not say what it needs: ${stop.getAttribute("title")}`);
  asked.length = 0;
  await act(async () => { stop.click(); });
  await settle();
  eq(asked.filter((a) => a.method === "PATCH").length, 0, "a viewer's press sent the disarm");
});

// ========================================================= #244 hold_deferred

await testAsync("control: a cloudy wait with hold_deferred null raises no cloud card", async () => {
  await mount({ sequence: waitingRun(cloudySky(null)), resumeArm: arm() });
  assert(byId("now-run-header") != null, "precondition: the run header is not up");
  eq(byId("incident-cloud"), null, "a cloud card with no hold and no deferral");
  assert(!text().includes("no cloud hold is open"), "the sentence is on Now with the field null");
});

await testAsync("a deferred hold is Now's cloud card, carrying the engine's sentence verbatim", async () => {
  // N2 "ignore hold_deferred", observed (10/12 passed; the next test's
  // precondition failed with it):
  //   x a deferred hold is Now's cloud card, carrying the engine's sentence
  //   verbatim: no cloud card on Now while sky.hold_deferred is set
  // N3 "refine overwrites the deferral card", observed (10/12 passed; the next
  // test failed with it, as "the card says capture is paused at a frame
  // boundary: no hold is open"). The lib built the right card, and Now printed
  // the hold card's sentence over it:
  //   x a deferred hold is Now's cloud card, carrying the engine's sentence
  //   verbatim: NEXT is not the engine's sentence:
  //     expected the frames say the sky has closed in, with no target set up
  //              to judge it from, so no cloud hold is open; the next target's
  //              setup opens one if the sky is still closed
  //     got      Watching the star count and the cloud score (cloudy, from a
  //              reading 40s old - 1 bright stars, low contrast); the ledger
  //              keeps the sub count, so the plan picks up mid-pass.
  //
  // THE SENTENCE MOVED TO ENGINE (H3 integration, #244's verifier note): the
  // cross-hub banner is `${title}. ${firstSentence(engine)}`, so with the
  // verdict as ENGINE every other hub read "CLOUDY. cloudy, from a reading
  // ..." and never why no hold opened. N2 and N3 were re-run against the
  // swapped lines, observed:
  //   N2 (14/16 passed): x a deferred hold is Now's cloud card, carrying the
  //   engine's sentence verbatim: no cloud card on Now while sky.hold_deferred
  //   is set
  //   N3 (14/16 passed; the next test failed with it as before):
  //   x a deferred hold is Now's cloud card, carrying the engine's sentence
  //   verbatim: ENGINE is not the engine's sentence:
  //     expected the frames say the sky has closed in, with no target set up
  //              to judge it from, so no cloud hold is open; the next target's
  //              setup opens one if the sky is still closed
  //     got      Capture paused at the frame boundary. Guiding parked, mount
  //              tracking, cooler holding at setpoint - nothing to redo when it
  //              clears.
  // B1 "the verdict back as ENGINE", observed:
  //   (15/16 passed)
  //   x a deferred hold is Now's cloud card, carrying the engine's sentence
  //   verbatim: ENGINE is not the engine's sentence:
  //     expected the frames say the sky has closed in, with no target set up
  //              to judge it from, so no cloud hold is open; the next target's
  //              setup opens one if the sky is still closed
  //     got      cloudy, from a reading 40s old - 1 bright stars, low contrast
  await update({ sequence: waitingRun(cloudySky(DEFERRED)) });
  const card = byId("incident-cloud");
  assert(card != null, "no cloud card on Now while sky.hold_deferred is set");
  eq(incidentRow(card, "ENGINE"), DEFERRED, "ENGINE is not the engine's sentence:");
  eq(incidentRow(card, "NEXT"), SKY_TEXT, "NEXT is not the verdict the sentence rests on:");
});

await testAsync("the deferral card claims no hold: no hold wording, no HOLDING pill, no weather override", async () => {
  const card = byId("incident-cloud");
  assert(card != null, "precondition: no cloud card");
  const body = card.textContent as string;
  assert(!/Capture paused/.test(body), "the card says capture is paused at a frame boundary: no hold is open");
  assert(!/Watching the star count/.test(body), "the card says the sky is being probed: nothing probes it");
  assert(!/CLOUD HOLD/.test(body), "the card is titled as a hold");
  const pill = byId("now-phase-pill");
  assert(pill != null, "precondition: no phase pill");
  assert(/WAITING/.test(pill.textContent), `the phase pill does not say WAITING: "${pill.textContent}"`);
  assert(!/HOLDING/.test(pill.textContent), "the phase pill says HOLDING over a wait that opened no hold");
  const labels = actionLabels(card);
  assert(!labels.some((l) => /IGNORE WEATHER/.test(l)),
    `the card offers IGNORE WEATHER, which lifts the rain veto and cannot touch a frames verdict: ${labels.join(", ")}`);
  assert(labels.includes("WAIT"), `the card has no WAIT: ${labels.join(", ")}`);
});

await testAsync("the card goes when the server clears the sentence", async () => {
  await update({ sequence: waitingRun(cloudySky(null)) });
  eq(byId("incident-cloud"), null, "the cloud card outlived the field");
});

await testAsync("control: a hold that is really up keeps the hold card and the engine's hold sentence", async () => {
  const held = "held for cloud - 1 bright stars. Probing every 5 min; parks after 45 min";
  await update({ sequence: waitingRun(cloudySky(null, true), { state: "holding", hold: "clouds", detail: held }) });
  const card = byId("incident-cloud");
  assert(card != null, "no cloud card for a real hold");
  eq(incidentRow(card, "ENGINE"), held, "the hold card's ENGINE changed:");
  assert(/Watching the star count/.test(incidentRow(card, "NEXT") ?? ""), "the hold card's NEXT changed");
  assert(/CLOUD HOLD/.test(card.textContent), "the hold card lost its title");
});

await act(async () => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`nowRecoveryAndDeferralDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
