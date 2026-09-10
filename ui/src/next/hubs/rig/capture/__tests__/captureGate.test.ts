// captureGate.test.ts - every row of the manual bench's refusal table.
//
//   Run directly:  npx tsx src/next/hubs/rig/capture/__tests__/captureGate.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHY EACH ROW EARNS A TEST. These are the sentences that stand between an
// operator and a frame that cannot be taken, and each one of them was a silent
// swallow first: a control that looked live, ate the tap and explained nothing.
// The two that matter most are ORDER, not presence -
//
//   * link down beats everything, because a reason about capabilities is a lie
//     when the browser cannot reach the rig at all;
//   * the CAPABILITY sentence beats "connect a camera first", because a viewer
//     who plugs in a camera still cannot capture and the second sentence would
//     send them to fix the wrong thing.
//
// `captureGate.ts` reaches `lib/caps.ts`, which imports the store, which reads
// localStorage and document at module scope - hence the minimal browser stubs
// installed BEFORE the dynamic import (shell-and-tests.md section 4's
// store-touching convention).

class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as {
  localStorage?: Storage; document?: unknown; window?: unknown;
};
if (typeof g.localStorage === "undefined") g.localStorage = new MemStorage() as unknown as Storage;
if (typeof g.document === "undefined") {
  const classList = { toggle() {}, add() {}, remove() {}, contains() { return false; } };
  const style = { setProperty() {}, getPropertyValue() { return ""; } };
  g.document = { documentElement: { classList, style } };
}
if (typeof g.window === "undefined") {
  g.window = {
    location: { pathname: "/", protocol: "http:", host: "test" },
    setTimeout: globalThis.setTimeout.bind(globalThis),
    clearTimeout: globalThis.clearTimeout.bind(globalThis),
    setInterval: globalThis.setInterval.bind(globalThis),
    clearInterval: globalThis.clearInterval.bind(globalThis),
    addEventListener() {},
    removeEventListener() {},
  };
}

const gate = await import("../captureGate");
const {
  accessReason, exposeReason, singleReason, loopReason, liveViewReason, stopReason,
  resetStackReason, coolerReason, warmReason, slewReason, isGainInvalid,
  isCoolerTargetInvalid, sequenceNotice, SESSION_OWNS_MOUNT_REASON,
  NEEDS_CAPTURE_REASON, POLAR_REASON, SEQUENCE_REASON, EXPOSURE_FIX_REASON,
  GAIN_FIX_REASON, PENDING_REASON, LOOP_RUNNING_REASON, NO_STACK_REASON,
  COOLER_OFF_REASON, COOLER_RANGE_REASON, VIDEO_LOCK_REASON,
} = gate;
type CaptureGateInput = import("../captureGate").CaptureGateInput;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ fixtures
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.mount"],
} as unknown as CaptureGateInput["principal"];
const VIEWER = {
  role: "viewer", email: null, caps: ["view.status", "view.preview"],
} as unknown as CaptureGateInput["principal"];

const STATUS = {
  connected: { camera: { connected: true } },
  looping: false,
  camera: { temperature: -10, can_cool: true, width: 100, height: 100, max_gain: 500 },
} as unknown as CaptureGateInput["status"];

function inp(over: Partial<CaptureGateInput> = {}): CaptureGateInput {
  return {
    principal: OPERATOR,
    status: STATUS,
    equipConnected: true,
    wsPhase: "up",
    polarBusy: false,
    seqState: "idle",
    exposureRaw: "60",
    gainRaw: "120",
    maxGain: 500,
    looping: false,
    pending: null,
    ...over,
  };
}

// ------------------------------------------------------------------- the rows
test("clean: an operator with a connected camera and sane numbers is not blocked", () => {
  eq(exposeReason(inp()), null, "a usable bench refused a shot");
});

test("link down outranks every other reason, including a viewer's", () => {
  eq(exposeReason(inp({ wsPhase: "down", principal: VIEWER, polarBusy: true })),
    "the rig is not reachable",
    "a reason about roles was given for a rig this browser cannot reach");
});

test("viewer: the capability sentence names the VERB, not just the role", () => {
  eq(exposeReason(inp({ principal: VIEWER })), NEEDS_CAPTURE_REASON, "wrong viewer reason");
  eq(NEEDS_CAPTURE_REASON, "Capturing needs operator or admin access.",
    "the capability sentence drifted from accessPhrase's role table");
});

test("capability outranks a disconnected camera", () => {
  eq(exposeReason(inp({ principal: VIEWER, equipConnected: false, status: null })),
    NEEDS_CAPTURE_REASON,
    "a viewer was told to plug in a camera, which would not help them");
});

test("no camera: the role sentence comes from lib/gate, not from here", () => {
  eq(exposeReason(inp({ equipConnected: false, status: { connected: {}, looping: false } as unknown as CaptureGateInput["status"] })),
    "connect a camera first", "wrong disconnected-camera reason");
});

test("polar alignment owns the camera", () => {
  eq(exposeReason(inp({ polarBusy: true })), POLAR_REASON, "polar did not block a shot");
});

