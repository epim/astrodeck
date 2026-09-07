// polarReticleDom.test.tsx — which side of the polar reticle is EAST.
//
//   Run directly:  npx tsx src/components/__tests__/polarReticleDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// One question, asked at the DOM because it is only answerable there: the dot's
// x coordinate and the axis label's x coordinate are computed in different
// places by different code, and nothing but geometry connects them.
//
// The convention, end to end:
//   * astro-tppa, error_det.rs::azimuth_direction — a POSITIVE azimuth error in
//     the north means the mount axis lies EAST of the pole, and the correction
//     is MoveLeft(West). polar.test.ts already pins the UI half of that
//     (knobHint(null, +az, "az").text === "turn W").
//   * PolarReticle plots the dot at dx = cx + eAz·k·scale, so +az is to the
//     RIGHT of centre.
//   * Facing north, east is on your right. Right half = east. The dot was
//     always correct.
//
// The LABEL was not. A single "AZ E" tick sat at the LEFT edge, and on
// 2026-08-07 the operator believed it: the right half read as west, the azimuth
// bolt went the wrong way, and the error climbed 430′ → 500′. The label is now
// at both ends, which is the part these tests pin — from one tick the handedness
// can be inferred backwards, and it was.
//
// 2026-09-07 added the rest of the chain, after the same instrument was reported
// mirrored again on a night when every NUMBER was right (server-side signs
// re-verified against an independent forward model in
// server/tests/test_tppa_engine_geometry.py). What was wrong was the picture:
// the skew vector's arrowhead sat on the DOT, so the loudest mark on the screen
// pointed from the pole out to the axis while "◀ AZ turn W" sat beside it saying
// the opposite — and nothing anywhere said whether the dot meant "you are here"
// or "go here". Pinned below: the arrow runs dot → pole, the legend names both
// marks, and the altitude term is held to the identical convention so the two
// axes can never drift apart again.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// matches:true = prefers-reduced-motion. Both of the reticle's tweens then
// answer straight from their props (useEasedPoint / useEasedScale short-circuit
// when animation is off), so the geometry under test is the geometry of the
// reading rather than of whichever animation frame the test happened to catch.
win.matchMedia = () => ({
  matches: true, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "getComputedStyle", "matchMedia",
]) {
  const v = k === "window" ? win : win[k];
  // Node >=21 defines `navigator` as a getter-only global; defineProperty
  // works for every key (same pattern as slewPadDom.test.tsx).
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
// A REAL timestamp, and a working cancel. The stub used elsewhere in this
// directory hands the callback a constant 0, which is fine for a component that
// only wants a frame — but the reticle's tween computes (now - t0)/600 against a
// performance.now() start, so a frozen 0 makes progress permanently negative,
// the ease never reaches 1, and the rAF chain never terminates: the file hangs
// instead of failing. `reduce` also only becomes true in an effect, so the very
// first commit always starts a tween that the second one has to cancel.
g.requestAnimationFrame = (cb: (t: number) => void) =>
  setTimeout(() => cb(performance.now()), 0) as unknown as number;
g.cancelAnimationFrame = (h: number) => clearTimeout(h as unknown as ReturnType<typeof setTimeout>);
g.IS_REACT_ACT_ENVIRONMENT = true;   // React 18: makes act() flush updates

// ------------------------------------------------------------------- imports
import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react";

// House harness (run-tests.mjs scores the printed tally / exported result):
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
};

const { PolarReticle, knobHint } = await import("../polar");

// The reticle's own geometry constants (PolarReticle: size 380, R 165).
const CX = 190;
const CY = 190;

const root = createRoot(win.document.getElementById("root"));
const render = (az: number, alt: number) =>
  act(() => root.render(React.createElement(PolarReticle, { az, alt, active: true })));

/** Every <text> in the SVG, as { text, x, anchor }. */
const texts = () =>
  Array.from(win.document.querySelectorAll("text")).map((t: any) => ({
    text: (t.textContent ?? "").trim(),
    x: Number(t.getAttribute("x")),
    y: Number(t.getAttribute("y")),
    anchor: t.getAttribute("text-anchor"),
  }));
const label = (want: string) => texts().find((t) => t.text === want);

/** The error dot's centre. Both dot circles carry cx = dx; r=7 is the outer
 *  ring of the marker and is unique in the drawing. */
const dotX = (): number => {
  const c: any = Array.from(win.document.querySelectorAll("circle"))
    .find((el: any) => el.getAttribute("r") === "7");
  assert.ok(c, "no error dot in the reticle — nothing to compare a label against");
  return Number(c.getAttribute("cx"));
};
/** …and its y, for the altitude half of the same convention. */
const dotY = (): number => {
  const c: any = Array.from(win.document.querySelectorAll("circle"))
    .find((el: any) => el.getAttribute("r") === "7");
  assert.ok(c, "no error dot in the reticle");
  return Number(c.getAttribute("cy"));
};
/** The skew vector, as { tail, head } points. The line is drawn dot → pole, so
 *  the TAIL sits on the dot and the arrowhead (x2/y2) sits just short of centre:
 *  the head names the CORRECTION. Computed from the same dx/dy as the dot but by
 *  entirely different markup, which is what makes comparing them worth anything. */
