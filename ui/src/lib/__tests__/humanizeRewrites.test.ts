// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// humanizeRewrites.test.ts - a keyword rewrite fires on the report it was
// written for, and a long line is shortened on a sentence boundary (#792).
//
//   Run directly:  npx tsx src/lib/__tests__/humanizeRewrites.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. humanizeLog rewrote on two bare words: any text holding "plate"
// and "solve" became "Plate-solve failed - check focus/exposure", any text
// holding "guid" and "lost" became "Guiding was lost - recovering", and any
// text holding "nina" and a "5" became "NINA reported an error". showToast
// routes every message through it, so honest copy that merely MENTIONED one of
// those things was replaced by a sentence that said the opposite, and the fall-
// through cut the rest at 137 characters wherever that fell, which in a refusal
// is the clause that names the cause or the repair.
//
// EACH CASE BELOW IS PAIRED. A line the rule was written for still maps (so a
// fix that simply deleted the rule fails), and a line that only shares its
// words survives whole (so the unfixed code fails).
//
// THE PLATE-SOLVE RULE IS PAIRED A SECOND TIME (#960). The report it was written
// for is a failure that names no cause. A failure that names one ("... failed:
// no light: the optic is capped", "... failed: no plate solver is available on
// this rig") is already the answer, and the generic "check focus/exposure" sent
// the operator to the wrong part of the rig.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { humanizeLog, CLIP_AT } from "../humanize";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

const SOLVE_FAILED = "Plate-solve failed - check focus/exposure, or solve manually.";
const GUIDING_LOST = "Guiding was lost - recovering.";
const NINA_ERROR = "NINA reported an error. Check NINA on the imaging PC.";
const ELLIPSIS = "…";

const line = (message: string, source = ""): string => humanizeLog({ source, message });

// ====================================================================
// plate + solve: only a plate solve that FAILED
// ====================================================================

test("a sentence that mentions a plate-solve sync survives whole", () => {
  // The shape WP-126's first draft of the position-unknown refusal had, and the
  // reason it was routed around showToast.
  const sentence =
    "Steps and nudges are measured from where the mount thinks it points, and it "
    + "does not know. A plate-solve sync, or TRUST POSITION, unlocks them.";
  assert(sentence.length <= CLIP_AT, `fixture is ${sentence.length} chars: the default would shorten it`);
  eq(line(sentence), sentence, "the refusal was rewritten:");
  eq(humanizeLog(sentence, { verbatim: true }), sentence, "verbatim does not bypass the rule:");
});

test("a plate solve that did not fail is not a failure", () => {
  for (const [source, message] of [
    ["solve", "plate solve: filter 'L' -> 'Lum' for the solve"],
    ["solve", "plate solve: another process held the frame file, retrying"],
    ["", "If a plate solve fails, press TRUST POSITION instead."],
    ["", "Plate solving needs a star field: aim at the sky first."],
  ] as const) {
    eq(line(message, source), message, `"${message}" was rewritten:`);
  }
});

test("a bare plate-solve failure, with no cause in the line, still maps", () => {
  for (const message of [
    "plate solve failed",
    "Plate-solve failed",
    "plate solving failed",
    "Plate solve error",
    "the plate solve timed out",
    "plate solve failed.",
    "  Plate solve failed  ",
    // A source or a stage in front of the failure is a label, not a cause.
    "solve failed: plate solve failed",
    "centering: plate solve failed.",
    "rotator sync: solve failed: the plate solve timed out",
  ]) {
    eq(line(message, "solve"), SOLVE_FAILED, `"${message}" was not mapped:`);
  }
});

// THE DEFECT OF #960. The advice above is "check focus/exposure, or solve
// manually", right for a failure that says nothing and wrong for one that says
// what is wrong: a lens cap on, no solver on the rig. The fixtures marked
// "server" are read out of the server's own source so they cannot drift from the
// words the rig really logs; the rest are the shapes its callers wrap around a
// cause.

/** The string a Python module constant is set to: one literal, or the adjacent
 *  literals of a parenthesised group, joined. Reads the server's source rather
 *  than a copy of it, as laneConflict.test.ts does for the lane table. */
function serverConst(file: string, name: string): string {
  const path = fileURLToPath(new URL(`../../../../server/astrodeck/${file}`, import.meta.url));
  const src = readFileSync(path, "utf8");
  const m = new RegExp(`^${name} = \\(?\\s*((?:"[^"\\n]*"\\s*)+)\\)?`, "m").exec(src);
  if (!m) throw new Error(`cannot find ${name} in ${file}: the scan is broken, so the cases below prove nothing`);
  return [...m[1].matchAll(/"([^"\n]*)"/g)].map((x) => x[1]).join("");
}

const SOLVER_MISSING = serverConst("hub.py", "SOLVE_REASON_SOLVER_MISSING");
const FILE_LOCKED = serverConst("hub.py", "SOLVE_REASON_FILE_LOCKED");
const NO_LIGHT_WORDS = serverConst("solve/light.py", "NO_LIGHT_WORDS");

