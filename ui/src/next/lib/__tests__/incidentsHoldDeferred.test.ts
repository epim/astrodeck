// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// incidentsHoldDeferred.test.ts - the cloud card for a hold the engine DEFERRED
// rather than opened (#221, #244). Pure: literal inputs, no DOM.
//
//   Run directly:  node --import tsx src/next/lib/__tests__/incidentsHoldDeferred.test.ts
//
// A scheduler wait under a closed sky opens no hold (it has no target to watch
// or point at) and publishes why in `sequence.sky.hold_deferred`. The card
// must carry that sentence as sent, only while it is non-null, and must not
// claim a hold: no HOLDING pill, no hold wording, no weather override that
// cannot touch a verdict read off the frames. Now's DOM test
// (`hubs/session/__tests__/nowRecoveryAndDeferralDom.test.tsx`) holds the
// same card through `refineIncident`.
//
// NAMED MUTANT, run from a byte copy of incidents.ts and restored
// byte-identical (sha256 checked); the observed failure is quoted at the test.
//   N2 "ignore hold_deferred"   `deferred` is never read off `sky`
//   B1 "the verdict back as ENGINE" the card's `engine` and `next` swapped back

import { CLOUD_DEFERRED_PILL, deriveIncidents, type IncidentInputs } from "../incidents";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = ""): void { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(cond: boolean, m: string): void { if (!cond) throw new Error(m); }

const NOW = 1_700_000_000_000;

/** Verbatim from `SequenceEngine._HOLD_DEFERRED`. */
const DEFERRED =
  "the frames say the sky has closed in, with no target set up to judge it "
  + "from, so no cloud hold is open; the next target's setup opens one if the "
  + "sky is still closed";
const SKY_TEXT = "cloudy, from a reading 40s old - 1 bright stars, low contrast";
/** Verbatim from `_note_hold_deferred`'s once-per-spell warning. */
const DEFERRED_LOG =
  "the frames say the sky has closed in, with no target set up to judge it "
  + "from - no cloud hold opens without one. The run goes on as it was (a wait "
  + "keeps its idle park-hold on the mount), and the next target's setup opens "
  + "the hold if the sky is still closed";

function inputs(sky: NonNullable<IncidentInputs["sequence"]["sky"]> | null): IncidentInputs {
  return {
    sequence: { state: "running", detail: "waiting for M31 to rise", sky },
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

const cloudy = (hold_deferred: string | null | undefined, holding = false) => ({
  cloudy: true, score: 0.12, reason: SKY_TEXT, text: SKY_TEXT, holding,
  ...(hold_deferred === undefined ? {} : { hold_deferred }),
});

// ------------------------------------------------------------------ controls

test("control: a cloudy wait with hold_deferred null raises nothing", () => {
  eq(deriveIncidents(inputs(cloudy(null)), NOW).length, 0, "incidents:");
});

test("control: a server that does not send the field raises nothing", () => {
  eq(deriveIncidents(inputs(cloudy(undefined)), NOW).length, 0, "incidents:");
});

test("control: an empty sentence is not a sentence", () => {
  eq(deriveIncidents(inputs(cloudy("")), NOW).length, 0, "incidents:");
});

// ------------------------------------------------------------------ the card

test("a deferred hold raises the cloud card with the engine's sentence as ENGINE, verbatim", () => {
  // N2 "ignore hold_deferred", observed (4 passed, 3 failed; the next two
  // tests failed with it, the dating one as "sinceMs: expected 1699999700000,
  // got undefined"):
  //   x a deferred hold raises the cloud card with the engine's sentence as
  //   NEXT, verbatim: incidents: expected 1, got 0
  //
  // ENGINE, NOT NEXT, SINCE THE H3 INTEGRATION (#244's verifier note). Every
  // #/next hub but Session shows this card as the cross-hub banner,
  // `${title}. ${firstSentence(engine)}`, and with the verdict as ENGINE it read
  // "CLOUDY. cloudy, from a reading 40s old - ...": the verdict twice, and not
  // why no hold opened. N2 re-run against the swapped lines, observed:
  //   (4 passed, 4 failed; the new verdict-once test failed as "precondition:
  //   no card")
  //   x a deferred hold raises the cloud card with the engine's sentence as
  //   ENGINE, verbatim: incidents: expected 1, got 0
  // B1 "the verdict back as ENGINE", observed:
  //   (6 passed, 2 failed)
  //   x a deferred hold raises the cloud card with the engine's sentence as
  //   ENGINE, verbatim: ENGINE: expected the frames say the sky has closed in,
  //   with no target set up to judge it from, so no cloud hold is open; the
  //   next target's setup opens one if the sky is still closed, got cloudy,
  //   from a reading 40s old - 1 bright stars, low contrast
  //   x the verdict word is said once: the title, and not again at the head of
  //   ENGINE: ENGINE leads with the verdict the title already says: "CLOUDY"
  //   over "cloudy, from a reading 40s old - 1 bright stars, low contrast"
  const list = deriveIncidents(inputs(cloudy(DEFERRED)), NOW);
  eq(list.length, 1, "incidents:");
  const c = list[0];
  eq(c.kind, "cloud", "kind:");
  eq(c.engine, DEFERRED, "ENGINE:");
  eq(c.next, SKY_TEXT, "NEXT (the verdict the sentence rests on):");
});

test("the verdict word is said once: the title, and not again at the head of ENGINE", () => {
  const c = deriveIncidents(inputs(cloudy(DEFERRED)), NOW)[0];
  ok(c != null, "precondition: no card");
  ok(!/cloud/i.test(c.engine.split(/[.;,]/)[0] ?? ""),
    `ENGINE leads with the verdict the title already says: "${c.title}" over "${c.engine}"`);
});

test("the deferral card claims no hold", () => {
  const c = deriveIncidents(inputs(cloudy(DEFERRED)), NOW)[0];
  ok(c != null, "precondition: no card");
  eq(c.pill, CLOUD_DEFERRED_PILL, "pill:");
  eq(c.pill, "WAITING", "pill word:");
  ok(!/HOLD/.test(c.title), `the title claims a hold: ${c.title}`);
  ok(!/Capture paused|Watching the star count/.test(`${c.engine} ${c.next}`),
    "the card describes a hold that is not open");
  eq(c.actions.map((a) => a.id).join(","), "wait",
    "actions (IGNORE WEATHER lifts the rain veto only, so it cannot act on this):");
});

test("the deferral card is dated by the rig's own log line, not this phone's clock", () => {
  const inp = inputs(cloudy(DEFERRED));
  inp.logs = [
    { source: "sequence", message: "target 1/2: M31", level: "info", tsMs: NOW - 900_000 },
    { source: "sequence", message: DEFERRED_LOG, level: "warning", tsMs: NOW - 300_000 },
  ];
  eq(deriveIncidents(inp, NOW)[0]?.sinceMs, NOW - 300_000, "sinceMs:");
  eq(deriveIncidents(inputs(cloudy(DEFERRED)), NOW)[0]?.sinceMs, null,
    "with the line rolled out of the ring, sinceMs:");
});

test("control: a hold that is really up is the hold card, whatever else is on the payload", () => {
  const c = deriveIncidents(inputs(cloudy(DEFERRED, true)), NOW)[0];
  ok(c != null, "no card for a real hold");
  eq(c.pill, "HOLDING", "pill:");
  eq(c.title, "CLOUD HOLD", "title:");
  ok(c.actions.some((a) => a.id === "ignore_weather"), "the hold card lost its actions");
});

console.log(`${passed} passed, ${failed} failed`);
for (const f of failures) console.error(f);
export const result = { passed, failed, total: passed + failed };
