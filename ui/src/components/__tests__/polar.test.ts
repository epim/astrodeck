// Polar (HERO 2) pure-logic regression — the verdict tier, the knob-direction
// decode, and the ZOOM LADDER that the TPPA wizard's reticle binds to. Same
// inline-assert / `tsx` style as the other suites (no jsdom, no runtime timers):
// these are the pure functions the reticle and the Align view share, so a
// binding regression fails loudly here.
//
// This file used to end in a bare `console.log("… OK")`, which run-tests.mjs
// scores by EXIT CODE and counts as zero assertions — thirty real checks that
// never appeared in the suite total. It now reports a tally like its siblings.
//
// Run directly:  npx tsx src/components/__tests__/polar.test.ts
// Also type-checked by `tsc -b` in the build.

import {
  polarTier, knobHint, polarInstruction, boundaryArcmin, ladderRings, CEIL_LADDER,
  tierMarks, ringNote, TIER_BOUNDS, mergeStepRings, freshRings,
} from "../polar";

// ------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg}: expected ${String(b)}, got ${String(a)}`);
}
function eqArr(a: number[], b: number[], msg: string): void {
  if (a.length !== b.length || a.some((v, i) => v !== b[i])) {
    throw new Error(`${msg}: expected [${b}], got [${a}]`);
  }
}
function isNull(a: unknown, msg: string): void {
  if (a !== null) throw new Error(`${msg}: expected null, got ${JSON.stringify(a)}`);
}

// ============================================================ verdict tiers
// <2′ excellent · 2–10′ good · >10′ keep going. Untouched by the zoom work —
// pinned here because the ladder deliberately does NOT share these boundaries.
test("verdict tiers", () => {
  eq(polarTier(0), "excellent", "0′ excellent");
  eq(polarTier(1.99), "excellent", "1.99′ excellent");
  eq(polarTier(2), "good", "2′ good (boundary is good, not excellent)");
  eq(polarTier(9.99), "good", "9.99′ good");
  eq(polarTier(10), "keepgoing", "10′ keep going (boundary)");
  eq(polarTier(45), "keepgoing", "45′ keep going");
});

// ============================================================ knob hints
test("azimuth knob labels (authoritative, from the native engine)", () => {
  eq(knobHint("left_west", 0.7, "az")?.arrow, "◀", "left_west arrow");
  eq(knobHint("left_west", 0.7, "az")?.text, "turn W", "left_west text");
  eq(knobHint("left_east", 0.7, "az")?.text, "turn E", "left_east text");
  eq(knobHint("right_west", 0.7, "az")?.arrow, "▶", "right_west arrow");
  eq(knobHint("right_east", 0.7, "az")?.text, "turn E", "right_east text");
  // wrong-axis label must be rejected, not mis-rendered
  isNull(knobHint("up", 0.7, "az"), "alt label on az axis rejected");
});

test("azimuth fallback (NINA/sim emit no knob label)", () => {
  eq(knobHint(null, -3, "az")?.arrow, "◀", "az<0 fallback arrow");
  eq(knobHint(null, -3, "az")?.text, "turn E", "az<0 fallback text");
  eq(knobHint(null, 3, "az")?.text, "turn W", "az>0 fallback text");
  isNull(knobHint(null, 0, "az"), "az=0 fallback null");
  isNull(knobHint(undefined, NaN, "az"), "az NaN fallback null");
});

test("altitude knob labels + fallback", () => {
  eq(knobHint("up", 0.6, "alt")?.arrow, "▲", "up arrow");
  eq(knobHint("up", 0.6, "alt")?.text, "raise", "up text");
  eq(knobHint("down", 0.6, "alt")?.arrow, "▼", "down arrow");
  eq(knobHint("down", 0.6, "alt")?.text, "lower", "down text");
  isNull(knobHint("left_west", 0.6, "alt"), "az label on alt axis rejected");
  eq(knobHint(null, 4, "alt")?.text, "lower", "alt>0 fallback lower");
  eq(knobHint(null, -4, "alt")?.text, "raise", "alt<0 fallback raise");
  isNull(knobHint(null, 0, "alt"), "alt=0 fallback null");
});