test("the server's own failure words are read, not guessed - the vacuity guard", () => {
  assert(SOLVER_MISSING.startsWith("plate solve failed: "), `server constant is "${SOLVER_MISSING}"`);
  assert(FILE_LOCKED.startsWith("plate solve failed: "), `server constant is "${FILE_LOCKED}"`);
  assert(NO_LIGHT_WORDS.startsWith("no light: "), `server constant is "${NO_LIGHT_WORDS}"`);
});

test("a plate-solve failure that names its cause is shown as written (#960)", () => {
  for (const message of [
    // server: the rig-side reasons, as _spawn writes them behind "solve failed: "
    `solve failed: ${SOLVER_MISSING}`,
    SOLVER_MISSING,
    `solve failed: ${FILE_LOCKED}`,
    // server: NoLightError (solve/light.py error_for) - a capped optic
    `solve failed: plate solve failed: ${NO_LIGHT_WORDS} (the frame reads at the level this `
      + "camera reads with no light on it; the solver said: Not enough stars.)",
    // the sky's own cause is a cause too; the line already says it
    "solve failed: plate solve failed: Not enough stars.",
    // a cause and what the system did about it
    "centering: plate solve failed (no stars); using raw GoTo",
    "plate solve failed - used raw GoTo",
    "Plate solve error: timed out",
    // a cause in front of the failure is not a label
    "no stars were found, so the plate solve failed",
  ]) {
    assert(message.length <= CLIP_AT, `fixture is ${message.length} chars: the default would shorten it`);
    const out = line(message, "solve");
    eq(out, message, `"${message}" lost its cause:`);
    assert(!/check focus/i.test(out), `"${message}" was sent to focus and exposure: ${out}`);
    eq(humanizeLog(message, { verbatim: true }), message, `verbatim changed "${message}":`);
  }
});

// ====================================================================
// guid + lost: only the report that guiding was lost
// ====================================================================

test("a stop that mentions guiding was lost survives whole (#850)", () => {
  for (const message of [
    "re-centring after guiding was lost: the mount refused the sync, so the target was stopped",
    "Stopping because guiding was lost and cannot be restarted until the star is back.",
    "guiding was lost and did not recover",
    "native guider: guiding stopped (lock lost)",
  ]) {
    eq(line(message, "sequence"), message, `"${message}" was rewritten to a recovery claim:`);
  }
});

test("a line that reports guiding lost still maps", () => {
  for (const message of [
    "guiding was lost",
    "Guiding lost.",
    "native guider lost the guide star (reacquire 1/3)",
    "PHD2: guiding lost",
  ]) {
    eq(line(message, "guide"), GUIDING_LOST, `"${message}" was not mapped:`);
  }
});

// ====================================================================
// nina + 5: an HTTP 5xx token, not any figure holding a 5
// ====================================================================

test("a NINA line with a figure holding a 5 survives whole", () => {
  for (const message of [
    // The oct08 slow-request line, as its formatter writes it before the
    // route is made pair-free: "15.0" holds a 5.
    "slow request GET /api/nina/health: still waiting after 15.0 s",
    "slow request GET /api/nina/health: still waiting after 15.0 s (5 in flight)",
    "NINA has not answered for 500 ms",
    "NINA image 512 bytes short of the expected size",
    "NINA at 85% of its buffer",
    // The closing line of a request that SUCCEEDED (200) after 500-599.9 s:
    // slow_requests.py writes the elapsed time as `{elapsed_s:.1f} s`, so the
    // figure is a 5xx-shaped token followed by a decimal part.
    "slow request GET /api/nina/health: 200 after 523.4 s",
    "NINA has 500,000 bytes pending",
  ]) {
    eq(line(message, "api"), message, `"${message}" was rewritten:`);
  }
});

test("a NINA line that reports an HTTP failure still maps", () => {
  for (const message of [
    "NINA HTTP 502 on /equipment/camera/info: reply of 123 bytes not quoted",
    "NINA returned 503 service unavailable",
    "NINA HTTP 404 on /equipment/mount/info: reply of 18 bytes not quoted",
    "NINA error on /sequence/start",
  ]) {
    eq(line(message, "nina"), NINA_ERROR, `"${message}" was not mapped:`);
  }
});

// ====================================================================
// shortening: whole sentences, never half of one
// ====================================================================
//
// Every fixture is sized from CLIP_AT, so retuning the budget cannot turn these
// into passes that prove nothing; the vacuity guard fails loudly instead.

/** One sentence of at least `len` characters (and under len + 12), ending in a
 *  full stop and holding no full stop before it. */
function sentence(lead: string, len: number): string {
  let s = lead;
  while (s.length < len - 1) s += " and so on";
  return `${s}.`;
}

