// Pure-lib test for incidents.ts.
//
// SABOTAGE CHECKS (each names the assertion that goes red):
//   * reorder SEVERITY_ORDER, or evaluate the kinds independently instead of
//     walking the fixed order -> "safety beats cloud" and "a stall outranks a
//     cloud hold" go red.
//   * drop the isSequenceRunning() guard on af/cooler -> "af ... requires BOTH"
//     and "the cooler gate needs a live run" go red.
//   * drop the no-progress exclusion from safetyIncident -> "the no-progress
//     watchdog reading is a STALL, not a safety trip" goes red (it would report
//     both, and the top card would say PARKED over a rig that is not parked).
//   * widen the stall margin back to 120 s, or stop calling stallLevel ->
//     "stall fires at 3x the exposure + 20 s" goes red.
//   * loosen the cloud reason filter (or drop the state check) -> the two cloud
//     negative cases go red.
//   * re-add any of the five verbless actions -> "no card offers an action the
//     server has no verb for" goes red.
//   * re-add any of the four invented promises -> "no card promises a number
//     the engine does not have" goes red.
import { deriveIncidents, incidentColor, type IncidentInputs } from "../incidents";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

const NOW = 1_700_000_000_000;

function baseInputs(): IncidentInputs {
  return {
    sequence: { state: "idle" },
    safety: { is_safe: true },
    wsPhase: "up",
    telemetryStale: false,
    wsLastEvent: NOW,
    mountOp: null,
    focus: null,
    lastAutofocusResult: null,
    guide: null,
    disk: { free_gb: 50, low: false, critical: false },
    cooler: null,
    logs: [],
    lastCaptureAtMs: null,
    expectedFrameS: null,
  };
}

test("a clean run has no incidents", () => {
  eq(deriveIncidents(baseInputs(), NOW).length, 0);
});

// ------------------------------------------------------------------- cloud

test("cloud hold fires when holding with a cloud/weather/sky reason", () => {
  const inp = baseInputs();
  inp.sequence = { state: "holding", hold: { reason: "clouds", since: NOW - 60000 } };
  const list = deriveIncidents(inp, NOW);
  eq(list.length, 1);
  eq(list[0].kind, "cloud");
  eq(list[0].pill, "HOLDING");
  eq(list[0].sinceMs, NOW - 60000);
});

test("a hold reason that isn't weather-shaped does NOT read as a cloud incident", () => {
  const inp = baseInputs();
  inp.sequence = { state: "holding", hold: { reason: "operator paused" } };
  eq(deriveIncidents(inp, NOW).length, 0);
});

test("a matching hold reason on a non-holding state does NOT fire (state must agree)", () => {
  const inp = baseInputs();
  inp.sequence = { state: "running", hold: { reason: "clouds" } };
  eq(deriveIncidents(inp, NOW).length, 0);
});

test("sky.holding fires the cloud card even while the state says running", () => {
  // The 2026-08-12 defect: `_set_state` promotes a routine `running` publish to
  // `holding` only while the hold is up, so a publish that raced it left the
  // state saying running over a rig that had parked the guider. `sequence.sky`
  // is published beside `state` on every publish and carries the truth.
  const inp = baseInputs();
  inp.sequence = {
    state: "running",
    sky: { holding: true, cloudy: true, text: "12 stars, score 0.21" },
  };
  const list = deriveIncidents(inp, NOW);
  eq(list.length, 1, "sky.holding alone must raise the card:");
  eq(list[0].kind, "cloud");
  ok(list[0].next.includes("12 stars, score 0.21"),
    `the engine's own sky text belongs in NEXT, got "${list[0].next}"`);
});

test("the rig's own hold log dates the cloud card, not this phone's clock", () => {
  const inp = baseInputs();
  inp.sequence = { state: "holding", hold: { reason: "clouds", since: NOW - 60000 } };
  inp.logs = [
    { source: "sequence", level: "info", message: "capture started", tsMs: NOW - 900000 },
    { source: "sequence", level: "warn", message: "holding for clear sky: 4 stars", tsMs: NOW - 300000 },
  ];
  eq(deriveIncidents(inp, NOW)[0].sinceMs, NOW - 300000, "the rig's log wins over `hold.since`:");
});

// ------------------------------------------------------------------ safety

test("safety unsafe fires PARKED red, and beats a simultaneous cloud hold", () => {
  const inp = baseInputs();
  inp.sequence = { state: "holding", hold: { reason: "clouds" } };
  inp.safety = { is_safe: false, reason: "rain", source: "boltwood", ts: NOW - 5000 };
  const list = deriveIncidents(inp, NOW);
  ok(list.length >= 2, "both safety and cloud should be present");
  eq(list[0].kind, "safety", "safety must be most severe");
  eq(list[0].pill, "PARKED");
  eq(list[0].sinceMs, NOW - 5000);
  ok(list[0].title.includes("BOLTWOOD"), `the source names the trip, got "${list[0].title}"`);
  ok(list.some((i) => i.kind === "cloud"), "cloud hold is still reported, just not first");
});

