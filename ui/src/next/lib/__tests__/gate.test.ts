// Pure-lib test for gate.ts. Sabotage check: reordering the priority checks
// (e.g. testing cap before link) turns "link down beats a cap block" red;
// dropping the roleLabel translation table turns the telescope/switch/
// filterwheel mapping tests red; hardcoding the busy-lane sentence instead of
// deferring to lib/humanize.ts turns "known lane uses the humanize sentence"
// red; treating `busyLane`'s mere PRESENCE as busy (instead of reading
// `status.busy_lanes`/`status.busy`) turns "a lane not listed as busy is
// unlocked" red; dropping the BUSY_WORD_FOR_LANE fallback turns the four
// collapsed-word mapping tests red; moving the `needsLan` rule BELOW the cap
// rule turns "LAN-only beats the capability sentence" red (an admin on the
// relay would be told they need admin access); moving it ABOVE the link rule
// turns "link down still wins over LAN-only" red; treating `needsLan`'s mere
// presence as blocking (instead of reading `s.onRelay`) turns "a LAN origin
// does not lock a LAN-only control" red; dropping `isLocalOnly`'s `code`
// comparison turns the two `isLocalOnly` tests red.
//
// gate.ts is pure, but it imports ui/src/lib/caps.ts (ARCHITECTURE.md #8:
// "using lib/caps helpers"), and caps.ts imports the real zustand store,
// which reads `window.location` at MODULE LOAD (lib/base.ts). So — same
// convention as lib/__tests__/authGate.test.ts — install minimal browser
// stubs on globalThis BEFORE anything imports the store, then dynamic-
// `import()` the runtime values (type-only imports are erased, so they stay
// static).
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
}
const g = globalThis as unknown as { localStorage?: Storage; document?: unknown; window?: unknown };
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

const { lockReason, roleLabel, LOCAL_ONLY_REASON, isLocalOnly } = await import("../gate");
const { humanizeLaneConflict } = await import("../../../lib/humanize");
import type { GateInput, GateStoreSlice } from "../gate";
import type { Principal } from "../../../types";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }

function viewer(): Principal { return { role: "viewer", email: null, caps: [] }; }
function admin(): Principal { return { role: "admin", email: "a@x.test", caps: ["view.status", "control.power", "control.mount"] }; }

function slice(over: Partial<GateStoreSlice> = {}): GateStoreSlice {
  return {
    principal: viewer(),
    status: null,
    equipConnected: false,
    wsPhase: "up",
    ...over,
  };
}

/** A minimal RigStatus-shaped object carrying only what lockReason reads —
 *  cast through `unknown` (same pattern as the backend_links test below)
 *  rather than filling in RigStatus's many other required/optional fields. */
function statusWith(over: { busy_lanes?: string[]; busy?: string | null }): GateStoreSlice["status"] {
  return { connected: {}, looping: false, ...over } as unknown as GateStoreSlice["status"];
}

test("unlocked when nothing in the input is blocked", () => {
  eq(lockReason({}, slice()), null);
});

test("link down: \"the rig is not reachable\", and it wins over every other reason (priority order)", () => {
  const inp: GateInput = { cap: "control.power", needsRole: "camera", busyLane: "solve", extra: "a flow owns the mount" };
  eq(lockReason(inp, slice({ wsPhase: "down", principal: viewer() })), "the rig is not reachable");
  eq(lockReason(inp, slice({ wsPhase: "reconnecting" })), "the rig is not reachable");
});

test("cap: \"needs <accessPhrase>\" when the principal lacks it, and it beats role/busy/extra", () => {
  const inp: GateInput = { cap: "control.power", needsRole: "camera", busyLane: "solve" };
  eq(lockReason(inp, slice({ principal: viewer() })), "needs admin access");
});

test("cap: null (unlocked by the cap check) once the principal holds it", () => {
  const inp: GateInput = { cap: "control.power" };
  eq(lockReason(inp, slice({ principal: admin() })), null);
});