/** A cause that fits the budget, and a repair that does not fit beside it. */
const CAUSE = sentence("The mount did not confirm it was parked and may still be tracking toward the roof edge", CLIP_AT - 60);
const REPAIR = sentence("Close the roof by hand, park the mount from the pad, then check the cables", 120);

test("the fixtures are sized against the budget - the vacuity guard", () => {
  assert(CAUSE.length <= CLIP_AT, `the cause is ${CAUSE.length} chars: it no longer fits whole`);
  assert(`${CAUSE} ${REPAIR}`.length > CLIP_AT,
    `cause and repair are ${(`${CAUSE} ${REPAIR}`).length} chars: nothing below is shortened, so nothing proves anything`);
});

test("a cause and a repair that fit the budget between them both reach the toast", () => {
  // The shape of a server refusal. The old cut at 140 landed inside the repair.
  const cause = sentence("The mount did not confirm it was parked", 110);
  const repair = sentence("Close the roof by hand and park the mount from the pad", 70);
  assert(`${cause} ${repair}`.length > 140 && `${cause} ${repair}`.length <= CLIP_AT,
    "the fixture is the wrong size to be the case under test");
  eq(line(`${cause} ${repair}`), `${cause} ${repair}`, "the repair was cut or dropped:");
});

test("a line over the budget keeps its leading sentences whole, and says more follows", () => {
  eq(line(`${CAUSE} ${REPAIR}`), `${CAUSE} ${ELLIPSIS}`, "the cut did not land on the sentence boundary:");
});

test("every leading sentence that fits is kept, not only the first", () => {
  // The harm #792 describes is a repair sentence cut away from its cause. Two
  // short sentences fit the budget between them and a third takes the line over
  // it, so the cut must land after the SECOND. A cut after the first keeps the
  // cause and drops the repair that sits next to it, and the case above, whose
  // cause is one sentence, cannot tell the two apart.
  const first = sentence("The mount did not confirm it was parked", Math.round(CLIP_AT * 0.3));
  const second = sentence("Close the roof by hand and park the mount from the pad", Math.round(CLIP_AT * 0.3));
  const third = sentence("Then check that the cables are clear of the pier before the next run", Math.round(CLIP_AT * 0.5));
  const two = `${first} ${second}`;
  const all = `${two} ${third}`;
  assert(first.length < two.length && two.length <= CLIP_AT,
    `the first two sentences are ${two.length} chars (first alone ${first.length}): they do not fit the ${CLIP_AT} budget together`);
  assert(all.length > CLIP_AT,
    `the three sentences are ${all.length} chars: nothing is shortened, so nothing proves anything`);
  eq(line(all), `${two} ${ELLIPSIS}`, "the cut did not keep both sentences that fit:");
});

test("a single long sentence is never cut in the middle", () => {
  // The repair comes last and nothing before it ends a sentence: a character
  // count would have landed inside it.
  const one = sentence("DAWN PARK FAILED: the mount did not confirm it was parked, so close the roof by hand", CLIP_AT + 100);
  assert(one.length > CLIP_AT && one.length < 2 * CLIP_AT, `fixture is ${one.length} chars`);
  eq(line(one), one, "the sentence was cut:");
});

test("when the first sentence alone is over the budget it is kept whole", () => {
  const first = sentence("The roof did not close because the rain sensor and the cloud sensor disagreed", CLIP_AT + 60);
  eq(line(`${first} Check the roof.`), `${first} ${ELLIPSIS}`, "the first sentence was cut or the second kept:");
});

test("a stack trace keeps its first line, not a character count of the whole", () => {
  const trace = [
    "Traceback (most recent call last):",
    `  File "astrodeck/hub.py", line 6481, in loop ${"x".repeat(CLIP_AT)}`,
    "DeviceError: loop capture failed",
  ].join("\n");
  eq(line(trace), `Traceback (most recent call last): ${ELLIPSIS}`, "the trace was cut mid-line:");
});

test("a dump with no sentence in it is cut at a word, and bounded", () => {
  const dump = `payload ${"abcdefghij ".repeat(Math.ceil((2 * CLIP_AT) / 11) + 20)}`;
  const out = line(dump);
  assert(out.length < dump.length, "the dump was passed through whole");
  assert(out.length <= 2 * CLIP_AT + 1, `the dump was kept at ${out.length} characters`);
  assert(out.endsWith(`abcdefghij${ELLIPSIS}`), `the dump was cut inside a word: "${out.slice(-20)}"`);
});

test("short text, verbatim text and empty text are unchanged", () => {
  eq(line("camera cooler at 3 C"), "camera cooler at 3 C");
  eq(humanizeLog(`${CAUSE} ${REPAIR}`, { verbatim: true }), `${CAUSE} ${REPAIR}`, "verbatim shortened the refusal:");
  eq(line(""), "Something went wrong.");
});

const total = passed + failed;
console.log(`humanizeRewrites.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