const vector = () => {
  const l: any = win.document.querySelector("line[marker-end]");
  assert.ok(l, "no skew vector drawn");
  return {
    tail: { x: Number(l.getAttribute("x1")), y: Number(l.getAttribute("y1")) },
    head: { x: Number(l.getAttribute("x2")), y: Number(l.getAttribute("y2")) },
  };
};
const dist = (p: { x: number; y: number }, q: { x: number; y: number }) =>
  Math.hypot(p.x - q.x, p.y - q.y);

// ----------------------------------------------------------------------------

test("the reticle actually rendered — the anti-blank-page guard", () => {
  // Every assertion below is "x is on this side of centre". With nothing drawn
  // they would fail loudly rather than pass, but a named guard says which.
  render(4, 2);
  assert.ok(win.document.querySelector("svg"), "no reticle SVG in the document");
  assert.ok(texts().length > 0, "the reticle drew no text at all");
});

test("BOTH ends of the azimuth axis are labelled", () => {
  // One tick is an invitation to infer the other end, and the inference that
  // was made on 2026-08-07 was backwards. Two ticks cannot be read wrong.
  render(4, 2);
  assert.ok(label("AZ E"), "no AZ E label on the reticle");
  assert.ok(label("AZ W"),
    "the azimuth axis is labelled at one end only — the other end is left to be guessed, " +
    "which is exactly how the 430′ → 500′ night happened");
});

test("east is on the RIGHT of the reticle, west on the left", () => {
  render(4, 2);
  const e = label("AZ E")!;
  const w = label("AZ W")!;
  assert.ok(e.x > CX, `AZ E is drawn at x=${e.x}, left of centre (${CX}) — facing north, east is right`);
  assert.ok(w.x < CX, `AZ W is drawn at x=${w.x}, right of centre (${CX})`);
});

test("a POSITIVE azimuth error puts the dot on the AZ E side", () => {
  // THE SIGN CONVENTION, pinned. dx = cx + eAz·k, and +az means the axis is
  // east of the pole (error_det.rs::azimuth_direction). If someone ever swaps
  // the labels back, or flips the plot, this is the assertion that stops it.
  render(4, 0.5);
  const e = label("AZ E")!;
  const w = label("AZ W")!;
  const d = dotX();
  assert.ok(d > CX,
    `az=+4′ drew the dot at x=${d}, on the west side of centre (${CX}) — the plot and the ` +
    "engine's sign convention disagree");
  assert.ok(Math.abs(d - e.x) < Math.abs(d - w.x),
    `az=+4′ put the dot at x=${d}, nearer the AZ W label (x=${w.x}) than the AZ E one (x=${e.x})`);
  assert.equal(vector().tail.x, d,
    "the vector's tail and the dot disagree about where the error is");
});

test("a NEGATIVE azimuth error puts the dot on the AZ W side", () => {
  render(-4, 0.5);
  const e = label("AZ E")!;
  const w = label("AZ W")!;
  const d = dotX();
  assert.ok(d < CX, `az=-4′ drew the dot at x=${d}, on the east side of centre (${CX})`);
  assert.ok(Math.abs(d - w.x) < Math.abs(d - e.x),
    `az=-4′ put the dot at x=${d}, nearer the AZ E label (x=${e.x}) than the AZ W one (x=${w.x})`);
});

test("the label agrees with the knob hint, which agrees with the engine", () => {
  // The chain the operator actually follows: see the dot on a side, read the
  // label for that side, turn the bolt the way the hint says. A dot on the east
  // side must be corrected by turning WEST — anything else and the two halves of
  // the instrument are telling the user opposite things.
  render(4, 0.5);
  const e = label("AZ E")!;
  assert.ok(dotX() > CX && e.x > CX, "precondition: +az must plot on the AZ E side");
  assert.equal(knobHint(null, 4, "az")?.text, "turn W",
    "a mount axis east of the pole is corrected by turning west; if this ever reads 'turn E' " +
    "the reticle and the hint are pointing opposite ways");
  assert.equal(knobHint(null, -4, "az")?.text, "turn E",
    "a mount axis west of the pole is corrected by turning east");
});

test("the correction arrow points AT the pole, not at the axis", () => {
  /* THE 2026-09-05 REPORT. The dot's side was right, the "turn W" was right,
     and the operator still read the instrument as mirrored — because the arrow
     ran centre → dot, so the biggest directional mark on the screen pointed
     from the pole out to the axis while the words a few units away said the
     opposite. An arrow is an instruction to everybody who has ever seen one.
     It now runs dot → pole, which IS the correction, and stops short of the
     centre so the pole target stays visible under it. */
  render(4, 3);
  const v = vector();
  const centre = { x: CX, y: CY };
  assert.equal(v.tail.x, dotX(), "the arrow does not start at the dot");
  assert.ok(dist(v.head, centre) < dist(v.tail, centre),
    `the arrowhead (${v.head.x},${v.head.y}) is FURTHER from the pole than its tail ` +
    `(${v.tail.x},${v.tail.y}) — it is pointing at the error instead of at the fix`);
  assert.ok(dist(v.head, centre) > 1,
    "the arrowhead lands on the pole target itself, hiding the mark being aimed at");
});