// ============================================================ the zoom ladder
// The reticle's outer ring is worth boundaryArcmin(total) arcminutes.

test("ladder reaches BELOW 10′ — the zoom-in half of the feature", () => {
  // The old ladder started at 10, so a 40″ error sat on the centre pip with no
  // way to see which way the last of the skew pointed. These four rungs are the
  // whole point of the change; if the floor creeps back up to 10 this fails.
  eq(CEIL_LADDER[0], 1, "floor rung is 1′");
  eq(boundaryArcmin(0), 1, "zero error uses the floor rung");
  eq(boundaryArcmin(0.5), 1, "30″ error → 1′ scale");
  eq(boundaryArcmin(0.93), 2, "just past 1/1.08 steps to 2′");
  eq(boundaryArcmin(2.7), 3, "2.7′ → 3′ scale");
  eq(boundaryArcmin(2.8), 5, "2.8′ needs the 5′ rung (8% headroom)");
});

test("cold fit (no current rung) keeps its 8% headroom and its ceiling", () => {
  eq(boundaryArcmin(9.25), 10, "9.25′ still fits 10′");
  eq(boundaryArcmin(9.26), 15, "9.26′ overflows 10′ (the 1.08 margin)");
  eq(boundaryArcmin(45), 50, "45′ → 50′");
  eq(boundaryArcmin(400), 300, "beyond the ladder clamps to its top rung");
  eq(boundaryArcmin(NaN), 1, "NaN is treated as no error, not as a huge one");
  eq(boundaryArcmin(-5), 1, "a negative total cannot select a rung by sign");
});

// --- HYSTERESIS. The defect being fixed: with one 1.08 margin, rung 10 was
// chosen exactly while total <= 9.259′, so an ordinary reading jittering across
// 9.26′ flipped 10↔15 every update and moved every ring and the dot while the
// mount sat still. Rising and falling MUST cross at different errors.
test("hysteresis: rising error steps OUT at 9.26′", () => {
  eq(boundaryArcmin(9.25, 10), 10, "holds 10′ just under the out-threshold");
  eq(boundaryArcmin(9.3, 10), 15, "steps out once the error overflows 10′");
});

test("hysteresis: falling error does NOT step back in until 7.41′", () => {
  eq(boundaryArcmin(9.0, 15), 15, "9.0′ holds 15′ — a cold fit would say 10′");
  eq(boundaryArcmin(8.0, 15), 15, "8.0′ still holds 15′");
  eq(boundaryArcmin(7.41, 15), 15, "7.41′ still holds 15′");
  eq(boundaryArcmin(7.4, 15), 10, "7.4′ clears 10′ by 35% — now zoom in");
});

test("hysteresis: the SAME error yields different rungs by direction", () => {
  // This is the assertion that makes it hysteresis rather than a rounder
  // threshold: one input, two answers, decided by where you came from.
  const rising = boundaryArcmin(9.25, 10);
  const falling = boundaryArcmin(9.25, 15);
  eq(rising, 10, "arriving from 10′ stays at 10′");
  eq(falling, 15, "arriving from 15′ stays at 15′");
  if (rising === falling) throw new Error("no hysteresis band: both directions agree");
});

test("hysteresis holds on the sub-10 rungs too", () => {
  eq(boundaryArcmin(1.9, 3), 3, "1.9′ holds the 3′ rung");
  eq(boundaryArcmin(1.4, 3), 2, "1.4′ clears 2′ with room — zoom in");
  eq(boundaryArcmin(2.5, 2), 3, "outgrowing 2′ steps out immediately");
  eq(boundaryArcmin(0.2, 1), 1, "nothing below the floor rung to step into");
});

