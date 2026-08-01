// focusVerdict.test.ts — the Focus panel must not endorse a donut field, and
// must not send someone to coarse focus from one autofocus step away.
//
// Every number below is measured, from the ground-truth sweep committed at
// server/tests/fixtures/focus_sweep (true focus 9900) run through the current
// measure_blob:  0 -> r80 6.0 | ±300 -> 26-34 | ±600 -> 58-62 | ±1000 -> 86-94.
import {
  DEFOCUS_COARSE_R80_PX, HFR_MEANINGLESS_R80_PX, defocusMessage, focusState,
} from "../focusVerdict";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(c: boolean, m: string) { if (!c) throw new Error(m); }

test("a donut field is reported as defocused, not FAIR", () => {
  // The real 2026-07-31 frame: 1393 "stars", HFR 4.5, an enormous blob.
  const s = focusState({ hfr: 4.5, stars: 1393, defocus_r80: 572 });
  eq(s.kind, "defocused", "HFR 4.5 with a 572px blob must not read as measured");
});

test("blob size outranks a healthy-looking star count AND a healthy HFR", () => {
  eq(focusState({ hfr: 2.1, stars: 500, defocus_r80: 300 }).kind, "defocused");
});

test("a genuinely focused frame still reports its HFR", () => {
  // r80 6.0 is what true focus measures on the fixture sweep.
  const s = focusState({ hfr: 2.4, stars: 120, defocus_r80: 6.0 });
  eq(s.kind, "measured");
  eq(s.kind === "measured" && s.hfr, 2.4);
});

test("300 steps off focus is NOT sent to coarse focus", () => {
  // THE REGRESSION THIS FILE EXISTS FOR. The old single threshold was 25, set
  // against a measure_blob that has since been replaced. On the new metric
  // ±300 steps reads r80 26-34, so 25 fired one autofocus step from perfect and
  // told the user to go run coarse focus.
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
  eq(focusState({ hfr: 3.0, stars: 50, defocus_r80: HFR_MEANINGLESS_R80_PX }).kind,
     "measured");
  eq(focusState({ hfr: 3.0, stars: 50, defocus_r80: HFR_MEANINGLESS_R80_PX + 1 }).kind,
     "soft");
});

test("the two bars are ordered, and far apart enough to be different advice", () => {
  ok(DEFOCUS_COARSE_R80_PX > HFR_MEANINGLESS_R80_PX * 4,
     "a single threshold wearing two hats is what caused the regression");
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
