// Unit tests for the shared "why nothing is happening" formatter (NOV-8).
// No vitest/jsdom in this UI — run directly:
//   npx tsx src/lib/__tests__/scheduleStatus.test.ts
// Also compiled by `tsc -b`.
//
// A MERIDIAN WAIT READS AS ONE (#488; spec 5.7, 5.10, 6.9). The formatter knew
// two waits, an altitude gate and a window that has not opened, and worded
// every other reason as the window: a mosaic held for the meridian so that it
// changes pier side once read "Waiting for the observing window to open." with
// the window wide open. Now the meridian wait has its own sentence, with no
// countdown and no clock (the crossing is the site's, 6.9); any other reason
// the formatter does not know prints the server's own words, with no time
// either; and the window sentence is said only while the window has not opened.
//
// NAMED MUTANTS (#488), each run in a private scratch copy of ui/ (scratchpad
// H4-UMON-mut, never the shared tree), from a byte backup restored and
// hash-compared after each run; the failure each produced is quoted at the
// test it turned red.
//   FW "fall through to the window sentence"  waitingText: no meridian branch,
//                                    and every reason that is not an altitude
//                                    gate gets the window lead (the pre-#488 code)
//   M  "meridian branch dropped"     waitingText: the meridian test removed, so
//                                    the meridian reason reaches the server-words
//                                    line
//   MT "the meridian line takes the countdown tail" waitingText: the meridian
//                                    sentence goes through the clock/countdown tail
//   UT "an unknown reason keeps the countdown tail" waitingText: the server-words
//                                    line goes through the clock/countdown tail
//   W  "window sentence dropped"     waitingText: a start_ts still ahead no longer
//                                    picks the window lead (guards the control)
//   AL "altitude read off the clock alone" waitingText: isAltitude drops its
//                                    /altitude/ test (guards the other control)
import {
  formatScheduleStatus,
  fmtWaitApprox,
  parseGateDeg,
} from "../scheduleStatus";
import type { SequenceState } from "../../types";

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function match(s: string, re: RegExp, msg = ""): void {
  if (!re.test(s)) throw new Error(`${msg} — ${JSON.stringify(s)} did not match ${re}`);
}
const NOW = 1_000_000; // arbitrary unix-seconds anchor

// ---- fmtWaitApprox ----
test("fmtWaitApprox: sub-minute / minutes / hours", () => {
  eq(fmtWaitApprox(45), "under a minute");
  eq(fmtWaitApprox(2820), "47 min");     // 47*60
  eq(fmtWaitApprox(3600), "1 hr");
  eq(fmtWaitApprox(4320), "1 hr 12 min"); // 72 min
  eq(fmtWaitApprox(-5), "under a minute");
});

// ---- parseGateDeg ----
test("parseGateDeg: pulls degrees from terse server reasons", () => {
  eq(parseGateDeg("below start altitude (30 deg)"), 30);
  eq(parseGateDeg("never rises above 30 deg tonight"), 30);
  eq(parseGateDeg("waiting for start time"), null);
});

// ---- waiting: altitude (start_ts already past) ----
test("waiting/altitude → plain sentence with gate + live countdown", () => {
  const sched: SequenceState["schedule"] = {
    state: "waiting", reason: "below start altitude (30 deg)",
    eta_s: 2820, start_ts: NOW - 100,
  };
  const r = formatScheduleStatus(sched, undefined, NOW)!;
  eq(r.tone, "calm", "tone");
  match(r.text, /^Waiting for your target to rise above 30° at \d{2}:\d{2}, about 47 min\.$/, "altitude copy");
});

// ---- waiting: clock (window not open yet) ----
test("waiting/clock → 'observing window to open' with start_ts clock", () => {
  const sched: SequenceState["schedule"] = {
    state: "waiting", reason: "waiting for start time",
    eta_s: 2820, start_ts: NOW + 2820,
  };
  const r = formatScheduleStatus(sched, undefined, NOW)!;
  eq(r.tone, "calm");
  match(r.text, /^Waiting for the observing window to open at \d{2}:\d{2}, about 47 min\.$/, "clock copy");
});

// ---- window_closed / never_rises = warn ----
test("window_closed → warn", () => {
  const r = formatScheduleStatus(
    { state: "window_closed", reason: "observing window has closed", eta_s: 0 },
    undefined, NOW)!;
  eq(r.tone, "warn");
  assert(r.text.includes("window has closed"), "closed copy");
});
test("never_rises → warn, gate woven in", () => {
  const r = formatScheduleStatus(
    { state: "never_rises", reason: "never rises above 30 deg tonight", eta_s: 0 },
    undefined, NOW)!;
  eq(r.tone, "warn");
  eq(r.text, "This target never rises above 30° from your site tonight.");
});