test("a big jump crosses several rungs at once, in both directions", () => {
  // Hysteresis must damp jitter, never lag a real slew of the reading.
  eq(boundaryArcmin(120, 10), 150, "10′ → 150′ in one update");
  eq(boundaryArcmin(0.4, 50), 1, "50′ → 1′ in one update");
});

test("an unknown current rung falls back to the cold fit", () => {
  // Guards the ladder against a stale rung left over from an older build.
  eq(boundaryArcmin(8, 12), 10, "12 is not on the ladder — ignore it");
  eq(boundaryArcmin(8, undefined), 10, "no current rung → cold fit");
});

test("boundaryArcmin is idempotent in `current`", () => {
  // The reticle writes the rung to a ref DURING render, so StrictMode calls this
  // twice with its own previous answer. If that were not a fixed point the
  // double invocation would zoom twice per reading.
  for (const t of [0, 0.4, 1.2, 3.3, 7.5, 9.26, 22, 61, 480]) {
    for (const c of CEIL_LADDER) {
      const once = boundaryArcmin(t, c);
      eq(boundaryArcmin(t, once), once, `t=${t} c=${c} not a fixed point`);
    }
  }
});

// --- RING SETS. Every ring is labelled with its own arcminute value, which is
// what buys the right to move them at all (the old code pinned 2′/10′).
test("ring sets are the boundary plus well-separated rungs below it", () => {
  eqArr(ladderRings(1), [1], "1′ scale has only the boundary");
  eqArr(ladderRings(2), [1, 2], "2′ scale");
  eqArr(ladderRings(3), [1, 3], "3′ scale skips 2 (too close to 3)");
  eqArr(ladderRings(5), [1, 3, 5], "5′ scale");
  eqArr(ladderRings(10), [3, 5, 10], "10′ scale");
  eqArr(ladderRings(15), [3, 5, 15], "15′ scale skips 10 (too close to 15)");
  eqArr(ladderRings(30), [5, 15, 30], "30′ scale skips 20");
  eqArr(ladderRings(300), [75, 150, 300], "top rung");
});

test("every rung's ring set is legible and self-consistent", () => {
  for (const s of CEIL_LADDER) {
    const rings = ladderRings(s);
    eq(rings[rings.length - 1], s, `${s}′: boundary must be the outermost ring`);
    if (rings.length > 3) throw new Error(`${s}′: ${rings.length} rings is more than the box holds`);
    for (const r of rings) {
      if (!CEIL_LADDER.includes(r)) throw new Error(`${s}′: ring ${r} is not a ladder rung`);
    }
    for (let i = 1; i < rings.length; i++) {
      if (rings[i] <= rings[i - 1]) throw new Error(`${s}′: rings not ascending`);
      // Adjacent ladder rungs (20 vs 30) put their labels in the same gutter and
      // read as a smear rather than a scale; the set must stay 0.62-separated.
      if (rings[i - 1] > rings[i] * 0.62) throw new Error(`${s}′: ${rings[i - 1]}/${rings[i]} too close to read apart`);
    }
    // …and the innermost must not collapse onto the centre pip. The tightest
    // set the 0.62 gate produces is 30′ → [5,15,30], i.e. 1/6 of the boundary;
    // the reticle drops any label under 16 of its 165 units (~0.097), so 0.15
    // is the floor that keeps every ring the function emits actually labelled.
    if (rings[0] / s < 0.15) throw new Error(`${s}′: innermost ring ${rings[0]} is too small a fraction`);
  }
});

// --- TIER MARKS. Deriving the rings from the rung dropped the 2′ goal off the
// screen: `ladderRings` yields 2 at exactly one of fourteen rungs, so from 3′ to
// 30′ — every scale at which the bolts can still move you — the target the whole
// screen exists to reach had no stroke and no label, only a change of fill tint.
// In .night that tint is 1.10:1 (all three status tokens are reds at 0.05–0.10),
// which index.css:154-157 forbids by name. These pin the mark back on.
test("the 2′ target is marked at every scale where a user is working toward it", () => {
  for (const s of [3, 5, 10, 15, 20, 30]) {
    const marked = tierMarks(s).includes(2) || ladderRings(s).includes(2);
    if (!marked) throw new Error(`${s}′ scale leaves the 2′ goal unmarked`);
  }
  eq(tierMarks(5).includes(2), true, "the review's case: 5′ scale, bolts coming down to 2′");
});