test("a PAUSED sequence still holds the camera, exactly like a running one", () => {
  eq(exposeReason(inp({ seqState: "running" })), SEQUENCE_REASON, "running did not block");
  eq(exposeReason(inp({ seqState: "paused" })), SEQUENCE_REASON,
    "a paused sequence was treated as having released the camera - it has not");
});

test("polar outranks the sequence: both true, polar is the sentence", () => {
  eq(exposeReason(inp({ polarBusy: true, seqState: "running" })), POLAR_REASON, "wrong order");
});

test("exposure: blank, zero, negative and scientific notation all block", () => {
  for (const raw of ["", "   ", "0", "-3", "1e9", "abc"]) {
    eq(exposeReason(inp({ exposureRaw: raw })), EXPOSURE_FIX_REASON,
      `exposure "${raw}" reached the shutter`);
  }
});

test("exposure: 3600 is allowed and 3601 is not", () => {
  eq(exposeReason(inp({ exposureRaw: "3600" })), null, "the ceiling itself was refused");
  eq(exposeReason(inp({ exposureRaw: "3601" })), EXPOSURE_FIX_REASON, "over the ceiling passed");
});

test("gain: blank, negative and over the camera's ceiling all block", () => {
  eq(exposeReason(inp({ gainRaw: "" })), GAIN_FIX_REASON, "a blank gain passed");
  eq(exposeReason(inp({ gainRaw: "-1" })), GAIN_FIX_REASON, "a negative gain passed");
  eq(exposeReason(inp({ gainRaw: "501", maxGain: 500 })), GAIN_FIX_REASON,
    "a gain over the advertised ceiling passed");
  eq(exposeReason(inp({ gainRaw: "500", maxGain: 500 })), null, "the ceiling itself was refused");
});

test("gain: 0 is a real setting, not a blank", () => {
  eq(isGainInvalid("0", 500), false, "gain 0 was treated as unset");
});

test("gain: no advertised ceiling means no ceiling check", () => {
  eq(isGainInvalid("9000", null), false, "a ceiling was invented for a camera that reports none");
});

test("exposure is checked before gain, so the first fix named is the first field", () => {
  eq(exposeReason(inp({ exposureRaw: "", gainRaw: "" })), EXPOSURE_FIX_REASON, "wrong order");
});

test("a start already in flight blocks a second one", () => {
  eq(exposeReason(inp({ pending: "loop" })), PENDING_REASON, "a second start was allowed");
});

test("singleReason adds the running loop; loopReason does not", () => {
  eq(singleReason(inp({ looping: true })), LOOP_RUNNING_REASON, "Single ignored a running loop");
  eq(loopReason(inp({ looping: true })), null,
    "Loop was blocked by its own loop - pressing it again is how you change the exposure");
});

test("Live View's OFF half is not gated on a pending start", () => {
  eq(liveViewReason(inp({ pending: "single" }), true), null,
    "ending a stack was refused because something else was starting");
  eq(liveViewReason(inp({ pending: "single" }), false), PENDING_REASON,
    "starting a stack ignored a start already in flight");
});

test("Live View's OFF half still needs the access floor", () => {
  eq(liveViewReason(inp({ principal: VIEWER }), true), NEEDS_CAPTURE_REASON,
    "a viewer was offered a stack stop");
});

test("STOP stays pressable through every condition the other gates refuse on", () => {
  eq(stopReason(inp({ looping: true, pending: "loop", polarBusy: true, seqState: "running" })),
    null, "STOP was locked by the very state it exists to end");
  eq(stopReason(inp({ principal: VIEWER })), NEEDS_CAPTURE_REASON, "a viewer got a live STOP");
});

test("RESET STACK says there is no stack rather than nothing", () => {
  eq(resetStackReason(inp(), false), NO_STACK_REASON, "reset was offered with no stack");
  eq(resetStackReason(inp(), true), null, "reset was refused with a stack running");
});

test("cooler: the range guard, and its bounds", () => {
  eq(coolerReason(inp(), "-10"), null, "a sane set-point was refused");
  eq(coolerReason(inp(), ""), COOLER_RANGE_REASON, "a blank set-point would have posted");
  eq(coolerReason(inp(), "x"), COOLER_RANGE_REASON, "a NaN set-point would have posted null");
  eq(coolerReason(inp(), "-61"), COOLER_RANGE_REASON, "under the floor passed");
  eq(coolerReason(inp(), "41"), COOLER_RANGE_REASON, "over the ceiling passed");
  eq(isCoolerTargetInvalid("-60"), false, "the floor itself was refused");
  eq(isCoolerTargetInvalid("40"), false, "the ceiling itself was refused");
});

test("warm: 'already off' only when there is genuinely nothing to warm", () => {
  eq(warmReason(inp(), { rampActive: false, coolerPresent: true, coolerOn: false }),
    COOLER_OFF_REASON, "WARM was live on a cooler that is already off");
  eq(warmReason(inp(), { rampActive: false, coolerPresent: true, coolerOn: true }),
    null, "WARM was refused on a running cooler");
  eq(warmReason(inp(), { rampActive: true, coolerPresent: true, coolerOn: false }),
    null,
    "STOP RAMP was refused mid-ramp because the cooler reads off - that is the escape hatch");
});

