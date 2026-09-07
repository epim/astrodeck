// focusVerdict.test.ts — the Focus panel must not endorse a donut field, must
// not send someone to coarse focus from one autofocus step away, and must not
// call a frame full of sharp stars "far out of focus".
//
// Every number below is measured. The blob bars come from the ground-truth
// sweep committed at server/tests/fixtures/focus_sweep (true focus 9900) run
// through the current measure_blob:  0 -> r80 6.0 | ±300 -> 26-34 |
// ±600 -> 58-62 | ±1000 -> 86-94. The star-population bars come from the
// 2026-09-05/06 NGC 604 frames (server/tests/fixtures/ngc604_20260906).
import {
  DEFOCUS_COARSE_R80_PX, HFR_MEANINGLESS_R80_PX, RESOLVED_MAX_HFR,
  RESOLVED_MIN_STARS, defocusMessage, focusState, resolvedStarField,
} from "../focusVerdict";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(c: boolean, m: string) { if (!c) throw new Error(m); }

test("a donut field is reported as defocused, not FAIR", () => {
  // The real 2026-07-31 frame: 1393 "stars", HFR 4.5, an enormous blob. The
  // count is ring fragments, and 4.5 is the box measuring its own geometry —
  // which is why the star-population escape hatch below must not open here.
  const s = focusState({ hfr: 4.5, stars: 1393, defocus_r80: 572 });
  eq(s.kind, "defocused", "HFR 4.5 with a 572px blob must not read as measured");
});

test("a frame full of sharp stars is NOT far out of focus", () => {
  // THE 2026-09-07 DEFECT, from the rig at 01:17. A 60s L sub of NGC 604 that
  // the run's grader measured at HFR 3.32 with 1294 stars was shown as
  // "Far out of focus — blob is 2268 px across — run coarse focus first",
  // because measure_blob had locked onto M33 and this file let any large blob
  // outrank HFR. r80 1134 is the number the server actually published.
  const s = focusState({ hfr: 3.32, stars: 1294, defocus_r80: 1134 });
  eq(s.kind, "measured", "a resolved star field was called far out of focus");
  eq(s.kind === "measured" && s.hfr, 3.32);
});

test("the same blob still wins once the stars stop being stars", () => {
  // Identical blob, identical count — but a box HFR the box can no longer
  // describe. That is a donut field, and it must keep the defocus verdict.
  eq(focusState({ hfr: 4.2, stars: 1294, defocus_r80: 1134 }).kind, "defocused");
});

test("a genuinely focused frame still reports its HFR", () => {
  // r80 6.0 is what true focus measures on the fixture sweep.
  const s = focusState({ hfr: 2.4, stars: 120, defocus_r80: 6.0 });
  eq(s.kind, "measured");
  eq(s.kind === "measured" && s.hfr, 2.4);
});

test("300 steps off focus is NOT sent to coarse focus", () => {
  // THE EARLIER REGRESSION THIS FILE EXISTS FOR. The old single threshold was
  // 25, set against a measure_blob that has since been replaced. On the new
  // metric ±300 steps reads r80 26-34, so 25 fired one autofocus step from
  // perfect and told the user to go run coarse focus.
  //
  // HFR 4.4 is what the sweep fixtures actually grade at ±300 (4.26-4.88), so
  // this frame is correctly NOT a resolved star field.
  for (const r80 of [26, 30, 34]) {
    const s = focusState({ hfr: 4.4, stars: 400, defocus_r80: r80 });
    eq(s.kind, "soft", `r80 ${r80} (±300 steps) must be soft, not defocused`);
    ok(/autofocus/i.test(defocusMessage(r80)),
       `r80 ${r80} must point at autofocus: ${defocusMessage(r80)}`);
    ok(!/coarse/i.test(defocusMessage(r80)),
       `r80 ${r80} must NOT mention coarse focus: ${defocusMessage(r80)}`);
  }
});

test("1000 steps off — the edge of a sweep — still points at autofocus", () => {
  // A default sweep is ±4×350 = ±1400 steps; ±1000 measures r80 86-94.
  eq(focusState({ hfr: 4.5, stars: 200, defocus_r80: 90 }).kind, "soft");
});

test("beyond a sweep's reach, coarse focus is the honest instruction", () => {
  const s = focusState({ hfr: 4.5, stars: 200, defocus_r80: 250 });
  eq(s.kind, "defocused");
  ok(/coarse/i.test(defocusMessage(250)), defocusMessage(250));
});

test("HFR stops being the verdict once the star outgrows its box", () => {
  // Equal to the server's HANDOVER_R80_PX: the same bar coarse focus uses to
  // decide the V-curve can take over. Below it HFR is trustworthy.
  eq(focusState({ hfr: 4.2, stars: 50, defocus_r80: HFR_MEANINGLESS_R80_PX }).kind,
     "measured");
  eq(focusState({ hfr: 4.2, stars: 50, defocus_r80: HFR_MEANINGLESS_R80_PX + 1 }).kind,
     "soft");
});

test("the two bars are ordered, and far apart enough to be different advice", () => {
  ok(DEFOCUS_COARSE_R80_PX > HFR_MEANINGLESS_R80_PX * 4,
     "a single threshold wearing two hats is what caused the regression");
});

test("the star-population gate is a population AND a compact one", () => {
  // Both halves are load-bearing. A count alone cannot do this: the real donut
  // frame L_0001 yields 200 detections. An HFR alone cannot either: three
  // sharp stars are not a population.
  ok(!resolvedStarField({ hfr: 2.7, stars: RESOLVED_MIN_STARS - 1 }),
     "a handful of detections outvoted a blob");
  ok(resolvedStarField({ hfr: 2.7, stars: RESOLVED_MIN_STARS }), "count bar");
  ok(!resolvedStarField({ hfr: RESOLVED_MAX_HFR, stars: 500 }),
     "a donut field grades 3.94-5.00 and must not read as resolved");
  ok(resolvedStarField({ hfr: RESOLVED_MAX_HFR - 0.01, stars: 500 }), "hfr bar");
  ok(!resolvedStarField({ hfr: null, stars: 500 }), "no HFR is not a verdict");
  ok(!resolvedStarField(null) && !resolvedStarField(undefined), "no frame");
});

test("the HFR bar is the server's own, so the two layers cannot drift", () => {
  // imaging/stars.py SIZE_FINE_MAX_BOX_HFR. Stated as a number here because a
  // TS file cannot import it, and pinned so a change on one side is visible.
  eq(RESOLVED_MAX_HFR, 3.8, "server SIZE_FINE_MAX_BOX_HFR");
});

test("an old server with no defocus field behaves exactly as before", () => {
  eq(focusState({ hfr: 2.2, stars: 40 }).kind, "measured");
  eq(focusState({ hfr: null, stars: 40 }).kind, "few-stars");
  eq(focusState({ hfr: 2.2, stars: 1 }).kind, "few-stars");
});

test("no frame at all is its own state", () => {
  eq(focusState(null).kind, "no-frame");
  eq(focusState(undefined).kind, "no-frame");
});

test("the message states the SIZE, not just that it is bad", () => {
  const m = defocusMessage(572);
  ok(m.includes("1144"), `must give the diameter in px: ${m}`);
});

console.log(`focusVerdict.test.ts: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