test("a safety trip needs a person - it does not resolve itself", () => {
  const inp = baseInputs();
  inp.safety = { is_safe: false, reason: "rain", ts: NOW };
  eq(deriveIncidents(inp, NOW)[0].resolvesItself, false);
});

test("the no-progress watchdog reading is a STALL, not a safety trip", () => {
  // engine.py:4310 publishes `no progress in {n} min` through the SAFETY
  // channel. Reporting it as a safety trip would put PARKED on the card over a
  // rig that is neither parked nor in danger.
  const inp = baseInputs();
  inp.sequence = { state: "running" };
  inp.safety = { is_safe: false, reason: "no progress in 14 min", source: "engine", ts: NOW - 60000 };
  const list = deriveIncidents(inp, NOW);
  eq(list.map((i) => i.kind).join(","), "stall", "kinds raised:");
  eq(list[0].sinceMs, NOW - 60000, "the server's own trip timestamp dates it:");
  ok(/watchdog has tripped/.test(list[0].next), `NEXT must say the watchdog tripped: "${list[0].next}"`);
});

// -------------------------------------------------------------------- link

test("link incident fires on a down phase OR stale telemetry", () => {
  const down = baseInputs();
  down.wsPhase = "down";
  eq(deriveIncidents(down, NOW)[0].kind, "link");

  const stale = baseInputs();
  stale.telemetryStale = true;
  eq(deriveIncidents(stale, NOW)[0].kind, "link");
  eq(deriveIncidents(stale, NOW)[0].pill, "STALE");
});

// ------------------------------------------------------------------- solve

test("solve incident fires when the mount op is stuck", () => {
  const inp = baseInputs();
  inp.mountOp = { stuck: true, attempt: 3 };
  const list = deriveIncidents(inp, NOW);
  eq(list[0].kind, "solve");
  eq(list[0].pill, "RETRYING");
});

test("a recent centring error in the rig's log fires the solve card on its own", () => {
  const inp = baseInputs();
  inp.sequence = { state: "running" };
  inp.logs = [
    { source: "sequence", level: "error", message: "centring failed: no solution", tsMs: NOW - 60000 },
  ];
  eq(deriveIncidents(inp, NOW)[0].kind, "solve");

  const old = baseInputs();
  old.sequence = { state: "running" };
  old.logs = [
    { source: "sequence", level: "error", message: "centring failed: no solution", tsMs: NOW - 30 * 60000 },
  ];
  eq(deriveIncidents(old, NOW).length, 0, "an error half an hour ago is history, not an incident");
});

// ---------------------------------------------------------------------- af

test("af incident requires BOTH a failed focus/result AND a live sequence", () => {
  const notRunning = baseInputs();
  notRunning.focus = { state: "failed" };
  eq(deriveIncidents(notRunning, NOW).length, 0, "idle sequence suppresses the af incident");

  const running = baseInputs();
  running.sequence = { state: "running" };
  running.lastAutofocusResult = { failed: true };
  const list = deriveIncidents(running, NOW);
  eq(list[0].kind, "af");
  eq(list[0].pill, "RECOVERING");
});

test("the server's own focus message wins over the generic sentence", () => {
  const inp = baseInputs();
  inp.sequence = { state: "running" };
  inp.focus = { state: "failed", message: "only 3 stars in the field" };
  eq(deriveIncidents(inp, NOW)[0].engine, "only 3 stars in the field");
});

// ------------------------------------------------------------------- guide

test("guide incident fires when the guider's own phase says the star is lost", () => {
  const inp = baseInputs();
  inp.guide = { guiding: true, phase: "lost" };
  eq(deriveIncidents(inp, NOW)[0].kind, "guide");
});

test("a stopped guider is NOT a lost star", () => {
  // The guider is stopped through every slew, every autofocus and every filter
  // change. A card that fired on `guiding === false` would spend the night
  // announcing the run working normally.
  const inp = baseInputs();
  inp.sequence = { state: "running" };
  inp.guide = { guiding: false, phase: "idle" };
  eq(deriveIncidents(inp, NOW).length, 0);
});

test("the engine's re-centring detail raises the guide card and IS the ENGINE line", () => {
  const inp = baseInputs();
  inp.sequence = { state: "running", detail: "re-centring after guiding loss" };
  const list = deriveIncidents(inp, NOW);
  eq(list[0].kind, "guide");
  eq(list[0].engine, "re-centring after guiding loss");
});

// -------------------------------------------------------------------- disk

