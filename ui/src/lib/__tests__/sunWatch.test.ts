// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// sunWatch.test.ts - what the sun watch's published state says to a person
// (#894), and what the Monitor health strip folds it into. Pure: no jsdom.
//
//   Run directly:  npx tsx src/lib/__tests__/sunWatch.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The server knew the net was blind or standing down and told only
// `/api/safety/state`, which no screen read. Each state below is a case a person
// at a rig with no alert sink would otherwise never have seen.
//
// NAMED MUTANTS (lib/sunWatch.ts and lib/health.ts), each applied to a byte
// backup and restored byte-identical, the observed failure quoted at the case
// it turns red:
//   U1 "position unknown is silent"  the `sw.position_unknown` branch returns null
//   U2 "blind is silent"             the `sw.blind` branch returns null
//   U3 "off without a mount"         `&& mountConnected` removed from the off branch
//   U4 "a missing key is off"        `if (!sw) return null;` removed
//   U5 "the ladder drops the sun"    deriveHealthIssues pushes no sun issue
//   U6 "goto advice in the blind step"  ', then slew it to a target' added to the step

import type { SunWatchState } from "../../types";
import { deriveHealthIssues } from "../health";
import {
  fmtElapsed, fmtSinceClock, SUN_WATCH_BLIND_STEP, SUN_WATCH_POSITION_STEP,
  sunWatchNotice,
} from "../sunWatch";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: unknown, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq<T>(got: T, want: T, msg: string): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg} (expected ${JSON.stringify(want)}, got ${JSON.stringify(got)})`);
  }
}

// A local 03:14 on a made-up night. Built from local fields, so the clock the
// notice prints is "03:14" in any time zone the test runs in.
const SINCE = new Date(2026, 9, 10, 3, 14, 0).getTime() / 1000;
const NOW = SINCE + 12 * 60;

const HEALTHY: SunWatchState = {
  blind: false, blind_since: null, position_unknown: false,
  position_unknown_since: null, last_position_at: SINCE - 5, armed: true,
};
const UNKNOWN: SunWatchState = {
  ...HEALTHY, position_unknown: true, position_unknown_since: SINCE,
};
const BLIND: SunWatchState = { ...HEALTHY, blind: true, blind_since: SINCE };
const OFF: SunWatchState = { ...HEALTHY, armed: false };

// ------------------------------------------------------------ position unknown

test("position unknown: the net is standing down, since when, and the safe order", () => {
  // U1 "position unknown is silent", observed (10/15 passed):
  //   x position unknown: the net is standing down, since when, and the safe
  //   order: expected a notice, got null
  const n = sunWatchNotice(UNKNOWN, true, NOW);
  assert(n != null, "expected a notice, got null");
  eq(n!.kind, "position_unknown", "kind");
  eq(n!.tier, 2, "the tube has no protection now, so it is an act-now rung");
  eq(n!.since, SINCE, "the since-time is the server's, passed through");
  assert(n!.text.includes("Sun watch is standing down"), `what the net is doing: ${n!.text}`);
  assert(n!.text.includes("since 03:14 (12 min)"), `since when, on the wall clock: ${n!.text}`);
  assert(n!.text.includes("will not park the tube"), `what it will not do: ${n!.text}`);
  eq(n!.action, SUN_WATCH_POSITION_STEP, "the step");
});

test("position unknown: the step is the safe order and never a move", () => {
  const step = SUN_WATCH_POSITION_STEP;
  assert(step.includes("Cover the tube if the Sun is up."), `cover first: ${step}`);
  assert((step.match(/TRUST POSITION/g) ?? []).length === 2,
    `TRUST POSITION for a tube at home, and again after the pad key: ${step}`);
  assert(step.includes("hold a pad key to bring it home by eye first"),
    `the by-eye step: ${step}`);
  assert(step.indexOf("pad key") < step.lastIndexOf("TRUST POSITION"),
    `Trust comes AFTER bringing it home by eye: ${step}`);
});

// ------------------------------------------------------------------ blind

test("blind: the net cannot read the mount, since when, and what to check", () => {
  // U2 "blind is silent", observed (11/15 passed):
  //   x blind: the net cannot read the mount, since when, and what to check:
  //   expected a notice, got null
  const n = sunWatchNotice(BLIND, true, NOW);
  assert(n != null, "expected a notice, got null");
  eq(n!.kind, "blind", "kind");
  eq(n!.tier, 2, "blind is an act-now rung");
  eq(n!.since, SINCE, "the since-time");
  assert(n!.text.includes("Sun watch is blind since 03:14 (12 min)"), `blind and since: ${n!.text}`);
  assert(n!.text.includes("cannot read the mount"), `why: ${n!.text}`);
  assert(n!.text.includes("cannot see the Sun closing on the tube"), `the consequence: ${n!.text}`);
  eq(n!.action, SUN_WATCH_BLIND_STEP, "the step");
  assert(n!.action!.includes("Check the mount's power and cable."), `check: ${n!.action}`);
  assert(n!.action!.includes("Cover the tube if the Sun is up."), `cover: ${n!.action}`);
});

// ------------------------------------------------------------------ off

test("off: the net is not running while a mount is connected", () => {
  const n = sunWatchNotice(OFF, true, NOW);
  assert(n != null, "expected a notice, got null");
  eq(n!.kind, "off", "kind");
  eq(n!.tier, 1, "off is a standing choice: a notice, not an act-now rung");
  eq(n!.since, null, "off has no since-time");
  assert(n!.text.includes("Sun watch is not running"), n!.text);
  assert(n!.text.includes("nothing will park the tube if the Sun closes on it"), n!.text);
});

test("off needs a mount: a net that is not running protects nothing that is unplugged", () => {
  // U3 "off without a mount", observed (13/15 passed):
  //   x off needs a mount: a net that is not running protects nothing that is
  //   unplugged: expected null (expected null, got {"kind":"off","tier":1,...})
  eq(sunWatchNotice(OFF, false, NOW), null, "expected null");
});

// ------------------------------------------------------------------ nothing

test("a healthy net says nothing", () => {
  eq(sunWatchNotice(HEALTHY, true, NOW), null, "healthy, mount connected");
  eq(sunWatchNotice(HEALTHY, false, NOW), null, "healthy, no mount");
});

test("an absent state says nothing: a missing key is not 'off'", () => {
  // U4 "a missing key is off", observed (13/15 passed; the TypeError is the
  // proof it read through the absent state instead of declining to speak):
  //   x an absent state says nothing: a missing key is not 'off': Cannot read
  //   properties of undefined (reading 'position_unknown')
  eq(sunWatchNotice(undefined, true, NOW), null, "undefined");
  eq(sunWatchNotice(null, true, NOW), null, "null");
});

test("both flags set (the server never sends it): the cause, the position latch, is named", () => {
  const n = sunWatchNotice({ ...UNKNOWN, blind: true, blind_since: SINCE }, true, NOW);
  eq(n!.kind, "position_unknown", "the latch is the cause and blindness the symptom");
});

// ------------------------------------------------------------------ copy rules

test("no copy advises a move, uses a dash the new UI forbids, or carries a site figure", () => {
  // U6 "goto advice in the blind step", observed (13/15 passed; the blind case
  // fails with it):
  //   x no copy advises a move, uses a dash the new UI forbids, or carries a
  //   site figure: advice to move: Check the mount's power and cable, then
  //   slew it to a target. Cover the tube if the Sun is up.
  // The en and em dash, by code point so this file stays ASCII.
  const DASHES = String.fromCharCode(0x2013, 0x2014);
  const all = [
    sunWatchNotice(UNKNOWN, true, NOW)!, sunWatchNotice(BLIND, true, NOW)!,
    sunWatchNotice(OFF, true, NOW)!,
  ].flatMap((n) => [n.text, n.action ?? ""]);
  for (const line of all) {
    assert(![...line].some((c) => DASHES.includes(c)), `a dash the new UI forbids: ${line}`);
    assert(!/\b(slew|goto|go to|go-to)\b/i.test(line), `advice to move: ${line}`);
  }
  // The only digits are the clock and the age; the steps carry none.
  for (const step of [SUN_WATCH_POSITION_STEP, SUN_WATCH_BLIND_STEP]) {
    assert(!/\d/.test(step), `a number in a step: ${step}`);
  }
});

test("the step does not change with the clock; the headline's age does", () => {
  const early = sunWatchNotice(UNKNOWN, true, SINCE + 60)!;
  const late = sunWatchNotice(UNKNOWN, true, SINCE + 3 * 3600)!;
  eq(early.action, late.action, "the step is a fixed sentence");
  assert(early.text.includes("(1 min)") && late.text.includes("(3 h)"),
    `the age moves: ${early.text} / ${late.text}`);
});

// ------------------------------------------------------------------ formatting

test("fmtElapsed", () => {
  eq(fmtElapsed(0), "under 1 min", "zero");
  eq(fmtElapsed(59), "under 1 min", "59 s");
  eq(fmtElapsed(60), "1 min", "one minute");
  eq(fmtElapsed(7140), "119 min", "119 min");
  eq(fmtElapsed(7200), "2 h", "two hours");
  eq(fmtElapsed(7500), "2 h 5 min", "two hours five");
  eq(fmtElapsed(-300), "under 1 min", "a browser clock behind the server's never prints a negative age");
});

test("fmtSinceClock is the local wall clock, zero padded", () => {
  eq(fmtSinceClock(new Date(2026, 0, 2, 5, 7, 0).getTime() / 1000), "05:07", "padded");
  eq(fmtSinceClock(Number.NaN), "--:--", "unreadable");
});

// ------------------------------------------------------------------ the ladder

function ladder(sunWatch: SunWatchState | undefined, mountConnected = true) {
  return deriveHealthIssues({
    safety: null, wsConnected: true, sunWatch, mountConnected, nowS: NOW,
  });
}

test("the Monitor ladder folds each state in, so 'Night looks OK' cannot print over it", () => {
  // U5 "the ladder drops the sun", observed (13/15 passed):
  //   x the Monitor ladder folds each state in, so 'Night looks OK' cannot
  //   print over it: position unknown: one issue (expected 1, got 0)
  const u = ladder(UNKNOWN);
  eq(u.length, 1, "position unknown: one issue");
  eq(u[0].tier, 2, "position unknown: act");
  assert(u[0].text.includes("Sun watch is standing down"), u[0].text);
  eq(u[0].detail, SUN_WATCH_POSITION_STEP, "the step rides as the detail line");

  const b = ladder(BLIND);
  eq(b.length, 1, "blind: one issue");
  eq(b[0].tier, 2, "blind: act");
  eq(b[0].detail, SUN_WATCH_BLIND_STEP, "blind step");

  const o = ladder(OFF);
  eq(o.length, 1, "off: one issue");
  eq(o[0].tier, 1, "off: notice");
  eq(o[0].detail, undefined, "off has no step");
});

test("the ladder is silent for a healthy net, an absent one, and an off net with no mount", () => {
  eq(ladder(HEALTHY).length, 0, "healthy");
  eq(ladder(undefined).length, 0, "absent");
  eq(ladder(OFF, false).length, 0, "off, no mount");
});

test("the ladder keeps every act rung ahead of every notice rung", () => {
  const issues = deriveHealthIssues({
    safety: null, wsConnected: true, sunWatch: BLIND, mountConnected: true, nowS: NOW,
    disk: { free_gb: 8, low: true, critical: false }, telemetryStale: true,
  });
  const tiers = issues.map((i) => i.tier);
  eq(tiers, [2, 1, 1], "act first");
});

const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nsunWatch.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