test("role: telescope maps to \"mount\", switch to \"power box\", filterwheel to \"filter wheel\", safety to \"safety monitor\"", () => {
  eq(roleLabel("telescope"), "mount");
  eq(roleLabel("switch"), "power box");
  eq(roleLabel("filterwheel"), "filter wheel");
  // The two roles whose id is a common English word: without a case here they
  // fall through to "connect a switch first" / "connect a safety first", which
  // name nothing the user can go and plug in.
  eq(roleLabel("safety"), "safety monitor");
  eq(roleLabel("camera"), "camera", "unmapped roles pass through unchanged");
  eq(roleLabel("guider"), "guider");
  eq(roleLabel("focuser"), "focuser");
  eq(roleLabel("rotator"), "rotator");
});

test("role: \"connect a <role> first\" when the role is not connected, via lib/caps.ts's equipConnected fallback", () => {
  eq(
    lockReason({ needsRole: "telescope" }, slice({ status: null, equipConnected: false })),
    "connect a mount first",
  );
  eq(
    lockReason({ needsRole: "switch" }, slice({ status: null, equipConnected: false })),
    "connect a power box first",
  );
  eq(
    lockReason({ needsRole: "safety" }, slice({ status: null, equipConnected: false })),
    "connect a safety monitor first",
  );
});

test("role: connected (via equipConnected) unlocks the role check", () => {
  eq(lockReason({ needsRole: "telescope" }, slice({ status: null, equipConnected: true })), null);
});

test("role: a mapped backend_links entry overrides the equipConnected fallback", () => {
  const withLink = slice({
    status: { connected: {}, looping: false, backend_links: [{ role: "telescope", connected: true, error: null }] } as unknown as GateStoreSlice["status"],
    equipConnected: false,
  });
  eq(lockReason({ needsRole: "telescope" }, withLink), null);
});

test("busy lane: declarative — naming a lane that ISN'T busy unlocks the control (null)", () => {
  eq(lockReason({ busyLane: "solve" }, slice({ status: statusWith({ busy_lanes: ["capture"] }) })), null);
  eq(lockReason({ busyLane: "solve" }, slice({ status: null })), null, "no status at all reads as not busy");
});

test("busy lane: listed in status.busy_lanes -> the lib/humanize.ts sentence for a mapped lane", () => {
  const expected = humanizeLaneConflict("'solve' is already running");
  eq(lockReason({ busyLane: "solve" }, slice({ status: statusWith({ busy_lanes: ["solve"] }) })), expected);
});

test("busy lane: listed in status.busy_lanes but unmapped in lib/humanize.ts falls back to \"<lane> is running\"", () => {
  eq(
    lockReason({ busyLane: "some_future_lane" }, slice({ status: statusWith({ busy_lanes: ["some_future_lane"] }) })),
    "some_future_lane is running",
  );
});

test("busy lane: the collapsed status.busy word is a fallback when busy_lanes is absent", () => {
  const cases: [string, string][] = [
    ["goto", "slewing"],
    ["solve", "solving"],
    ["autofocus", "focusing"],
    ["capture", "capturing"],
  ];
  for (const [lane, word] of cases) {
    const busy = lockReason({ busyLane: lane }, slice({ status: statusWith({ busy: word }) }));
    eq(busy !== null, true, `lane "${lane}" should read busy from collapsed word "${word}"`);
  }
});

test("busy lane: a collapsed status.busy word for a DIFFERENT lane does not unlock a block", () => {
  eq(lockReason({ busyLane: "solve" }, slice({ status: statusWith({ busy: "slewing" }) })), null);
});

test("busy lane: either signal is enough — busy_lanes empty but the collapsed word still matches -> busy", () => {
  const busy = lockReason({ busyLane: "solve" }, slice({ status: statusWith({ busy_lanes: [], busy: "solving" }) }));
  eq(busy !== null, true, "the collapsed word is an OR with busy_lanes, not overridden by an empty list");
});