test("tier marks never duplicate a ring the ladder already draws", () => {
  // One circle, one label. A second "10′ keep going" in the other gutter is the
  // double-rendered-copy defect, not a redundancy that helps.
  for (const s of CEIL_LADDER) {
    const ladder = ladderRings(s);
    for (const t of tierMarks(s)) {
      if (ladder.includes(t)) throw new Error(`${s}′: ${t}′ marked twice`);
      if (!TIER_BOUNDS.includes(t)) throw new Error(`${s}′: ${t}′ is not a verdict boundary`);
      if (t >= s) throw new Error(`${s}′: ${t}′ mark is not inside the reticle`);
    }
  }
});

test("tier marks drop off rather than crowd the centre pip", () => {
  // The pip is 3 of the reticle's 165 units; a mark under 10 units is on top of
  // it. At 50′ the 2′ circle would be r=6.6 — and at 50′ the honest instruction
  // is "use the bolts", not "you are nearly there".
  eqArr(tierMarks(15), [2, 10], "15′ marks both boundaries");
  eqArr(tierMarks(30), [2, 10], "30′ still marks 2′ (r=11, clear of the pip)");
  eqArr(tierMarks(50), [10], "at 50′ the 2′ mark would sit on the pip");
  eqArr(tierMarks(200), [], "at 200′ neither boundary is drawable");
  eqArr(tierMarks(20), [2], "10′ is a ladder ring at the 20′ scale");
  eqArr(tierMarks(2), [], "the 2′ rung IS the boundary ring");
  eqArr(tierMarks(1), [], "nothing inside the floor rung");
});

test("tier marks say what they are for", () => {
  eq(ringNote(2), " stop", "2′ is the stop line");
  eq(ringNote(10), " keep going", "10′ is the keep-going line");
  eq(ringNote(5), "", "an ordinary rung is just a number");
});

test("a rung that is not on the ladder still yields a drawable ring", () => {
  // Mid-tween the scale is a continuous value; the caller only ever passes the
  // TARGET rung, but a wrong caller must not blank the reticle.
  eqArr(ladderRings(7), [7], "off-ladder value degrades to a single ring");
});

// --- STEP RINGS. A ladder step cross-fades: the outgoing rings dim while the
// incoming ones glide in. A NEW READING ARRIVING MID-STEP is the case that was
// broken — the retarget has to inherit both the ring set on screen and how far
// each of those rings had already faded.
test("a mid-step retarget keeps the rings that are actually on screen", () => {
  // Step 10′→50′ in flight. What is drawn is union([3,5,10],[15,30,50]).
  const onScreen = mergeStepRings(freshRings(ladderRings(10)), 50, 0);
  eqArr(onScreen.map((x) => x.r), [3, 5, 10, 15, 30, 50], "the union mid-step");
  // Now a reading retargets to 20′ at ~78% of the way. Before this fix the merge
  // was computed from ladderRings(10) — the rung left two steps ago — so 15, 30
  // and 50 were absent from the union and blinked out in a single frame.
  const after = mergeStepRings(onScreen, 50, 0.78);
  eqArr(after.map((x) => x.r), [3, 5, 10, 15, 30, 50], "nothing is dropped by the retarget");
});