// ---- ready / absent / idle → null ----
test("ready → null (nothing to say)", () => {
  eq(formatScheduleStatus({ state: "ready", reason: "", eta_s: 0 }, undefined, NOW), null);
});
test("no schedule + no live → null", () => {
  eq(formatScheduleStatus(undefined, undefined, NOW), null);
  eq(formatScheduleStatus(null, null, NOW), null);
});

// ---- meridian heads-up (schedule ready/absent) ----
test("meridian eta → calm 'imaging will pause briefly'", () => {
  const r = formatScheduleStatus(undefined, { meridian_eta_s: 1080 }, NOW)!;
  eq(r.tone, "calm");
  eq(r.text, "Meridian flip in 18 min — imaging will pause briefly.");
});
test("meridian eta <= 0 → 'starting' (defensive)", () => {
  const r = formatScheduleStatus({ state: "ready", reason: "", eta_s: 0 }, { meridian_eta_s: 0 }, NOW)!;
  eq(r.text, "Meridian flip starting — imaging will pause briefly.");
});

// ---- precedence: schedule waiting beats meridian ----
test("waiting schedule wins over a concurrent meridian eta", () => {
  const r = formatScheduleStatus(
    { state: "waiting", reason: "below start altitude (30 deg)", eta_s: 2820, start_ts: NOW - 1 },
    { meridian_eta_s: 600 }, NOW)!;
  match(r.text, /^Waiting for your target to rise above 30°/, "schedule takes precedence");
});

// ---- #488: the meridian wait, and a reason the formatter does not know ----
// The engine's own publish for a mosaic held on the meridian rule
// (engine.py `_publish_group_wait`; recorded in
// server/tests/fixtures/sequence_state_mosaic.json, meridian_wait_operator):
// eta_s 0, no start_ts, and the words below. The fixture also carries a live
// meridian countdown beside it, which the wait must not borrow.
const MERIDIAN_REASON = "the mosaic waits for the meridian, so it changes pier side once";
const MERIDIAN_TEXT = "Waiting for the meridian, so the mosaic changes pier side once.";
/** A time of any kind in a line: a clock, or one of fmtWaitApprox's durations. */
const TIME_IN_LINE = /\d{1,2}:\d{2}|\babout\b|under a minute|\bmin\b|\bhr\b/;

test("#488: a meridian wait reads as a meridian wait, not the window", () => {
  // FW "fall through to the window sentence", observed (13/18 passed; every
  // #488 case but the two controls failed with it):
  //   ✗ #488: a meridian wait reads as a meridian wait, not the window: the
  //   meridian wait expected Waiting for the meridian, so the mosaic changes
  //   pier side once., got Waiting for the observing window to open.
  // M "meridian branch dropped", observed (16/18 passed; the next case failed
  // with it, on the same words):
  //   ✗ #488: a meridian wait reads as a meridian wait, not the window: the
  //   meridian wait expected Waiting for the meridian, so the mosaic changes
  //   pier side once., got Waiting: the mosaic waits for the meridian, so it
  //   changes pier side once.
  const r = formatScheduleStatus(
    { state: "waiting", reason: MERIDIAN_REASON, eta_s: 0 },
    { meridian_eta_s: 813 }, NOW)!;
  eq(r.tone, "calm", "tone");
  eq(r.text, MERIDIAN_TEXT, "the meridian wait");
});

test("#488: a meridian wait carries no countdown and no clock, even handed one", () => {
  // The crossing is set by the site (6.9), so the line never carries a time,
  // whatever numbers ride beside the reason.
  // MT "the meridian line takes the countdown tail", observed (17/18 passed):
  //   ✗ #488: a meridian wait carries no countdown and no clock, even handed
  //   one: a time in the meridian wait: "Waiting for the meridian, so the
  //   mosaic changes pier side once at 06:33, about 47 min."
  // FW "fall through to the window sentence", observed here:
  //   ✗ #488: a meridian wait carries no countdown and no clock, even handed
  //   one: a time in the meridian wait: "Waiting for the observing window to
  //   open at 06:33, about 47 min."
  // (06:33 is NOW + 2820 s on the local clock of the run.)
  for (const sched of [
    { state: "waiting" as const, reason: MERIDIAN_REASON, eta_s: 2820 },
    { state: "waiting" as const, reason: MERIDIAN_REASON, eta_s: 0, start_ts: NOW + 2820 },
  ]) {
    const r = formatScheduleStatus(sched, { meridian_eta_s: 813 }, NOW)!;
    assert(!TIME_IN_LINE.test(r.text), `a time in the meridian wait: ${JSON.stringify(r.text)}`);
    eq(r.text, MERIDIAN_TEXT, "the meridian wait handed a time");
  }
});

