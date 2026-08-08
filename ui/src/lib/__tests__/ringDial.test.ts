// ringDial.test.ts — NO TWO CHIPS MAY OVERLAP. That is the whole file.
//
// The dial shipped with its limit written in a header comment ("five items…
// clear each other by 6.6px") and was then handed thirteen exposures. Nothing
// failed, because nothing measured. The DOM test read labels out of the tree and
// reported green while the operator could not read a single value, and while
// Back sat squarely on top of the first option of every second ring — the
// filter L, the shortest exposure, the lowest gain, bin 1 — for the same reason.
//
// So these tests do not ask what a layout renders. They ask where every seat IS,
// including Back's, and fail if any pair is closer than a chip is wide.
import {
  ARC_MAX_OPTIONS, ARC_SEATS, DIAL_CLEAR_PX, DIAL_ITEM_PX, DIAL_R,
  RING_EDGE_PX, RING_HUB_PX, SEAT_PITCH_PX,
  arcSeatFracs, arcSeatXY, arcSeats, dialFractions, dialRadius, minSeatGap,
  needsRing, ringChord, ringMinViewport, ringPlacement, ringRadius, ringSlots,
  ringStep,
} from "../ringDial";

let passed = 0, failed = 0; const failures: string[] = [];
function test(n: string, f: () => void) { try { f(); passed++; } catch (e) { failed++; failures.push(`x ${n}: ${(e as Error).message}`); } }
function eq<T>(a: T, b: T, m = "") { if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`); }
function ok(c: boolean, m: string) { if (!c) throw new Error(m); }
function near(a: number, b: number, tol: number, m = "") {
  if (Math.abs(a - b) > tol) throw new Error(`${m} expected ~${b}, got ${a}`);
}

/** The real lists, from CameraPickers. Restated rather than imported: that
 *  module pulls in the store, and the numbers that broke the dial are the point
 *  of the test, so they belong in front of the reader. */
const EXPOSURES = 13;   // 0.3 0.5 1 2 5 10 15 30 60 90 120 180 300
const GAINS = 10;       // 0 50 100 120 150 200 250 300 400 500
const FILTERS = 7;      // L R G B Ha OIII SII  (Dark is opaque, excluded)
const BINS = 4;

/* ══════════════════════════════════════════ the defect, stated as arithmetic */

test("the reported defect is real and this is the number", () => {
  // 13 chips over a 90° arc: 12 gaps of 7.5°, chord 2·140·sin(3.75°).
  const chord = 2 * DIAL_R * Math.sin((Math.PI / 180) * (90 / (EXPOSURES - 1)) / 2);
  near(chord, 18.3, 0.1, "13 exposures on the old quarter arc");
  ok(chord < DIAL_ITEM_PX,
     "if this ever passes, the premise of the fix has changed");
});

/* ═══════════════════════════════════════════════ the quarter arc, seats and all */

test("the arc's stated capacity is now computed, and it is five", () => {
  eq(ARC_SEATS, 5, "the header always said five; now something checks");
  eq(arcSeats(DIAL_R), 5);
  eq(arcSeats(124), 4, "DIAL_MIN_R is where the fifth seat stops fitting");
});

test("the >4 threshold is derived from the seats, not agreed with", () => {
  // The operator said "more than 4 options is too dense". The geometry says
  // five seats, one of which is Back. Same number, arrived at independently.
  eq(ARC_MAX_OPTIONS, 4);
  eq(needsRing(4, true), false, "four options plus Back is exactly five seats");
  eq(needsRing(5, true), true, "a fifth option needs a sixth seat, and there is none");
  eq(needsRing(BINS, true), false, "binning stays on the arc");
  eq(needsRing(EXPOSURES, true), true);
  eq(needsRing(GAINS, true), true);
  eq(needsRing(FILTERS, true), true, "seven filters overlap too — 36.5px chord");
});

test("BACK GETS ITS OWN SEAT — it used to sit on top of option 0", () => {
  // The bug verbatim: Back was drawn at frac 0 and dialFractions(n) puts
  // option 0 at frac 0 as well, so Back covered it. FILT read "R G B S H O".
  const { back, options } = arcSeatFracs(4, true);
  eq(back, 0);
  eq(options.length, 4);
  ok(!options.includes(0), "no option may be given Back's seat");
  eq(options[0], 0.25);
  eq(options[3], 1);
});

test("no Back means no seat reserved — the categories use the whole arc", () => {
  const { back, options } = arcSeatFracs(5, false);
  eq(back, null);
  eq(options.length, 5);
  eq(options[0], 0);
});

test("an entry category still gets a way back", () => {
  const { back, options } = arcSeatFracs(0, true);
  eq(back, 0);
  eq(options.length, 0);
});

test("NOTHING ON THE ARC OVERLAPS ANYTHING ELSE, Back included", () => {
  // The pair that was broken in production is option-0-against-Back, so the
  // check is over EVERY pair of seats and not just neighbouring options.
  for (let n = 0; n <= ARC_MAX_OPTIONS; n++) {
    const gap = minSeatGap(arcSeatXY(n, true, DIAL_R));
    ok(gap >= DIAL_ITEM_PX,
       `${n} options + Back: closest pair is ${gap.toFixed(1)}px, need ${DIAL_ITEM_PX}px`);
  }
  for (let n = 1; n <= ARC_SEATS; n++) {
    const gap = minSeatGap(arcSeatXY(n, false, DIAL_R));
    ok(gap >= DIAL_ITEM_PX,
       `${n} categories: closest pair is ${gap.toFixed(1)}px, need ${DIAL_ITEM_PX}px`);
  }
});

test("the arc's own clearance is what DIAL_CLEAR_PX promises", () => {
  // 4 options + Back = the 5 seats the shipped design used, 6.6px apart.
  const gap = minSeatGap(arcSeatXY(ARC_MAX_OPTIONS, true, DIAL_R));
  near(gap, 54.6, 0.2, "five seats at R=140");
  ok(gap - DIAL_ITEM_PX >= DIAL_CLEAR_PX,
     "the constant must be the guarantee, and the arc must clear it");
});

test("a stage too small for a usable arc gets no arc at all", () => {
  ok(dialRadius({ w: 200, h: 160 }, { right: 12, bottom: 12 }) < 124,
     "a 200x160 stage must not be given an arc it cannot hold");
  eq(dialRadius({ w: 420, h: 300 }, { right: 12, bottom: 12 }), 140);
  eq(dialRadius(null, { right: 12, bottom: 12 }), 140,
     "unmeasured assumes the full arc — the arc is closed on first paint anyway");
});

test("seats are spread evenly with one at the top", () => {
  eq(dialFractions(5).join(","), "0,0.25,0.5,0.75,1");
  eq(dialFractions(1).join(","), "1");
});

/* ═════════════════════════════════════════════════════════════ the full ring */

test("NOTHING ON THE RING OVERLAPS, at every count that reaches it", () => {
  for (const n of [5, 6, 7, 8, 9, 10, 11, 12, 13, 16, 20]) {
    const r = ringRadius(n);
    const gap = minSeatGap(ringSlots(n, r, Math.PI / 2, 0));
    ok(gap >= DIAL_ITEM_PX,
       `${n} on a ring of ${r}px: closest pair is ${gap.toFixed(1)}px, need ${DIAL_ITEM_PX}px`);
  }
});

test("the ring seats thirteen exposures with room, where the arc could not", () => {
  const r = ringRadius(EXPOSURES);
  eq(r, 113, "27/sin(180/13) = 112.8, ceil");
  near(ringChord(EXPOSURES, r), 54.1, 0.2, "chord between neighbouring exposures");
  ok(ringChord(EXPOSURES, r) - DIAL_ITEM_PX >= DIAL_CLEAR_PX,
     "and it clears by at least what the arc clears by");
  // The whole point, in one comparison: 18.3px before, 54.1px after.
  const before = 2 * DIAL_R * Math.sin((Math.PI / 180) * (90 / (EXPOSURES - 1)) / 2);
  ok(ringChord(EXPOSURES, r) > 2 * before, "the fix must be a fix, not a nudge");
});

test("ten gains and seven filters fit too", () => {
  eq(ringRadius(GAINS), 88);
  eq(ringRadius(FILTERS), 70, "floored by the hub, not by the neighbours");
  ok(minSeatGap(ringSlots(GAINS, ringRadius(GAINS), 0, 0)) >= DIAL_ITEM_PX,
     "gains overlap on their own ring");
  ok(minSeatGap(ringSlots(FILTERS, ringRadius(FILTERS), 0, 0)) >= DIAL_ITEM_PX,
     "filters overlap on their own ring");
});

test("no chip ever lands on the hub", () => {
  for (const n of [5, 7, 10, 13, 16]) {
    const r = ringRadius(n);
    ok(r - DIAL_ITEM_PX / 2 >= RING_HUB_PX / 2 + DIAL_CLEAR_PX,
       `${n} chips at r=${r} crowd the hub — and the hub is the way back`);
  }
});

test("the radius is the SMALLEST that reads, because radius is thumb travel", () => {
  for (const n of [7, 10, 13]) {
    const r = ringRadius(n);
    if (r <= RING_HUB_PX / 2 + DIAL_ITEM_PX / 2 + DIAL_CLEAR_PX) continue;
    ok(ringChord(n, r - 1) < SEAT_PITCH_PX,
       `${n} chips would still fit at ${r - 1}px, so ${r}px is one pixel of `
       + "thumb travel nobody asked for");
  }
});

test("the ring's fit is a SQUARE, which the arc's was not", () => {
  // A quarter arc needed a corner; a full ring needs 2·(r + chip/2 + edge) in
  // BOTH dimensions. This is the calculation that decides the fallback.
  eq(ringMinViewport(EXPOSURES), 2 * (113 + DIAL_ITEM_PX / 2 + RING_EDGE_PX));
  eq(ringMinViewport(EXPOSURES), 298);
  ok(ringMinViewport(EXPOSURES) <= 375,
     "13 exposures must fit a phone held in landscape (short side ~375px)");
  ok(ringMinViewport(GAINS) <= 375, "10 gains must fit that phone too");
});

test("a screen too small refuses the ring rather than drawing a tighter one", () => {
  const tiny = ringPlacement({ w: 260, h: 240 }, { x: 250, y: 230 }, EXPOSURES);
  eq(tiny.fits, false, "260px cannot hold a 298px ring");
  const phone = ringPlacement({ w: 390, h: 700 }, { x: 350, y: 560 }, EXPOSURES);
  eq(phone.fits, true);
});

/* ───────────────────────────────────── reachability: the thing being traded */

test("the hub is CLAMPED TOWARD THE THUMB, never centred on the screen", () => {
  const view = { w: 390, h: 700 };
  const anchor = { x: 350, y: 560 };          // the disc, bottom-right of a phone
  const p = ringPlacement(view, anchor, EXPOSURES);
  const pad = 113 + 24 + RING_EDGE_PX;         // 149
  eq(p.cx, view.w - pad, "pushed as far right as the ring can go, not to 195");
  eq(p.cy, view.h - pad, "and as far down — 9px, the least that keeps it on screen");
  ok(p.cx > view.w / 2 && p.cy > view.h / 2,
     "a ring centred on the screen would be the lazy answer and the wrong one");
  ok(Math.hypot(p.cx - anchor.x, p.cy - anchor.y) < p.r,
     "on a phone the disc ends up INSIDE the ring, so the near arc is under "
     + "the thumb that just tapped it");
});

test("worst-case reach is what the clamp minimises, and it is bounded", () => {
  const view = { w: 390, h: 700 };
  const anchor = { x: 350, y: 560 };
  const p = ringPlacement(view, anchor, EXPOSURES);
  const worst = Math.hypot(p.cx - anchor.x, p.cy - anchor.y) + p.r + DIAL_ITEM_PX / 2;
  // The arc's own committed reach is DIAL_R + chip/2 = 164px. The ring costs
  // more — it has to, thirteen chips do not fit in the sweep — but the clamp
  // is what keeps it to one ring-diameter rather than half a screen.
  ok(worst <= 2 * p.r + DIAL_ITEM_PX / 2,
     `worst reach ${worst.toFixed(0)}px exceeds a ring diameter — the clamp is not working`);
  // And moving the hub any closer to the thumb would push chips off screen.
  ok(p.cx + p.r + DIAL_ITEM_PX / 2 <= view.w - 1,
     "the ring must stay on screen; that is the only reason it moved at all");
});

test("THE CURRENT VALUE SITS NEAREST THE THUMB, and its neighbours flank it", () => {
  const view = { w: 390, h: 700 };
  const anchor = { x: 350, y: 560 };
  const p = ringPlacement(view, anchor, EXPOSURES);
  const sel = 7;                                  // say 30s of the 13
  const slots = ringSlots(EXPOSURES, p.r, p.near, sel);
  const dist = (i: number) =>
    Math.hypot(p.cx + slots[i].x - anchor.x, p.cy + slots[i].y - anchor.y);
  for (let i = 0; i < EXPOSURES; i++) {
    if (i === sel) continue;
    ok(dist(sel) <= dist(i) + 0.01,
       `chip ${i} is nearer the thumb than the current value — the one pick `
       + "that must be free is a nudge off where you already are");
  }
  // one stop longer and one stop shorter are the two nearest after it
  const rank = [...Array(EXPOSURES).keys()].sort((a, b) => dist(a) - dist(b));
  eq(rank[0], sel);
  ok((rank[1] === sel - 1 && rank[2] === sel + 1)
     || (rank[1] === sel + 1 && rank[2] === sel - 1),
     `after the current value the nearest chips were ${rank[1]},${rank[2]} — `
     + "they must be its neighbours in the list");
});

test("list order runs CLOCKWISE from the current value", () => {
  // Screen axes: +y is DOWN, so an increasing angle turns clockwise. With the
  // current value pinned at the bottom of the ring, the next one along must
  // move left, not right.
  const slots = ringSlots(8, 100, Math.PI / 2, 0);
  near(slots[0].x, 0, 0.2, "slot 0 pinned at the near pole");
  near(slots[0].y, 100, 0.2);
  ok(slots[1].x < slots[0].x, "the next value goes clockwise, not anticlockwise");
});

test("a hub sitting exactly on the thumb still has a near pole", () => {
  const p = ringPlacement({ w: 400, h: 400 }, { x: 200, y: 200 }, 5);
  eq(p.near, Math.PI / 2, "straight down: the reachable pole on any phone");
});

/* ─────────────────────────────────────────────────────────────── the keyboard */

test("arrows walk the ring both ways and WRAP", () => {
  eq(ringStep(0, 13, "ArrowRight"), 1);
  eq(ringStep(12, 13, "ArrowRight"), 0, "a circle has no last item");
  eq(ringStep(0, 13, "ArrowLeft"), 12);
  eq(ringStep(5, 13, "ArrowDown"), 6);
  eq(ringStep(5, 13, "ArrowUp"), 4);
  eq(ringStep(5, 13, "Home"), 0);
  eq(ringStep(5, 13, "End"), 12);
});

test("wrapping is safe here BECAUSE nothing commits on a keypress", () => {
  // stepDial.stepByKey deliberately clamps: it commits on release, and wrapping
  // from 1000 to 1 is how you move a focuser a thousand steps by mistake. This
  // ring applies nothing until Enter or a tap, so the same choice would only
  // strand the operator at an invisible seam.
  eq(ringStep(12, 13, "ArrowRight"), 0);
});

test("a key the ring does not own is handed back, not swallowed", () => {
  eq(ringStep(3, 13, "Enter"), null, "must not preventDefault activation");
  eq(ringStep(3, 13, "Escape"), null, "Escape belongs to the dial");
  eq(ringStep(3, 13, "Tab"), null);
  eq(ringStep(3, 0, "ArrowRight"), null);
});

console.log(`ringDial.test.ts: ${passed} passed, ${failed} failed`);
if (failed) { failures.forEach((f) => console.error(f)); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
export default { passed, failed, total: passed + failed };
