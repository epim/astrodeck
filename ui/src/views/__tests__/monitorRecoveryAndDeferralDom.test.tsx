// monitorRecoveryAndDeferralDom.test.tsx - the classic Monitor's run panel says
// what the rig is doing in the two states it used to leave blank (#246, #244).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/views/__tests__/monitorRecoveryAndDeferralDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// ONE CLASS, TWO INSTANCES: a server fact added for the operator with no screen
// behind it.
//
//   #246  `GET /api/sequence/resume-arm` reports `recovering` and
//         `recovery: {step, session_id, session_name}` while ResumeArm's
//         recovery ladder blind-solves and re-centres the mount after a
//         restart. The engine is idle all that time, so the panel said RUN
//         ARMED (or showed the last run's ending) over a rig slewing by itself.
//   #244  `sequence.sky.hold_deferred` is the engine's sentence while a
//         scheduler wait meets a cloudy sky with no target to hold for, so no
//         hold opens. Nothing read it, so a wait under a closed sky showed no
//         hold and no reason.
//
// Every assertion runs after the Progress panel has been found, so an absence
// cannot pass over a blank page.
//
// NAMED MUTANTS. Each was run from a byte copy of the file it mutates, and the
// file was restored byte-identical afterwards (sha256 checked). The observed
// failure is quoted at the test it turns red.
//   M1 "Monitor ignores recovering"    MonitorView.tsx never renders the line
//   M2 "gate the line on idle"         MonitorView.tsx computes it only when idle
//   M3 "drop the live-run guard"       api/sessions.ts resumeRecoveryLine
//   M4 "read recovery without the flag" api/sessions.ts resumeRecoveryLine
//   M5 "ignore hold_deferred"          MonitorView.tsx never reads the field
//   M6 "show the stale hold while recovering" MonitorView.tsx RUN ARMED paragraph
//                                      (the paragraph is now gated whole, so S3
//                                      below is this mutant's form today)
//   S1 "no stop on the classic Monitor"  MonitorView.tsx never renders the button
//   S2 "the stop is not gated on control.mount" MonitorView.tsx's `!canRun` dropped
//   S3 "the wait sentence while recovering" MonitorView.tsx keeps the paragraph
//   S7 "the stop re-arms"                stopResumeRecovery.ts sends auto_resume: true

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
// The view's cold-load reads (/api/monitor/snapshot, /api/dome/state) are
// conveniences; failing them is a supported path and keeps this test on the
// two lines under test.
win.fetch = () => Promise.reject(new Error("offline in this test"));

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MonitorView = (await import("../MonitorView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

// ------------------------------------------------------------------ fixtures
const ARMED = {
  id: "sess-1", name: "M31 LRGB", owed: 58, accepted: 132, total: 190,
  origin: "plan", origin_id: "",
};
const LADDER = { step: "solve", session_id: "sess-1", session_name: "M31 LRGB" };
const LINE = "Auto-resume is re-centring the mount for M31 LRGB (solve)";

/** The engine's own sentence, verbatim from `SequenceEngine._HOLD_DEFERRED`. */
const DEFERRED =
  "the frames say the sky has closed in, with no target set up to judge it "
  + "from, so no cloud hold is open; the next target's setup opens one if the "
  + "sky is still closed";

function arm(over: Record<string, unknown> = {}): any {
  return { armed: ARMED, hold: null, recovering: false, recovery: null, ...over };
}

/** A run inside a scheduler wait: running, no frame in flight, a schedule. */
function waiting(sky: Record<string, unknown> | undefined): any {
  return {
    state: "running",
    plan_name: "M31 LRGB",
    target: "M31",
    detail: "waiting for M31 to rise",
    schedule: { state: "waiting", reason: "M31 is below its floor", eta_s: 1500 },
    ...(sky ? { sky } : {}),
  };
}

function cloudySky(holdDeferred: string | null | undefined): Record<string, unknown> {
  const s: Record<string, unknown> = {
    cloudy: true, age_s: 40, score: 0.12,
    reason: "cloudy, from a reading 40s old - 1 bright stars, low contrast",
    text: "cloudy, from a reading 40s old - 1 bright stars, low contrast",
    holding: false,
    latest_frame: { cloudy: true, score: 0.12, reason: "1 bright stars, low contrast" },
  };
  if (holdDeferred !== undefined) s.hold_deferred = holdDeferred;
  return s;
}

function set(state: Record<string, unknown>): void {
  act(() => { useStore.setState(state as never); });
}

set({
  principal: { role: "admin", email: null, caps: ["view.status", "control.mount"] },
  wsConnected: true,
  sequence: { state: "idle" },
  resumeArm: arm(),
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(MonitorView)); });

const text = () => (container.textContent || "") as string;
const recoveryEl = () => container.querySelector('[data-testid="monitor-resume-recovering"]');
const deferredEl = () => container.querySelector('[data-testid="monitor-hold-deferred"]');
/** The Progress panel: the <section> whose title is "Progress". */
const progressPanel = (): any =>
  [...container.querySelectorAll("section")].find((s: any) =>
    (s.querySelector(".panel-title")?.textContent || "") === "Progress");

// ============================================================== preconditions

test("precondition: the Progress panel is mounted and shows RUN ARMED for the armed session", () => {
  // Without this, every "nothing is rendered" control below would pass over a
  // page that rendered nothing at all.
  assert(progressPanel() != null, `no Progress panel on the Monitor: ${text().slice(0, 160)}`);
  assert(/RUN ARMED/.test(progressPanel().textContent), "the armed card is not up - the fixture is wrong");
  assert(/M31 LRGB/.test(progressPanel().textContent), "the armed card does not name the session");
});

// ============================================================ #246 recovering

test("control: with recovering false the panel says nothing about re-centring", () => {
  set({ resumeArm: arm() });
  eq(recoveryEl(), null, "a line rendered while the ladder is not running");
  assert(!/re-centring/.test(text()), "a re-centring claim is on the page with recovering false");
});

test("while the ladder runs, the panel says so in the server's words: session and step", () => {
  // M1 "Monitor ignores recovering", observed (9/14 passed; the four tests
  // below that need the line failed with it):
  //   x while the ladder runs, the panel says so in the server's words: session
  //   and step: no recovery line on the Monitor while recovering is true
  set({ resumeArm: arm({ recovering: true, recovery: LADDER }) });
  const el = recoveryEl();
  assert(el != null, "no recovery line on the Monitor while recovering is true");
  // EXACT equality is the "nothing positional is added" check (#233): an
  // altitude, a position or a time appended to the step would fail it.
  eq(el.textContent, LINE, "the line is not the server's session and step, verbatim:");
});

test("the line sits in the Progress panel, directly over the RUN ARMED card it is about", () => {
  const el = recoveryEl();
  assert(el != null, "precondition: no recovery line");
  const panel = progressPanel();
  assert(panel.contains(el), "the line is not in the run panel");
  const armedWord = [...panel.querySelectorAll("span")].find((s: any) => s.textContent === "RUN ARMED");
  assert(armedWord != null, "precondition: RUN ARMED is not in the panel");
  // DOCUMENT_POSITION_FOLLOWING (4): RUN ARMED comes after the line.
  assert((el.compareDocumentPosition(armedWord) & 4) !== 0,
    "the line is not placed ahead of the RUN ARMED card it names");
});

test("the step word is rendered as sent, whatever word the server sends", () => {
  for (const step of ["starting", "safety", "focus", "autofocus", "limits", "recentre"]) {
    set({ resumeArm: arm({ recovering: true, recovery: { ...LADDER, step } }) });
    eq(recoveryEl()?.textContent, `Auto-resume is re-centring the mount for M31 LRGB (${step})`,
      `step "${step}" was not rendered verbatim:`);
  }
});

const STALE_HOLD = {
  reason: "M31 is below its start floor; not slewing yet",
  since: 1_700_000_000, retry_at: 1_700_000_600,
  session_id: "sess-1", session_name: "M31 LRGB", owed: 58,
};

test("control: an armed session held by a refusal says Holding and why", () => {
  set({ resumeArm: arm({ hold: STALE_HOLD }) });
  const p = progressPanel().textContent as string;
  assert(p.includes(`Holding: ${STALE_HOLD.reason}.`), `the hold is not shown while waiting: ${p}`);
});

test("while the ladder runs, the previous attempt's hold is not claimed as a wait", () => {
  // `hold` survives into the next ladder (ResumeArm clears it only after its
  // own start or a stop), so beside `recovering` it is the refusal of the
  // attempt before this one.
  // M6 "show the stale hold while recovering", observed (13/14 passed):
  //   x while the ladder runs, the previous attempt's hold is not claimed as a
  //   wait: "Holding: ... It starts by itself when that clears" beside a
  //   ladder that is already moving the mount
  set({ resumeArm: arm({ hold: STALE_HOLD, recovering: true, recovery: LADDER }) });
  assert(recoveryEl() != null, "precondition: no recovery line");
  assert(!/Holding:/.test(progressPanel().textContent),
    "\"Holding: ... It starts by itself when that clears\" beside a ladder that is already moving the mount");
});

test("a ladder after a night that ended on a safety stop is shown too, not only from idle", () => {
  // The engine keeps its terminal word until the next run starts, and a
  // safety-stopped night is the one auto-resume exists to pick up.
  // M2 "gate the line on idle", observed (13/14 passed):
  //   x a ladder after a night that ended on a safety stop is shown too, not
  //   only from idle: the line vanished because the engine's state is
  //   "aborted", not "idle"
  set({
    sequence: { state: "aborted", end_reason: "unsafe", detail: "unsafe: rain" },
    resumeArm: arm({ recovering: true, recovery: LADDER }),
  });
  assert(progressPanel() != null, "precondition: the Progress panel is gone");
  eq(recoveryEl()?.textContent ?? null, LINE,
    'the line vanished because the engine\'s state is "aborted", not "idle"');
});

test("control: a live run beside a stale recovering poll shows no recovery line", () => {
  // The ladder lowers its flag before its own start, and resume-arm is polled
  // every 20 s: `recovering: true` over a running engine is the last poll.
  // M3 "drop the live-run guard", observed (13/14 passed):
  //   x control: a live run beside a stale recovering poll shows no recovery
  //   line: a re-centring claim over a run that is shooting
  set({
    sequence: waiting(undefined),
    resumeArm: arm({ recovering: true, recovery: LADDER }),
  });
  assert(progressPanel() != null, "precondition: the Progress panel is gone");
  eq(recoveryEl(), null, "a re-centring claim over a run that is shooting");
});

test("control: a recovery detail on a lowered flag says nothing", () => {
  // M4 "read recovery without the flag", observed (13/14 passed):
  //   x control: a recovery detail on a lowered flag says nothing: the line
  //   rendered off `recovery` alone
  set({
    sequence: { state: "idle" },
    resumeArm: arm({ recovering: false, recovery: LADDER }),
  });
  assert(/RUN ARMED/.test(progressPanel().textContent), "precondition: the armed card is not up");
  eq(recoveryEl(), null, "the line rendered off `recovery` alone");
});

// ======================================================= #246 the stop
//
// With the engine idle neither Pause nor Abort is on screen, so the line
// carries the session's own disarm, which stops the ladder recovering it (H2).

const stopEl = () => container.querySelector('[data-testid="monitor-resume-recovering-stop"]');
const WINDOW_SENTENCE = "It starts by itself when its window opens.";

test("control: with recovering false there is no stop, and RUN ARMED says when it starts", () => {
  set({ sequence: { state: "idle" }, resumeArm: arm() });
  assert(/RUN ARMED/.test(progressPanel().textContent), "precondition: the armed card is not up");
  eq(stopEl(), null, "a stop for a ladder that is not running");
  assert(progressPanel().textContent.includes(WINDOW_SENTENCE), "the waiting card lost its wait sentence");
});

test("while the ladder runs, the line carries a stop, and RUN ARMED drops the wait sentence", () => {
  // S1 "no stop on the classic Monitor", observed:
  //   (15/18 passed; the press test failed as "PATCH requests sent ([]):"
  //   and the viewer's as "the stop vanished for a viewer")
  //   x while the ladder runs, the line carries a stop, and RUN ARMED drops
  //   the wait sentence: no stop beside the line while the ladder moves the
  //   mount
  // S3 "the wait sentence while recovering", observed:
  //   (16/18 passed; M6's test above failed with it, since the paragraph the
  //   mutant keeps is the one that carried the stale hold)
  //   x while the ladder runs, the line carries a stop, and RUN ARMED drops
  //   the wait sentence: "It starts by itself when its window opens." under a
  //   line saying the window opened and the ladder is running
  set({ sequence: { state: "idle" }, resumeArm: arm({ recovering: true, recovery: LADDER }) });
  assert(recoveryEl() != null, "precondition: no recovery line");
  const stop = stopEl();
  assert(stop != null, "no stop beside the line while the ladder moves the mount");
  eq(stop.textContent, "Stop auto-resume", "the stop's label:");
  eq(stop.disabled, false, "an admin's stop is disabled:");
  assert((recoveryEl().compareDocumentPosition(stop) & 4) !== 0, "the stop is not placed after the line it stops");
  assert(!progressPanel().textContent.includes(WINDOW_SENTENCE),
    `"${WINDOW_SENTENCE}" under a line saying the window opened and the ladder is running`);
});

await (async () => {
  // S7 "the stop re-arms", observed:
  //   (17/18 passed)
  //   x pressing the stop disarms the session the ladder names, and the line
  //   comes off: the disarm's body:
  //     expected {"auto_resume":false}
  //     got      {"auto_resume":true}
  const asked: { method: string; url: string; body: string | undefined }[] = [];
  const offline = g.fetch;
  g.fetch = async (url: any, init?: { method?: string; body?: string }) => {
    const u = String(url);
    const method = init?.method ?? "GET";
    asked.push({ method, url: u, body: init?.body });
    const data = u.endsWith("/api/sequence/resume-arm")
      ? { armed: null, hold: null, recovering: false, recovery: null }
      : { id: "sess-1", status: "dormant", auto_resume: false, remaining: {} };
    return {
      ok: true, status: 200, statusText: "OK",
      headers: { get: () => "application/json" }, json: async () => data, text: async () => "",
    };
  };
  try {
    await act(async () => { stopEl()?.click(); });
    for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    test("pressing the stop disarms the session the ladder names, and the line comes off", () => {
      const patches = asked.filter((a) => a.method === "PATCH");
      eq(patches.length, 1, `PATCH requests sent (${JSON.stringify(asked)}):`);
      assert(patches[0].url.endsWith("/api/sessions/sess-1"),
        `the disarm went to ${patches[0].url}, not the session the ladder is recovering`);
      eq(patches[0].body, JSON.stringify({ auto_resume: false }), "the disarm's body:");
      eq(recoveryEl(), null, "the recovery line outlived the stop");
      eq(stopEl(), null, "the stop outlived the ladder it stopped");
    });
  } finally {
    g.fetch = offline;
  }
})();

test("a viewer sees the stop disabled with what it needs", () => {
  // S2 "the stop is not gated on control.mount", observed:
  //   (17/18 passed)
  //   x a viewer sees the stop disabled with what it needs: a viewer's stop is
  //   enabled:
  set({
    principal: { role: "viewer", email: null, caps: ["view.status"] },
    sequence: { state: "idle" },
    resumeArm: arm({ recovering: true, recovery: LADDER }),
  });
  const stop = stopEl();
  assert(stop != null, "the stop vanished for a viewer: a permission changes whether it works, not whether it is there");
  eq(stop.disabled, true, "a viewer's stop is enabled:");
  assert(/stopping it needs .*access/.test(progressPanel().textContent),
    "no note saying what the stop needs");
  set({ principal: { role: "admin", email: null, caps: ["view.status", "control.mount"] } });
});

// ========================================================= #244 hold_deferred

test("control: a cloudy wait with hold_deferred null shows no deferral sentence", () => {
  set({ resumeArm: arm(), sequence: waiting(cloudySky(null)) });
  assert(progressPanel() != null, "precondition: the Progress panel is gone");
  assert(/RUNNING/.test(text()), `precondition: the run is not shown as running: ${text().slice(0, 160)}`);
  eq(deferredEl(), null, "a deferral line rendered while the field is null");
  assert(!text().includes("no cloud hold is open"), "the sentence is on the page with the field null");
});

test("control: a server that does not send the field shows nothing either", () => {
  set({ sequence: waiting(cloudySky(undefined)) });
  eq(deferredEl(), null, "a deferral line rendered with no field on the wire");
});

test("a deferred hold is shown in the run panel as the engine's sentence, verbatim", () => {
  // M5 "ignore hold_deferred", observed (12/14 passed; the next test's
  // precondition failed with it):
  //   x a deferred hold is shown in the run panel as the engine's sentence,
  //   verbatim: no deferral line on the Monitor while sky.hold_deferred is set
  set({ sequence: waiting(cloudySky(DEFERRED)) });
  const el = deferredEl();
  assert(el != null, "no deferral line on the Monitor while sky.hold_deferred is set");
  eq(el.textContent, DEFERRED, "the line is not the sentence itself:");
  assert(progressPanel().contains(el), "the sentence is not in the run panel");
});

test("the sentence goes when the server clears it", () => {
  set({ sequence: waiting(cloudySky(DEFERRED)) });
  assert(deferredEl() != null, "precondition: the sentence is not up");
  set({ sequence: waiting(cloudySky(null)) });
  eq(deferredEl(), null, "the sentence outlived the field");
});

act(() => { root.unmount(); });

// ------------------------------------------------------------------ summary
const total = passed + failed;
console.log(`monitorRecoveryAndDeferralDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total };
