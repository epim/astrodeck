// safetyChain.test.ts - the WHEN A LIMIT TRIPS chain, built from config.
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/safetyChain.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. The prototype draws the same five nodes on every rig. This
// module exists so the picture matches the engine, and the assertions below are
// the four ways that can go wrong:
//
//   1. A chain that draws a teardown a `warn` rig will never perform.
//   2. A CLOSE node lit on a rig with no roof - the flag is set, the device is
//      not there, and nothing closes.
//   3. A NOTIFY node lit with no alert channel - the one node whose failure is
//      invisible until the morning after.
//   4. A WARM COOLER node lit while `cooling.warm_ramp` is off, which is the
//      pre-2026-08-04 bug (the TEC cut dead) drawn as a promise.
//
// Pure module, printed tally + the counts export (shell-and-tests.md section 4).

import {
  CLOSE_FLAG_OFF_REASON, NO_RAMP_REASON, NO_ROOF_REASON, NO_SINK_REASON,
  chainReasons, safetyChain, type ChainInput,
} from "../lib/safetyChain";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const FULL: ChainInput = {
  onUnsafe: "abort_park_warm",
  closeDomeOnUnsafe: true,
  domeConnected: true,
  warmRamp: true,
  hasSink: true,
};

const labels = (i: ChainInput) => safetyChain(i).map((n) => n.label);
const lit = (i: ChainInput) => safetyChain(i).filter((n) => n.lit).map((n) => n.label);
const node = (i: ChainInput, id: string) => safetyChain(i).find((n) => n.id === id);

// ------------------------------------------------------------------ warn

test("warn draws LOG IT and nothing that stops the run", () => {
  const nodes = labels({ ...FULL, onUnsafe: "warn" });
  // The precondition and the point in one: the teardown words must be ABSENT,
  // not merely dim. On a warn rig nothing stops, parks or closes, and a dim
  // PARK would still read as "it parks, just not right now".
  assert(nodes.includes("LOG IT"), `warn lost its LOG IT node - got ${JSON.stringify(nodes)}`);
  for (const gone of ["STOP CAPTURE", "PARK", "CLOSE", "WARM COOLER"]) {
    assert(!nodes.includes(gone), `warn still draws ${gone}, which the engine will not do`);
  }
});

test("warn with no alert channel lights LOG IT alone", () => {
  eq(lit({ ...FULL, onUnsafe: "warn", hasSink: false }).join(" "), "LOG IT",
    "a warn rig with no sink lit something other than LOG IT");
});

// ------------------------------------------------------------------ pause

test("pause pauses capture and never parks", () => {
  const nodes = labels({ ...FULL, onUnsafe: "pause" });
  eq(nodes.join(" -> "), "PAUSE CAPTURE -> NOTIFY", "the pause chain is not what the engine does");
});

// ------------------------------------------------------------------ park

test("park stops, parks, and offers CLOSE", () => {
  eq(labels({ ...FULL, onUnsafe: "park" }).join(" -> "),
    "STOP CAPTURE -> PARK -> CLOSE -> NOTIFY",
    "the park chain lost or gained a node");
  // ...but never the cooler ramp, which only abort_park_warm runs.
  assert(!labels({ ...FULL, onUnsafe: "park" }).includes("WARM COOLER"),
    "park draws WARM COOLER - it does not warm the camera, that is what park-and-warm is for");
});

// ------------------------------------------------- abort_park_warm, all lit

test("park and warm with a roof, a ramp and a channel lights all five", () => {
  const nodes = safetyChain(FULL);
  eq(nodes.length, 5, "the full chain is not five nodes");
  eq(nodes.filter((n) => n.lit).length, 5, `a node is dim on a rig that has everything: ${
    JSON.stringify(nodes.filter((n) => !n.lit))}`);
  eq(nodes.map((n) => n.label).join(" -> "),
    "STOP CAPTURE -> PARK -> CLOSE -> WARM COOLER -> NOTIFY",
    "the full chain is not in the engine's order");
  eq(chainReasons(nodes).length, 0, "a fully lit chain still printed a reason");
});

// --------------------------------------------------------- the dim reasons

test("no roof dims CLOSE and says so", () => {
  const n = node({ ...FULL, domeConnected: false }, "close");
  assert(n != null, "the CLOSE node vanished instead of dimming");
  eq(n?.lit, false, "CLOSE is lit on a rig with no roof connected");
  eq(n?.reason, NO_ROOF_REASON, "CLOSE is dim for an unstated reason");
});

test("the flag off dims CLOSE with a DIFFERENT reason from no roof", () => {
  const n = node({ ...FULL, closeDomeOnUnsafe: false }, "close");
  eq(n?.lit, false, "CLOSE is lit while close-on-unsafe is switched off");
  eq(n?.reason, CLOSE_FLAG_OFF_REASON, "a switched-off flag reads as a missing device");
  // `String(...)` because both are literal types and tsc rejects the comparison
  // as "no overlap" - which is the compiler agreeing with the assertion, but it
  // still has to hold at runtime if either constant is ever edited.
  assert(String(CLOSE_FLAG_OFF_REASON) !== String(NO_ROOF_REASON),
    "the two CLOSE reasons are the same string - one of them is untrue on any given rig");
});

test("no roof beats the flag: the reason that cannot be fixed in this app wins", () => {
  eq(node({ ...FULL, domeConnected: false, closeDomeOnUnsafe: false }, "close")?.reason,
    NO_ROOF_REASON, "with neither a roof nor the flag, the settable reason is shown first");
});

test("no alert channel dims NOTIFY and says so", () => {
  const n = node({ ...FULL, hasSink: false }, "notify");
  eq(n?.lit, false, "NOTIFY is lit with no channel configured - the morning-after failure");
  eq(n?.reason, NO_SINK_REASON, "NOTIFY is dim for an unstated reason");
});

test("the warm ramp off dims WARM COOLER and names the consequence", () => {
  const n = node({ ...FULL, warmRamp: false }, "warm");
  eq(n?.lit, false, "WARM COOLER is lit while the ramp is off - the TEC is cut dead");
  eq(n?.reason, NO_RAMP_REASON, "WARM COOLER is dim for an unstated reason");
});

test("chainReasons returns every dim node's reason, in chain order", () => {
  const rs = chainReasons(safetyChain({ ...FULL, domeConnected: false, warmRamp: false, hasSink: false }));
  eq(rs.length, 3, `expected three reasons, got ${JSON.stringify(rs)}`);
  eq(rs[0], NO_ROOF_REASON, "the reasons are not in chain order");
  eq(rs[1], NO_RAMP_REASON, "the reasons are not in chain order");
  eq(rs[2], NO_SINK_REASON, "the reasons are not in chain order");
});

test("every dim node carries a reason and every lit node carries none", () => {
  const cases: ChainInput[] = [
    FULL,
    { ...FULL, onUnsafe: "warn" }, { ...FULL, onUnsafe: "pause" }, { ...FULL, onUnsafe: "park" },
    { ...FULL, domeConnected: false }, { ...FULL, hasSink: false }, { ...FULL, warmRamp: false },
  ];
  for (const c of cases) {
    for (const n of safetyChain(c)) {
      assert(n.lit === (n.reason == null),
        `${n.label} is ${n.lit ? "lit with" : "dim without"} a reason on ${JSON.stringify(c)}`);
    }
  }
});

const total = passed + failed;
console.log(`safetyChain.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
