// tonightVerdict.test.ts - the four answers Session / Now's list is allowed to
// give about tonight, and the two ways a fold like this normally invents one.
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/tonightVerdict.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT THIS FILE IS FOR. `GET /api/flows/{id}/tonight` is the one place in this
// app where a plausible-looking number is worse than no number: an operator
// plans an evening around "3h 40m usable from 21:48", and the whole reason the
// server refuses rather than guessing (`tonight.py`'s docstring) is that a
// default site produces exactly that sentence about somewhere else. So the two
// hardest assertions here are not about the arithmetic, they are about the
// REFUSALS:
//
//   * a payload that never arrived must not read as a night with no targets;
//   * a payload with `ok: true` but no dark window must not produce a duration,
//     because the only way to get one is to treat a missing boundary as zero.
//
// Convention: plain assertions, printed tally, `{passed, failed, total}` export
// (shell-and-tests.md section 4). No DOM - this module is pure.

import {
  TONIGHT_NO_NIGHT, TONIGHT_NO_REASON, TONIGHT_NO_WINDOW, TONIGHT_UNCHECKED,
  tonightVerdict,
} from "../now/tonightVerdict";

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}

// ----------------------------------------------------------------- fixtures
// An eight-hour dark window at a fixed instant. Every window below is written
// as an offset from it so the arithmetic is readable.
const DARK_START = 1_760_000_000;
const H = 3600;
const DARK_END = DARK_START + 8 * H;
// `now` sits exactly at dark start so every clock below lands on the SAME local
// calendar day and `fmtClock`'s day-rollover marker stays out of the expected
// strings; the marker gets its own test at the bottom, where the offset is a
// day and a half and so is timezone-independent.
const NOW = DARK_START * 1000;

function win(startOffH: number, endOffH: number): Record<string, unknown> {
  return {
    label: `t${startOffH}`,
    window: { start_unix: DARK_START + startOffH * H, end_unix: DARK_START + endOffH * H },
  };
}

function payload(targets: unknown[], night: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    reason: "",
    night: { dusk_unix: DARK_START - H, dawn_unix: DARK_END + H,
             dark_start_unix: DARK_START, dark_end_unix: DARK_END, ...night },
    targets,
  };
}

/** The wall-clock hh:mm of a unix instant, derived WITHOUT calling the
 *  formatter under test - otherwise the assertion would be a tautology. */
function hhmm(unix: number): string {
  const d = new Date(unix * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// ------------------------------------------------- 1. the four branches
test("no payload is DIM and says it was never asked, not that there is no window", () => {
  const v = tonightVerdict(null, NOW);
  eq(v.tone, "dim", "tone:");
  eq(v.line, TONIGHT_UNCHECKED, "line:");
  assert(!/usable/.test(v.line), `an unasked row claims a usable window: "${v.line}"`);
});

test("a refusal is WARN and carries the server's own reason, verbatim", () => {
  const reason = "No observatory site is set, so there is no night to resolve.";
  const v = tonightVerdict({ ok: false, reason }, NOW);
  eq(v.tone, "warn", "tone:");
  eq(v.line, reason, "the server's sentence was rewritten:");
});

test("an ABSENT ok is a refusal too - a payload shape we do not recognise is not a night", () => {
  const v = tonightVerdict({ reason: "" }, NOW);
  eq(v.tone, "warn", "tone:");
  eq(v.line, TONIGHT_NO_REASON, "line:");
});

test("ok:true with NO targets is BAD - the night resolved and nothing is up in it", () => {
  const v = tonightVerdict(payload([]), NOW);
  eq(v.tone, "bad", "tone:");
  eq(v.line, TONIGHT_NO_WINDOW, "line:");
});

test("ok:true with a real window is GOOD, with the duration and the opening clock", () => {
  // [dark, dark+2h] and [dark+5h, dark+9h] clipped to an 8 h dark window:
  // 2 h + 3 h = 5 h, opening at dark start.
  const v = tonightVerdict(payload([win(-1, 2), win(5, 9)]), NOW);
  eq(v.tone, "good", "tone:");
  assert(/usable from \d\d:\d\d/.test(v.line), `no clock in "${v.line}"`);
  eq(v.line, `5h 0m usable from ${hhmm(DARK_START)}`, "line:");
});

// --------------------------------------- 2. the ways a fold invents a number
test("a window that CLOSES BEFORE DARK contributes zero, and alone it is BAD", () => {
  const v = tonightVerdict(payload([win(-4, -1)]), NOW);
  eq(v.tone, "bad", "a window entirely before dark was counted:");
  eq(v.line, TONIGHT_NO_WINDOW, "line:");
});

test("...and beside a real one it adds nothing to the total", () => {
  const alone = tonightVerdict(payload([win(0, 2)]), NOW);
  const withDead = tonightVerdict(payload([win(-4, -1), win(0, 2)]), NOW);
  eq(withDead.line, alone.line, "the dead window changed the answer:");
  eq(alone.line, `2h 0m usable from ${hhmm(DARK_START)}`, "line:");
});

test("overlapping windows are UNIONED, never summed - two targets are not twice the sky", () => {
  const v = tonightVerdict(payload([win(0, 2), win(1, 3)]), NOW);
  eq(v.line, `3h 0m usable from ${hhmm(DARK_START)}`,
    "overlapping windows were added together:");
});

test("a MISSING dark_end_unix is WARN and NEVER a computed duration", () => {
  const v = tonightVerdict(payload([win(0, 2)], { dark_end_unix: null }), NOW);
  eq(v.tone, "warn", "tone:");
  eq(v.line, TONIGHT_NO_NIGHT, "line:");
  assert(!/usable/.test(v.line), `a missing boundary produced a duration: "${v.line}"`);
});

test("a missing dark_start_unix is the same refusal, not a night starting at the epoch", () => {
  const v = tonightVerdict(payload([win(0, 2)], { dark_start_unix: undefined }), NOW);
  eq(v.tone, "warn", "tone:");
  eq(v.line, TONIGHT_NO_NIGHT, "line:");
});

test("a target with no window at all is skipped, not read as a zero-length one", () => {
  const v = tonightVerdict(payload([{ label: "no window" }, win(0, 2)]), NOW);
  eq(v.line, `2h 0m usable from ${hhmm(DARK_START)}`, "line:");
});

test("a window opening on a later local day keeps fmtClock's rollover marker", () => {
  // 30 hours before dark start is at least one whole local day earlier in every
  // timezone, so the marker must be there and the bare clock must not.
  const v = tonightVerdict(payload([win(0, 2)]), (DARK_START - 30 * H) * 1000);
  assert(/usable from \d\d:\d\d \(\+\d+d\)/.test(v.line),
    `a window on another day lost its day marker: "${v.line}"`);
});

test("a malformed targets field is an empty list, not a crash", () => {
  const v = tonightVerdict({ ...payload([]), targets: "nope" }, NOW);
  eq(v.tone, "bad", "tone:");
});

// -------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`tonightVerdict.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