test("the sequence NOTICE and the sequence REASON are different sentences", () => {
  eq(sequenceNotice("paused"), "Sequence paused - camera reserved.", "wrong paused notice");
  eq(sequenceNotice("running"), "Sequence running - camera reserved.", "wrong running notice");
  eq(sequenceNotice("idle"), null, "an idle sequence produced a reservation notice");
  assert(sequenceNotice("running") !== SEQUENCE_REASON,
    "the notice and the button reason collapsed into one string");
});

test("the access floor is the same for every control on the screen", () => {
  eq(accessReason(inp({ principal: VIEWER })), NEEDS_CAPTURE_REASON, "wrong floor for a viewer");
  eq(accessReason(inp({ polarBusy: true, seqState: "running", looping: true })), null,
    "the floor picked up a reason that belongs to the shutter alone");
});

test("SLEW is gated on the MOUNT, not on the camera capability that runs the shutter", () => {
  const captureOnly = {
    role: "operator", email: "op@rig",
    caps: ["view.status", "view.preview", "control.capture"],
  } as unknown as CaptureGateInput["principal"];
  eq(exposeReason(inp({ principal: captureOnly })), null,
    "precondition: a control.capture holder cannot shoot");
  eq(slewReason(inp({ principal: captureOnly }), { runOwnsMount: false }),
    "Slewing needs operator or admin access.",
    "a caller without control.mount was offered a live SLEW");
  eq(slewReason(inp(), { runOwnsMount: false }), null, "an operator's SLEW was refused");
  eq(slewReason(inp(), { runOwnsMount: true }), SESSION_OWNS_MOUNT_REASON,
    "SLEW was live while a session had the mount");
});

test("SLEW needs the telescope connected, and says which device is missing", () => {
  eq(slewReason(inp({
    equipConnected: false,
    status: { connected: {}, looping: false } as unknown as CaptureGateInput["status"],
  }), { runOwnsMount: false }), "connect a mount first",
    "SLEW named the wrong missing device");
});

test("the VIDEO ruling says what the rig HAS, not only what it lacks", () => {
  eq(VIDEO_LOCK_REASON,
    "Video capture is not available on this rig yet - the camera surface here is "
    + "single frames, a loop and Live View stacking.",
    "the video reason drifted");
  assert(!/[—–]/.test(VIDEO_LOCK_REASON), "an em-dash or en-dash reached a UI string");
});

test("no reason on this screen carries an em-dash", () => {
  const strings = [
    NEEDS_CAPTURE_REASON, POLAR_REASON, SEQUENCE_REASON, EXPOSURE_FIX_REASON,
    GAIN_FIX_REASON, PENDING_REASON, LOOP_RUNNING_REASON, NO_STACK_REASON,
    COOLER_OFF_REASON, COOLER_RANGE_REASON, VIDEO_LOCK_REASON, SESSION_OWNS_MOUNT_REASON,
    sequenceNotice("paused") ?? "", sequenceNotice("running") ?? "",
    gate.POLAR_NOTICE, gate.NO_LAST_LIGHT_REASON,
  ];
  for (const s of strings) {
    assert(!/[—–]/.test(s), `an em-dash or en-dash reached: ${s}`);
  }
});

test("draftNumber shoots the typed text and falls back only for a label", () => {
  eq(gate.draftNumber("180", 30), 180, "a parsing draft is not the number it will shoot");
  eq(gate.draftNumber("0.5", 30), 0.5, "a fractional exposure was rounded away");
  // The two the gate refuses. The fallback is a LABEL, never a shot: an empty
  // or unbounded box is blocked by `exposeReason` before any press posts.
  eq(gate.draftNumber("", 30), 30, "a blank box rendered NaN instead of the last agreed number");
  eq(gate.draftNumber("  ", 30), 30, "a whitespace-only box is not blank");
  eq(gate.draftNumber("abc", 30), 30, "a non-numeric box rendered NaN");
  // "1e9" PARSES, so it comes back as itself - the bound is `isExposureInvalid`'s
  // job, not this one's, and splitting them here is what lets the box stay on
  // screen holding the bad value while the shutter refuses it.
  eq(gate.draftNumber("1e9", 30), 1e9, "an unbounded draft was silently repaired");
});

test("polar owns the camera from EITHER channel - our own session or the rig's lane", () => {
  assert(gate.isPolarBusy("running", []), "our own running alignment did not count");
  assert(gate.isPolarBusy("paused", []), "a paused alignment still holds the camera");
  assert(gate.isPolarBusy("idle", ["polar"]),
    "an alignment started on ANOTHER device did not count - the rig's own lane is the "
    + "only signal for it");
  assert(!gate.isPolarBusy("idle", ["capture"]), "an unrelated lane read as polar");
  assert(!gate.isPolarBusy("idle", undefined),
    "a server too old to publish busy_lanes blocked the camera forever");
  assert(!gate.isPolarBusy(null, null), "a null state read as busy");
});

const total = passed + failed;
console.log(`captureGate.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export default { passed, failed, total };
export { passed, failed, total };