test("the reticle says which mark is which", () => {
  // Both readings of a bullseye exist ("you are here" / "go here") and they are
  // opposites, so the instrument has to name its own convention. Nothing on
  // this screen did until 2026-09-07.
  render(4, 3);
  const legend = texts().find((t) => /DOT\s*=/.test(t.text));
  assert.ok(legend,
    "the reticle never says what the dot means — the ambiguity that let a " +
    "correctly plotted dot be reported as mirrored");
  assert.ok(/AXIS/.test(legend!.text) && /POLE/.test(legend!.text),
    `the legend reads "${legend!.text}" — it must name BOTH marks, or the reader ` +
    "still has to guess which of the two conventions is in force");
});

test("a POSITIVE altitude error puts the dot ABOVE centre and asks you to lower it", () => {
  /* The axis that was NOT reported wrong, pinned to the SAME convention as
     azimuth: the dot is where the axis IS. +alt means the axis sits above the
     pole (error_det.rs: alt_err = axis_alt − pole_alt), so the dot goes up and
     the instruction is "lower" — you move AWAY from the dot, exactly as a dot
     on the east is corrected by turning west. If one axis is ever re-pointed
     without the other, these two tests disagree. */
  render(0.5, 4);
  assert.ok(dotY() < CY,
    `alt=+4′ drew the dot at y=${dotY()}, below centre (${CY}) — on screen, up is up`);
  const hint = texts().find((t) => /ALT/.test(t.text) && /raise|lower/.test(t.text));
  assert.ok(hint, "no altitude knob hint drawn for a +4′ altitude error");
  assert.ok(/lower/.test(hint!.text) && /▼/.test(hint!.text),
    `alt=+4′ (axis above the pole) is instructed as "${hint!.text}"`);

  render(0.5, -4);
  assert.ok(dotY() > CY, `alt=-4′ drew the dot at y=${dotY()}, above centre (${CY})`);
  const up = texts().find((t) => /ALT/.test(t.text) && /raise|lower/.test(t.text));
  assert.ok(up && /raise/.test(up.text) && /▲/.test(up.text),
    `alt=-4′ (axis below the pole) is instructed as "${up?.text}"`);
});

test("axis east of the pole: dot east, arrow west, words 'turn W'", () => {
  /* The whole chain in one assertion set, at the level the operator reads it.
     A payload whose axis is east of the pole must draw the dot on the side the
     reticle labels east, aim the correction arrow back toward the west half,
     and print "turn W". Any one of the three flipping on its own is the defect
     this file exists for. */
  render(6, 0.5);
  const e = label("AZ E")!;
  assert.ok(dotX() > CX && e.x > CX, "the dot is not on the labelled-east side");
  const v = vector();
  assert.ok(v.head.x < v.tail.x,
    `the correction arrow runs east-ward (tail x=${v.tail.x} → head x=${v.head.x}) ` +
    "for an axis that is already east of the pole");
  const hint = texts().find((t) => /AZ/.test(t.text) && /turn/.test(t.text));
  assert.ok(hint && /turn W/.test(hint.text) && /◀/.test(hint.text),
    `the azimuth instruction for an east axis reads "${hint?.text}"`);
});

test("altitude stays one-sided BECAUSE the top-centre slot is taken", () => {
  /* The azimuth fix had an obvious counterpart — "ALT +" opposite "ALT −" —
     and it is deliberately not there. The bottom label sits at (cx, cy + R − 1),
     textAnchor middle; its mirror would be (cx, cy − R + 1), textAnchor middle,
     which is precisely where the altitude KNOB HINT is drawn. Two strings, same
     anchor, same point, and the hint is present exactly when the label would
     matter. This test fails if that top slot is ever freed up — at which point
     adding the "+" becomes free, or if someone adds it anyway on top of the
     hint. */
  render(4, 3);
  const minus = texts().find((t) => t.text.startsWith("ALT −"));
  assert.ok(minus, "the altitude axis lost its bottom label");
  assert.equal(minus!.anchor, "middle", "the ALT − label is no longer centre-anchored");

  const topCentre = texts().filter((t) => t.anchor === "middle" && t.y < minus!.y - 100);
  assert.equal(topCentre.length, 1,
    `${topCentre.length} centre-anchored labels share the top gutter — an "ALT +" opposite ` +
    'the "ALT −" would land on the altitude knob hint, so there must be exactly one');
  assert.ok(/ALT/.test(topCentre[0].text) && /raise|lower/.test(topCentre[0].text),
    `the top-centre slot holds "${topCentre[0].text}" — the altitude knob hint, which is why ` +
    "the axis label there is omitted rather than stacked");
});

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`polarReticleDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