test("#488: a reason the formatter does not know prints the server's own words", () => {
  // The engine's zenith keep-out hold (engine.py `_limit_words`, published by
  // `_publish_group_wait` with eta_s 0): not an altitude gate, not a window.
  // FW "fall through to the window sentence", observed (13/18 passed):
  //   ✗ #488: a reason the formatter does not know prints the server's own
  //   words: the unknown reason expected Waiting: M31 1-1 would be in the
  //   mount's zenith keep-out by the end of a slew there., got Waiting for
  //   the observing window to open.
  const reason = "M31 1-1 would be in the mount's zenith keep-out by the end of a slew there";
  const r = formatScheduleStatus({ state: "waiting", reason, eta_s: 0 }, undefined, NOW)!;
  eq(r.tone, "calm", "tone");
  eq(r.text, `Waiting: ${reason}.`, "the unknown reason");
});

test("#488: an unknown reason's words carry no time: the formatter cannot know it is site-free", () => {
  // The hour-angle constraint (schedule.py constraint_gate) is the one such
  // reason that arrives with an eta_s, and its opening is set by the site's
  // longitude. It used to read "Waiting for the observing window to open at
  // HH:MM"; the window was not its cause, and the clock was a site fact.
  // UT "an unknown reason keeps the countdown tail", observed (17/18 passed):
  //   ✗ #488: an unknown reason's words carry no time: the formatter cannot
  //   know it is site-free: a time beside the server's words: "Waiting:
  //   before hour-angle window (-2h) at 06:33, about 47 min."
  // FW "fall through to the window sentence", observed here (13/18 passed):
  //   ✗ #488: an unknown reason's words carry no time: the formatter cannot
  //   know it is site-free: a time beside the server's words: "Waiting for
  //   the observing window to open at 06:33, about 47 min."
  const reason = "before hour-angle window (-2h)";
  const r = formatScheduleStatus({ state: "waiting", reason, eta_s: 2820 }, undefined, NOW)!;
  assert(!TIME_IN_LINE.test(r.text), `a time beside the server's words: ${JSON.stringify(r.text)}`);
  eq(r.text, `Waiting: ${reason}.`, "the hour-angle wait");
});

test("#488: a wait with no reason and no window ahead says only that it waits", () => {
  // No server publish does this today (every waiting publish carries words),
  // and the window sentence would claim a cause nobody gave.
  // FW "fall through to the window sentence", observed (13/18 passed):
  //   ✗ #488: a wait with no reason and no window ahead says only that it
  //   waits: the reasonless wait expected Waiting., got Waiting for the
  //   observing window to open.
  const r = formatScheduleStatus({ state: "waiting", reason: "", eta_s: 0 }, undefined, NOW)!;
  eq(r.text, "Waiting.", "the reasonless wait");
});

test("#488 control: a window that has not opened keeps the window sentence and its clock", () => {
  // The existing clock case above, with an empty reason: the sentence is set
  // by the window being ahead, not by the words.
  // W "window sentence dropped", observed (16/18 passed; the clock case above
  // failed with it, as "clock copy — "Waiting: waiting for start time." did
  // not match ..."):
  //   ✗ #488 control: a window that has not opened keeps the window sentence
  //   and its clock: window copy — "Waiting." did not match
  //   /^Waiting for the observing window to open at \d{2}:\d{2}, about 47 min\.$/
  const r = formatScheduleStatus(
    { state: "waiting", reason: "", eta_s: 2820, start_ts: NOW + 2820 }, undefined, NOW)!;
  match(r.text, /^Waiting for the observing window to open at \d{2}:\d{2}, about 47 min\.$/, "window copy");
});

test("#488 control: an altitude gate keeps its sentence and countdown", () => {
  // The altitude case with no start_ts at all, as the engine publishes one for
  // a target whose window has no start (resolve_window mode "none"): read off
  // the words, so the server-words line must not take it.
  // AL "altitude read off the clock alone", observed (17/18 passed):
  //   ✗ #488 control: an altitude gate keeps its sentence and countdown:
  //   altitude copy — "Waiting: below start altitude (30 deg)." did not match
  //   /^Waiting for your target to rise above 30° at \d{2}:\d{2}, about 47 min\.$/
  const r = formatScheduleStatus(
    { state: "waiting", reason: "below start altitude (30 deg)", eta_s: 2820 },
    undefined, NOW)!;
  match(r.text, /^Waiting for your target to rise above 30° at \d{2}:\d{2}, about 47 min\.$/, "altitude copy");
});

const total = passed + failed;
console.log(`scheduleStatus.test: ${passed}/${total} passed`);
if (failures.length) { console.error(failures.join("\n")); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
export const result = { passed, failed, total };