test("disk incident fires on the SERVER's own low flag and names the free space", () => {
  const inp = baseInputs();
  inp.disk = { free_gb: 1.5, low: true, critical: false };
  const list = deriveIncidents(inp, NOW);
  eq(list[0].kind, "disk");
  eq(list[0].pill, "RECOVERING");
  ok(/DISK LOW · 1\.5 GB/.test(list[0].title), `title: "${list[0].title}"`);
  eq(list[0].color, "#ffb454");
});

test("DISK CRITICAL is a different card: its own title and the red colour", () => {
  const inp = baseInputs();
  inp.disk = { free_gb: 0.4, low: true, critical: true };
  const list = deriveIncidents(inp, NOW);
  ok(/DISK CRITICAL · 0\.4 GB/.test(list[0].title), `title: "${list[0].title}"`);
  eq(list[0].color, "#ff5470", "a critical disk is not amber:");
});

test("disk incident does not fire while the server says neither low nor critical", () => {
  const inp = baseInputs();
  inp.disk = { free_gb: 1.0, low: false, critical: false };
  eq(deriveIncidents(inp, NOW).length, 0, "the threshold is the server's, not this module's");
});

// ------------------------------------------------------------------ cooler

test("cooler fires on an explicit gate_open false, and names the setpoint", () => {
  const idle = baseInputs();
  idle.cooler = { gate_open: false, target_c: -10 };
  eq(deriveIncidents(idle, NOW).length, 0, "idle sequence suppresses the cooler incident");

  const running = baseInputs();
  running.sequence = { state: "running" };
  running.cooler = { gate_open: false, target_c: -10, temperature: -3.4, power: 99, can_report_power: true };
  const list = deriveIncidents(running, NOW);
  eq(list[0].kind, "cooler");
  eq(list[0].pill, "GATE CLOSED");
  ok(list[0].title.includes("-10"), `title names the setpoint, got "${list[0].title}"`);
  ok(/99% power/.test(list[0].engine), `the power figure belongs in ENGINE: "${list[0].engine}"`);
  ok(/-3\.4/.test(list[0].engine), `so does where the sensor actually is: "${list[0].engine}"`);
});

test("with no gate flag the gate is derived from `cooling to ...` plus at_target", () => {
  const cooling = baseInputs();
  cooling.sequence = { state: "running", detail: "cooling to -10C" };
  cooling.cooler = { on: true, at_target: false, target_c: -10, temperature: -3.4 };
  eq(deriveIncidents(cooling, NOW)[0].kind, "cooler");

  const settled = baseInputs();
  settled.sequence = { state: "running", detail: "cooling to -10C" };
  settled.cooler = { on: true, at_target: true, target_c: -10, temperature: -10 };
  eq(deriveIncidents(settled, NOW).length, 0, "a sensor that reached the setpoint re-opens the gate");

  const capturing = baseInputs();
  capturing.sequence = { state: "running", detail: "capturing L 120s" };
  capturing.cooler = { on: true, at_target: false, target_c: -10, temperature: -3.4 };
  eq(deriveIncidents(capturing, NOW).length, 0,
    "a warm sensor while the engine is NOT gating capture is a vitals reading, not an incident");
});

test("a camera that cannot report power prints no power figure", () => {
  const inp = baseInputs();
  inp.sequence = { state: "running" };
  inp.cooler = { gate_open: false, target_c: -10, temperature: -3.4, power: null, can_report_power: false };
  ok(!/%/.test(deriveIncidents(inp, NOW)[0].engine), "a number with nothing behind it must not be printed");
});

// ------------------------------------------------------------------- stall

test("stall fires at 3x the exposure + 20 s, and only while running", () => {
  // The margin is `STALL_MARGIN_S` from lib/eta.ts, through `stallLevel` - the
  // SAME rule the Monitor's stall chip uses, so the two cannot disagree.
  const expectedFrameS = 60;
  const thresholdMs = (3 * expectedFrameS + 20) * 1000; // 200000

  const justUnder = baseInputs();
  justUnder.sequence = { state: "running" };
  justUnder.expectedFrameS = expectedFrameS;
  justUnder.lastCaptureAtMs = NOW - thresholdMs; // exactly at threshold: not yet a stall
  eq(deriveIncidents(justUnder, NOW).length, 0);

  const over = baseInputs();
  over.sequence = { state: "running" };
  over.expectedFrameS = expectedFrameS;
  over.lastCaptureAtMs = NOW - thresholdMs - 1000;
  const list = deriveIncidents(over, NOW);
  eq(list[0].kind, "stall");
  eq(list[0].pill, "NO PROGRESS");
  eq(list[0].sinceMs, over.lastCaptureAtMs);

  const paused = baseInputs();
  paused.sequence = { state: "paused" };
  paused.expectedFrameS = expectedFrameS;
  paused.lastCaptureAtMs = NOW - thresholdMs - 1000;
  eq(deriveIncidents(paused, NOW).length, 0,
    "a paused run's frame gap is explained by the pause, not a fault");
});