test("a ring that was already fading does not brighten when the target changes", () => {
  const onScreen = mergeStepRings(freshRings(ladderRings(10)), 50, 0);
  const dim = onScreen.find((x) => x.r === 3)!.fade;      // leaving: starts at 0.5
  eq(dim, 0.5, "a ring starts leaving at half strength");
  const after = mergeStepRings(onScreen, 50, 0.78);
  const carried = after.find((x) => x.r === 3)!.fade;
  eq(Math.round(carried * 1000) / 1000, 0.11, "0.5 × (1 − 0.78) is carried forward");
  if (carried > dim) throw new Error("a leaving ring brightened on retarget");
  // …while a ring that is only NOW leaving starts from full half-strength.
  eq(after.find((x) => x.r === 50)!.fade, 0.5, "50′ was staying, so it resets");
});

test("step rings are ascending, deduplicated, and never lose the incoming set", () => {
  for (const from of CEIL_LADDER) {
    for (const to of CEIL_LADDER) {
      const merged = mergeStepRings(freshRings(ladderRings(from)), to, 0.5);
      const rs = merged.map((x) => x.r);
      for (let i = 1; i < rs.length; i++) {
        if (rs[i] <= rs[i - 1]) throw new Error(`${from}→${to}: not ascending / duplicated`);
      }
      for (const r of ladderRings(to)) {
        if (!rs.includes(r)) throw new Error(`${from}→${to}: incoming ring ${r} missing`);
      }
      for (const r of ladderRings(from)) {
        if (!rs.includes(r)) throw new Error(`${from}→${to}: outgoing ring ${r} vanished unfaded`);
      }
    }
  }
});

// ============================================================ instruction copy
// "What do I turn next" — its own 30′/10′/1′ boundaries, NOT the verdict's.
test("instruction copy: above 30′ no bolt has the travel", () => {
  eq(polarInstruction(45), "Rotate the tripod, or reposition the pier.", "45′");
  eq(polarInstruction(30.1), "Rotate the tripod, or reposition the pier.", "just over 30′");
});

test("instruction copy: 10′–30′ is the bolts' range", () => {
  eq(polarInstruction(30), "Use the azimuth and altitude bolts.", "30′ boundary is bolts");
  eq(polarInstruction(18), "Use the azimuth and altitude bolts.", "18′");
  eq(polarInstruction(10), "Use the azimuth and altitude bolts.", "10′ boundary is bolts");
});

test("instruction copy: 1′–10′ is fine adjustment", () => {
  eq(polarInstruction(9.99), "Fine bolt adjustment.", "just under 10′");
  eq(polarInstruction(3), "Fine bolt adjustment.", "3′");
  eq(polarInstruction(1), "Fine bolt adjustment.", "1′ boundary is fine");
});

test("instruction copy: under 1′ says stop", () => {
  eq(polarInstruction(0.99), "Good enough for most imaging.", "just under 1′");
  eq(polarInstruction(0), "Good enough for most imaging.", "0′");
});

test("instruction copy survives a non-finite reading", () => {
  eq(polarInstruction(NaN), "Fine bolt adjustment.", "NaN must not claim you are done");
});

test("instruction and verdict are INDEPENDENT scales, by design", () => {
  // A future 'consistency' cleanup that reconciles these two is a regression:
  // 25′ and 35′ are the same verdict but different actions, and 1.5′ and 5′ are
  // the same action but different verdicts.
  eq(polarTier(25), polarTier(35), "25′ and 35′ share a verdict tier");
  if (polarInstruction(25) === polarInstruction(35)) {
    throw new Error("instruction copy collapsed onto the verdict's boundaries");
  }
  eq(polarInstruction(1.5), polarInstruction(5), "1.5′ and 5′ share an instruction");
  if (polarTier(1.5) === polarTier(5)) {
    throw new Error("verdict tiers collapsed onto the instruction's boundaries");
  }
});

// ------------------------------------------------------------ report
if (failed) {
  console.error(`polar: ${passed} passed, ${failed} FAILED`);
  for (const f of failures) console.error("  ✗ " + f);
  // Signal failure to any runner without importing node:process types.
  const proc = (globalThis as unknown as { process?: { exitCode?: number } }).process;
  if (proc) proc.exitCode = 1;
} else {
  console.log(`polar: ${passed} passed`);
}