// ------------------------------------------------------------- LAN-only (R8)
//
// The rig fences a dozen write families to the LAN (`app.py`'s
// `_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES` / `_REMOTE_LOCAL_ONLY_EXACT`) and
// answers 403 `code: "local_only"` to a tunnelled session. Before this rule
// existed those controls rendered armed over the relay and the refusal arrived
// only after the press.

test("LAN-only: the sentence is the one the review fixed, verbatim", () => {
  eq(
    LOCAL_ONLY_REASON,
    "This changes the rig's own settings, so it needs the LAN - you are connected through the relay.",
  );
});

test("LAN-only: a relay origin locks the control", () => {
  eq(lockReason({ needsLan: true }, slice({ onRelay: true })), LOCAL_ONLY_REASON);
});

test("LAN-only: a LAN origin does not lock a LAN-only control", () => {
  // Declarative, like `busyLane`: naming the fence does not mean this tab is
  // behind it. Getting this backwards would lock every fenced control on the
  // LAN UI, which is the only place they were ever meant to work.
  eq(lockReason({ needsLan: true }, slice({ onRelay: false })), null);
  eq(lockReason({ needsLan: true }, slice({})), null, "no relay field reads as the LAN:");
  eq(lockReason({}, slice({ onRelay: true })), null, "a control that needs no LAN is untouched:");
});

test("LAN-only: link down still wins over LAN-only (priority order)", () => {
  // With the socket down, the origin is not why the button cannot be pressed.
  eq(
    lockReason({ needsLan: true, cap: "control.power" }, slice({ wsPhase: "down", onRelay: true })),
    "the rig is not reachable",
  );
});

test("LAN-only: beats the capability sentence, and role/busy/extra under it", () => {
  // The fence refuses EVERY role, an admin included, so a capability sentence
  // here would name a blocker that is not the blocker: the same press works
  // for the same principal on the LAN.
  const inp: GateInput = {
    needsLan: true, cap: "control.power", needsRole: "camera", busyLane: "solve",
    extra: "should not show",
  };
  eq(lockReason(inp, slice({ principal: viewer(), onRelay: true })), LOCAL_ONLY_REASON);
  eq(lockReason(inp, slice({ principal: admin(), onRelay: true })), LOCAL_ONLY_REASON,
    "an admin on the relay is refused for the origin, not the role:");
  // And with the relay out of the picture the cap sentence is back.
  eq(lockReason(inp, slice({ principal: viewer(), onRelay: false })), "needs admin access");
});

test("isLocalOnly: only a `local_only` code, and nothing else, is the fence", () => {
  eq(isLocalOnly({ code: "local_only", status: 403 }), true);
  eq(isLocalOnly({ code: "forbidden", status: 403 }), false,
    "an RBAC 403 must still get the capability sentence:");
  eq(isLocalOnly({ status: 403 }), false, "a 403 with no code is not this one:");
});

test("isLocalOnly: survives whatever a catch block actually hands it", () => {
  eq(isLocalOnly(null), false);
  eq(isLocalOnly(undefined), false);
  eq(isLocalOnly("local_only"), false, "a bare string has no code field:");
  eq(isLocalOnly(new Error("boom")), false);
  const e = Object.assign(new Error("this security-sensitive operation is LAN-only"), {
    status: 403, code: "local_only",
  });
  eq(isLocalOnly(e), true, "the real ApiError shape the fence produces:");
});

test("extra: returned verbatim when nothing else blocks", () => {
  eq(lockReason({ extra: "a flow owns the mount" }, slice()), "a flow owns the mount");
});

test("extra: does not override link/cap/role/busy (lowest priority)", () => {
  eq(
    lockReason({ cap: "control.power", extra: "should not show" }, slice({ principal: viewer() })),
    "needs admin access",
  );
});

console.log(`${passed} passed, ${failed} failed`);
if (failed) {
  for (const f of failures) console.error(f);
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
export { passed, failed };
export const total = passed + failed;