test("a stall outranks a cloud hold - the one that needs a person gets the card", () => {
  const inp = baseInputs();
  inp.sequence = { state: "running", sky: { holding: true } };
  inp.expectedFrameS = 60;
  inp.lastCaptureAtMs = NOW - 400_000;
  const list = deriveIncidents(inp, NOW);
  eq(list.map((i) => i.kind).join(","), "stall,cloud", "kinds, most severe first:");
});

// ---------------------------------------------------------------- the order

test("severity order is fixed: safety, link, solve, stall, cooler, guide, af, disk, cloud", () => {
  const inp = baseInputs();
  inp.safety = { is_safe: false, reason: "rain" };
  inp.wsPhase = "down";
  inp.mountOp = { stuck: true };
  inp.sequence = { state: "running", sky: { holding: true } };
  inp.guide = { phase: "lost" };
  inp.focus = { state: "failed" };
  inp.disk = { free_gb: 1.2, low: true, critical: false };
  inp.cooler = { gate_open: false, target_c: -10 };
  inp.expectedFrameS = 60;
  inp.lastCaptureAtMs = NOW - 400_000;
  const list = deriveIncidents(inp, NOW);
  eq(list.map((i) => i.kind).join(","),
    "safety,link,solve,stall,cooler,guide,af,disk,cloud");
});

// ------------------------------------------------------- promises and verbs

/** Every input turned on at once, so the sweeps below see all nine cards. */
function everyKind(): IncidentInputs {
  const inp = baseInputs();
  inp.safety = { is_safe: false, reason: "rain", source: "boltwood", ts: NOW - 5000 };
  inp.wsPhase = "down";
  inp.mountOp = { stuck: true, error_arcmin: 4.2, attempt: 3 };
  inp.sequence = { state: "running", sky: { holding: true, text: "4 stars" } };
  inp.guide = { guiding: false, phase: "lost" };
  inp.focus = { state: "failed" };
  inp.disk = { free_gb: 1.2, low: true, critical: false };
  inp.cooler = { gate_open: false, target_c: -10, temperature: -3.4 };
  inp.expectedFrameS = 60;
  inp.lastCaptureAtMs = NOW - 400_000;
  return inp;
}

test("the sweep fixture really raises every kind - otherwise the sweeps are vacuous", () => {
  const kinds = deriveIncidents(everyKind(), NOW).map((i) => i.kind).sort().join(",");
  eq(kinds, "af,cloud,cooler,disk,guide,link,safety,solve,stall");
});

test("no card offers an action the server has no verb for", () => {
  // Verified against `server/astrodeck/api/app.py` by
  // `hubs/session/__tests__/nowIncidentActions.test.ts`, which reads the routes
  // out of the source. These five (and the stall's RESUME, which could only
  // 409 on a run that is by definition still running) have none.
  const verbless = [
    "switch_target", "switch", "skip_target", "skip",
    "keep_last_good", "useLast", "continue_unguided", "unguided",
    "switch_relay", "relay", "resume_run",
  ];
  for (const inc of deriveIncidents(everyKind(), NOW)) {
    for (const a of inc.actions) {
      ok(!verbless.includes(a.id), `${inc.kind} offers "${a.id}", which fires nothing`);
    }
    ok(inc.actions.length > 0, `${inc.kind} has no actions at all`);
  }
});

test("no card promises a number the engine does not have", () => {
  const forbidden = [
    /3 clear frames/i,          // the prototype's, not the engine's
    /30 minutes after/i,        // there is no 30-minute safety re-arm
    /relay after 30 seconds/i,  // there is no client-side transport switch
    /blind solve/i,             // the engine publishes no such ladder
  ];
  for (const inc of deriveIncidents(everyKind(), NOW)) {
    for (const re of forbidden) {
      ok(!re.test(inc.engine), `${inc.kind} ENGINE says ${re}: "${inc.engine}"`);
      ok(!re.test(inc.next), `${inc.kind} NEXT says ${re}: "${inc.next}"`);
    }
  }
});

test("incidentColor matches each kind's README hex", () => {
  eq(incidentColor("safety"), "#ff5470");
  eq(incidentColor("link"), "#7683a5");
  eq(incidentColor("solve"), "#ff5470");
  eq(incidentColor("cloud"), "#ffb454");
  eq(incidentColor("guide"), "#ffb454");
  eq(incidentColor("af"), "#ffb454");
  eq(incidentColor("cooler"), "#ffb454");
  eq(incidentColor("disk"), "#ffb454");
  eq(incidentColor("stall"), "#ffb454");
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
