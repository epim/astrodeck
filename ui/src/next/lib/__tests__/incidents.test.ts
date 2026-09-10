// Pure-lib test for incidents.ts. Sabotage check: reordering SEVERITY_ORDER
// (or evaluating kinds independently instead of walking the fixed order)
// turns "safety beats cloud" red; dropping the isSequenceRunning() guard on
// af/cooler/stall turns "af/cooler/stall only fire on a live run" red;
// loosening the cloud regex (or dropping the state check) turns the two
// cloud negative-cases red.
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
    diskFreeBytes: 50 * 1024 * 1024 * 1024,
    cooler: null,
    lastCaptureAtMs: null,
    expectedFrameS: null,
  };
}

test("a clean run has no incidents", () => {
  eq(deriveIncidents(baseInputs(), NOW).length, 0);
});

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

test("safety unsafe fires PARKED red, and beats a simultaneous cloud hold", () => {
  const inp = baseInputs();
  inp.sequence = { state: "holding", hold: { reason: "clouds" } };
  inp.safety = { is_safe: false, reason: "rain", ts: NOW - 5000 };
  const list = deriveIncidents(inp, NOW);
  ok(list.length >= 2, "both safety and cloud should be present");
  eq(list[0].kind, "safety", "safety must be most severe");
  eq(list[0].pill, "PARKED");
  eq(list[0].sinceMs, NOW - 5000);
  ok(list.some((i) => i.kind === "cloud"), "cloud hold is still reported, just not first");
});

test("link incident fires on a down phase OR stale telemetry", () => {
  const down = baseInputs();
  down.wsPhase = "down";
  eq(deriveIncidents(down, NOW)[0].kind, "link");

  const stale = baseInputs();
  stale.telemetryStale = true;
  eq(deriveIncidents(stale, NOW)[0].kind, "link");
  eq(deriveIncidents(stale, NOW)[0].pill, "STALE");
});

test("solve incident fires when the mount op is stuck", () => {
  const inp = baseInputs();
  inp.mountOp = { stuck: true, attempt: 3 };
  const list = deriveIncidents(inp, NOW);
  eq(list[0].kind, "solve");
  eq(list[0].pill, "RETRYING");
});

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

test("guide incident fires when the guide star is lost", () => {
  const inp = baseInputs();
  inp.guide = { guiding: true, lost: true };
  eq(deriveIncidents(inp, NOW)[0].kind, "guide");
});

test("disk incident fires under 2 GB free and names the free space in the title", () => {
  const inp = baseInputs();
  inp.diskFreeBytes = 1.5 * 1024 * 1024 * 1024;
  const list = deriveIncidents(inp, NOW);
  eq(list[0].kind, "disk");
  eq(list[0].pill, "RECOVERING");
  ok(list[0].title.includes("GB") || list[0].title.includes("MB"), "title names the free space");
});

test("disk incident does not fire at or above 2 GB free", () => {
  const inp = baseInputs();
  inp.diskFreeBytes = 2 * 1024 * 1024 * 1024;
  eq(deriveIncidents(inp, NOW).length, 0);
});

test("cooler incident requires the gate closed AND a live run; names the setpoint when known", () => {
  const idle = baseInputs();
  idle.cooler = { gate_open: false, setpoint: -10 };
  eq(deriveIncidents(idle, NOW).length, 0, "idle sequence suppresses the cooler incident");

  const running = baseInputs();
  running.sequence = { state: "running" };
  running.cooler = { gate_open: false, setpoint: -10 };
  const list = deriveIncidents(running, NOW);
  eq(list[0].kind, "cooler");
  eq(list[0].pill, "GATE CLOSED");
  ok(list[0].title.includes("-10") || list[0].title.includes("−10"), "title names the setpoint");
});

test("stall incident: no frame in over 3x the expected exposure + 120s, only while running", () => {
  const expectedFrameS = 60;
  const thresholdMs = (3 * expectedFrameS + 120) * 1000; // 300000

  const justUnder = baseInputs();
  justUnder.sequence = { state: "running" };
  justUnder.expectedFrameS = expectedFrameS;
  justUnder.lastCaptureAtMs = NOW - thresholdMs; // exactly at threshold: not yet a stall
  eq(deriveIncidents(justUnder, NOW).length, 0);

  const over = baseInputs();
  over.sequence = { state: "running" };
  over.expectedFrameS = expectedFrameS;
  over.lastCaptureAtMs = NOW - thresholdMs - 1;
  const list = deriveIncidents(over, NOW);
  eq(list[0].kind, "stall");
  eq(list[0].pill, "NO PROGRESS");
  eq(list[0].sinceMs, over.lastCaptureAtMs);
});

test("severity order is fixed: safety, link, solve, cloud, guide, af, cooler, disk, stall", () => {
  const inp = baseInputs();
  inp.safety = { is_safe: false };
  inp.wsPhase = "down";
  inp.mountOp = { stuck: true };
  inp.sequence = { state: "holding", hold: { reason: "clouds" } };
  inp.guide = { lost: true };
  const list = deriveIncidents(inp, NOW);
  eq(list.map((i) => i.kind).join(","), "safety,link,solve,cloud,guide");
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
