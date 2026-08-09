// focusPod.test.tsx — behaviour, contract and copy regression for the #125
// Focus pod (components/focus/FocusPod.tsx) and for the one-line touch-action
// fix on ui/StepDial that shipped with it.
//
//   Run directly:  npx tsx src/components/__tests__/focusPod.test.tsx
//   Also type-checked by `tsc -b` in the build.
//
// Same dependency-free inline-assert harness as SegmentedControl.test.tsx and
// polar.test.ts — no vitest, no jsdom. THERE IS NO LAYOUT HERE, so nothing
// below asserts a rendered pixel: `podPolar` is checked as the pure
// trigonometry it is, and everything else is checked as behaviour (which
// handler fired), contract (which state the ring is in) or copy (the exact
// sentence a blocked chip speaks).
//
// The two things this suite exists to stop regressing, both of which have burnt
// this app before:
//   1. A blocked control that says nothing. Every refusal in the pod must be a
//      SENTENCE rendered through LockedChip, and the native `disabled`
//      attribute must appear nowhere — it deletes the control and its reason
//      from the a11y tree (house rule §11.8).
//   2. A shortcut that grows its own behaviour. Each chip must dispatch the
//      caller's handler, which is FocusView's own panel handler; `podActions`
//      is pure precisely so a spy can prove it.

import { createElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import FocusPod, {
  FocusPodArc, HFR_TREND_EPS, POD_BADGE_R, POD_BOTTOM_PX, POD_CHIP_PX, POD_CHIP_R,
  POD_DISC_PX, POD_MIN_CHIP_R, POD_MIN_STAGE_H, POD_RIGHT_PX,
  hfrTrend, nextInCycle, podActions, podDiscLabel, podHfrRead, podLayout, podPolar,
  podRing, podSlotStyle,
  type PodAction, type PodActionInputs, type FocusPodProps,
} from "../focus/FocusPod";
import StepDial from "../ui/StepDial";

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}
function near(a: number, b: number, tol: number, msg = ""): void {
  if (Math.abs(a - b) > tol) throw new Error(`${msg} — expected ~${b} (±${tol}), got ${a}`);
}

type AnyEl = ReactElement<Record<string, unknown>>;
function isElement(n: unknown): n is AnyEl {
  return typeof n === "object" && n !== null && "props" in (n as object);
}
function collectByRole(node: ReactNode, role: string, out: AnyEl[] = []): AnyEl[] {
  if (Array.isArray(node)) { node.forEach((c) => collectByRole(c, role, out)); return out; }
  if (!isElement(node)) return out;
  if ((node.props as { role?: string }).role === role) out.push(node);
  const kids = (node.props as { children?: ReactNode }).children;
  if (kids != null) collectByRole(kids, role, out);
  return out;
}
function ariaLabel(el: AnyEl): string {
  return String((el.props as { "aria-label"?: string })["aria-label"] ?? "");
}
/** Reach a menu item by what it SAYS rather than by where it sits, so the press
 *  tests keep testing presses when the arc is reordered. */
function itemNamed(node: ReactNode, startsWith: string): AnyEl {
  const hit = collectByRole(node, "menuitem").find((i) => ariaLabel(i).startsWith(startsWith));
  if (!hit) throw new Error(`no menu item whose name starts with "${startsWith}"`);
  return hit;
}

// ======================================================== geometry (pure math)
// Not layout: podPolar is trigonometry with no DOM in it. What is being pinned
// is the CONTRACT the arc is built on — 0 is due left of the disc, 1 is due up,
// and both are inside the only quadrant still over the image.
test("podPolar: fraction 0 is due LEFT of the disc, 1 is due UP", () => {
  const left = podPolar(0, 100);
  eq(left.x, -100, "frac 0 x");
  eq(left.y, 0, "frac 0 y");
  const up = podPolar(1, 100);
  near(up.x, 0, 0.05, "frac 1 x");
  eq(up.y, -100, "frac 1 y");
});

test("podPolar: everything stays in the up-left quadrant (never over the page edge)", () => {
  for (let i = 0; i <= 10; i++) {
    const p = podPolar(i / 10, POD_CHIP_R);
    assert(p.x <= 0.05, `frac ${i / 10} escaped rightwards: x=${p.x}`);
    assert(p.y <= 0.05, `frac ${i / 10} escaped downwards: y=${p.y}`);
    near(Math.hypot(p.x, p.y), POD_CHIP_R, 0.2, `frac ${i / 10} left the radius`);
  }
});

test("podPolar: fractions outside 0..1 clamp instead of wrapping round the disc", () => {
  eq(podPolar(-3, 80).x, podPolar(0, 80).x, "negative clamps to the left end");
  eq(podPolar(9, 80).y, podPolar(1, 80).y, "over 1 clamps to the top end");
});

// The radius is load-bearing, not taste: five 48px chips a quarter turn apart
// must not overlap, because a mistap here is a 1000-step focuser move in the
// dark. Chord = 2·R·sin(Δθ/2), asserted through the real function.
test("chip radius keeps neighbouring 48px chips from overlapping", () => {
  const a = podPolar(0, POD_CHIP_R);
  const b = podPolar(0.25, POD_CHIP_R);
  const gap = Math.hypot(a.x - b.x, a.y - b.y);
  assert(gap > 48, `neighbouring chips are ${gap.toFixed(1)}px apart — 48px chips would overlap`);
});

test("badge radius keeps the two badges clear of each other and of the chips", () => {
  const expo = podPolar(0.5, POD_BADGE_R);
  const stepBadge = podPolar(0.875, POD_BADGE_R);
  assert(
    Math.hypot(expo.x - stepBadge.x, expo.y - stepBadge.y) > 44,
    "the exposure and step badges overlap",
  );
  const shoot = podPolar(0.5, POD_CHIP_R);
  assert(
    Math.hypot(expo.x - shoot.x, expo.y - shoot.y) > 46,
    "the exposure badge overlaps the SHOOT chip it belongs to",
  );
});

// ==================================================== fitting the real stage
// The bug this section exists for: PreviewStage in `compact` is w-full at 3:2
// with no floor, so on a 390px phone — the device the pod exists for — the
// stage is 326x217 and the full arc needs 230. NOTHING between the pod and the
// Panel header clips (.panel sets no overflow), so the `+` and `IN` chips, the
// two pressed most in a focus loop, painted over FocusVerdict: the pod covering
// the readout it exists to mirror.
//
// jsdom has no layout, so the pod measures itself at runtime and podLayout is
// the pure half. That makes this arithmetic, not geometry, and testable here.

/** Every chip corner, in stage coordinates, for a stage of this size. */
function chipEscape(stage: { w: number; h: number }): string | null {
  const { radius } = podLayout(stage);
  const cx = stage.w - (POD_RIGHT_PX + POD_DISC_PX / 2);
  const cy = stage.h - (POD_BOTTOM_PX + POD_DISC_PX / 2);
  const half = POD_CHIP_PX / 2;
  for (let i = 0; i <= 4; i++) {
    const p = podPolar(i / 4, radius);
    const x = cx + p.x;
    const y = cy + p.y;
    if (y - half < 0) return `chip ${i} is ${(half - y).toFixed(0)}px above the stage`;
    if (x - half < 0) return `chip ${i} is ${(half - x).toFixed(0)}px left of the stage`;
    if (x + half > stage.w || y + half > stage.h) return `chip ${i} escaped bottom/right`;
  }
  return null;
}

test("POD_MIN_STAGE_H is exactly the height the full arc needs, derived not chosen", () => {
  eq(POD_MIN_STAGE_H, 230, "38 bottom + 28 half-disc + 140 radius + 24 half-chip");
  eq(podLayout({ w: 1000, h: POD_MIN_STAGE_H }).radius, POD_CHIP_R,
    "the floor must buy the FULL arc, not almost");
  assert(podLayout({ w: 1000, h: POD_MIN_STAGE_H - 1 }).radius < POD_CHIP_R,
    "one pixel under the floor must already clamp — otherwise the constant is slack");
});

test("a 3:2 compact stage on a phone is SHORTER than the arc — the defect being fixed", () => {
  // 390px viewport: main p-4 + .panel p-4 leaves 326px, and 3:2 makes that 217.
  const bare = { w: 326, h: Math.round((326 * 2) / 3) };
  eq(bare.h, 217, "the untouched compact stage on a 390px phone");
  assert(bare.h < POD_MIN_STAGE_H, "if this ever stops being true the floor can go");
  assert(podLayout(bare).radius < POD_CHIP_R, "the arc must clamp rather than overhang");
  eq(chipEscape(bare), null, "…and even clamped, no chip may leave the stage");
});

test("with FocusView's floor applied, every phone width gets the full arc and nothing escapes", () => {
  for (const vw of [320, 360, 390, 414, 430]) {
    // main p-4 (32) + .panel p-4 (32) = 64px of chrome, then the floor.
    const w = vw - 64;
    const stage = { w, h: Math.max(Math.round((w * 2) / 3), POD_MIN_STAGE_H) };
    const layout = podLayout(stage);
    eq(layout.radius, POD_CHIP_R, `${vw}px: the floored stage must hold the full arc`);
    assert(layout.fits, `${vw}px: the pod must be usable`);
    eq(chipEscape(stage), null, `${vw}px: ${chipEscape(stage)}`);
  }
});

test("a stage too small for a usable arc reports it rather than crowding the chips", () => {
  // Five 48px chips a quarter turn apart are 0.39·R from each other, so under
  // POD_MIN_CHIP_R they overlap — and a mistap here is a 1000-step focuser move.
  const tiny = { w: 256, h: 171 };            // 320px phone with no floor at all
  assert(!podLayout(tiny).fits, "the pod must stand down instead of overlapping itself");
  const gap = (r: number) => {
    const a = podPolar(0, r);
    const b = podPolar(0.25, r);
    return Math.hypot(a.x - b.x, a.y - b.y);
  };
  assert(gap(POD_MIN_CHIP_R) >= POD_CHIP_PX,
    `at the floor radius neighbouring chips are ${gap(POD_MIN_CHIP_R).toFixed(1)}px apart`);
  // The closed form rather than podPolar for this direction: podPolar rounds to
  // 0.1px, which is enough to flatter a radius that is genuinely a hair short.
  assert(2 * (POD_MIN_CHIP_R - 1) * Math.sin(Math.PI / 16) < POD_CHIP_PX,
    "the floor is slack — it must be the SMALLEST radius that still clears");
});

test("the badges and the scrim shrink WITH the arc, not independently of it", () => {
  const full = podLayout({ w: 1000, h: 1000 });
  eq(full.badgeRadius, POD_BADGE_R, "an unconstrained stage keeps the designed badge ring");
  eq(full.scrim, 2 * (POD_CHIP_R + 44), "…and the designed scrim");
  const clamped = podLayout({ w: 326, h: 217 });
  assert(clamped.badgeRadius < POD_BADGE_R, "badges must not stay put while the chips move in");
  assert(clamped.scrim < full.scrim,
    "a clamped arc inside a full-size pool of black is a scrim describing an arc that is not there");
  assert(clamped.badgeRadius > POD_DISC_PX / 2 + 22,
    "…and they must not close in far enough to sit on the disc");
});

test("an unmeasured stage assumes the full arc, so the first paint is not a flinch", () => {
  // The arc is closed on the first frame anyway; measuring corrects it before
  // anything blooms. Rendering the pod as `does not fit` until measured would
  // pop the whole control in a frame late on every mount.
  eq(podLayout(null).radius, POD_CHIP_R);
  assert(podLayout(null).fits, "unmeasured must not read as unusable");
});

// ========================================================== cycling badges
test("nextInCycle walks the exposure presets and wraps at the top", () => {
  const presets = [1, 2, 3, 5, 10];
  eq(nextInCycle(presets, 1), 2, "1 -> 2");
  eq(nextInCycle(presets, 3), 5, "3 -> 5");
  eq(nextInCycle(presets, 10), 1, "the top wraps to the bottom");
});

test("nextInCycle steps UP from a typed off-preset value instead of snapping", () => {
  // 4s is the exposure that actually worked at the scope on 2026-07-31 and is
  // not a preset. A badge that snapped it to 1 would silently undo the one
  // setting the user had reason to type.
  eq(nextInCycle([1, 2, 3, 5, 10], 4), 5, "4 -> 5");
  eq(nextInCycle([1, 2, 3, 5, 10], 0.5), 1, "below the bottom -> the bottom");
  eq(nextInCycle([1, 2, 3, 5, 10], 99), 1, "above the top -> wraps");
});

test("nextInCycle starts the cycle when the exposure box is empty", () => {
  // null is an empty / unparseable box, which is itself a capture blocker —
  // so one tap of the badge is also the repair for that blocker.
  eq(nextInCycle([1, 2, 3, 5, 10], null), 1, "null -> first preset");
});

test("nextInCycle drives the step magnitudes too", () => {
  const steps = [1, 10, 100, 1000];
  eq(nextInCycle(steps, 1), 10, "1 -> 10");
  eq(nextInCycle(steps, 100), 1000, "100 -> 1000");
  eq(nextInCycle(steps, 1000), 1, "1000 wraps to 1");
});

// =============================================================== HFR trend
test("hfrTrend: smaller HFR is SHARPER, larger is softer", () => {
  eq(hfrTrend(3.0, 4.0), "sharper", "3.0 after 4.0");
  eq(hfrTrend(4.0, 3.0), "softer", "4.0 after 3.0");
});

test("hfrTrend: the epsilon holds from both sides so the caret does not flicker", () => {
  eq(hfrTrend(4.0 - HFR_TREND_EPS, 4.0), "flat", "exactly one epsilon down is still flat");
  eq(hfrTrend(4.0 + HFR_TREND_EPS, 4.0), "flat", "exactly one epsilon up is still flat");
  eq(hfrTrend(4.0 - HFR_TREND_EPS * 2, 4.0), "sharper", "two epsilons down is sharper");
  eq(hfrTrend(4.0 + HFR_TREND_EPS * 2, 4.0), "softer", "two epsilons up is softer");
});

test("hfrTrend: no previous frame means no claim about direction", () => {
  eq(hfrTrend(4.0, null), "flat", "first frame of the night");
  eq(hfrTrend(null, 4.0), "flat", "no current measurement");
});

// =============================================== when the number is a ceiling
// The trap: detect_stars measures inside a 15px box, so a 440px donut still
// reports "HFR 4.6". FocusVerdict DROPS the number in that state on purpose
// (FocusVerdict.tsx:108-129) — printing "FAIR" there tells the user to stop
// adjusting. The pod renders ~40px below that line over the same frame.
test("podHfrRead: a frame past the measurement box prints no number at all", () => {
  for (const state of ["defocused", "soft"] as const) {
    const read = podHfrRead({ hfr: 4.62, state });
    eq(read.value, null, `${state} must not print the box's ceiling as a measurement`);
    eq(read.unmeasured, "too far out of focus to measure", `${state} must say why`);
  }
});

test("podHfrRead: too few stars is its OWN sentence, not the defocus one", () => {
  // Different fact, different repair — check clouds or lengthen the exposure,
  // which is not what "turn the focuser" would tell you to do.
  const read = podHfrRead({ hfr: 2.1, state: "few-stars" });
  eq(read.value, null);
  eq(read.unmeasured, "too few stars to measure");
});

test("podHfrRead: a measured frame is passed straight through, and so is an empty one", () => {
  eq(podHfrRead({ hfr: 2.14, state: "measured" }).value, 2.14);
  eq(podHfrRead({ hfr: 2.14, state: "measured" }).unmeasured, null);
  const none = podHfrRead({ hfr: null, state: "no-frame" });
  eq(none.value, null, "no frame, no number");
  eq(none.unmeasured, null, "…but 'no frame yet' is not 'cannot be measured'");
});

test("an untrusted frame silences the TREND CARET as well as the number", () => {
  // Two saturated readings differ by measurement noise, so a caret between them
  // is a direction invented out of nothing — constant-where-it-should-vary,
  // read as progress. hfrTrend of a null current value is the mechanism.
  const read = podHfrRead({ hfr: 4.62, state: "defocused" });
  eq(hfrTrend(read.value, 4.90), "flat",
    "the pod must not claim 'sharper' from one ceiling to the next");
});

// ================================================================= the ring
const RING_BASE = {
  progress: null, note: null, tone: null, looping: false, starting: false, captureBlocked: null,
} as const;
const RING_CASES = [
  ["idle", podRing({ ...RING_BASE })],
  ["starting", podRing({ ...RING_BASE, starting: true })],
  ["exposing", podRing({ ...RING_BASE, progress: 0.4 })],
  ["stalled", podRing({ ...RING_BASE, progress: 1, tone: "warn", note: "no frame in 74s" })],
  ["looping", podRing({ ...RING_BASE, looping: true })],
  ["blocked", podRing({ ...RING_BASE, captureBlocked: "No camera" })],
] as const;

test("ring: every state is distinguishable by WEIGHT and DASH alone (no hue)", () => {
  // The house rule this pins: in night mode --accent, --good, --warn and --bad
  // all resolve to reds, so a ring that told "exposing" from "looping" by
  // colour tells the user nothing at the eyepiece.
  const seen = new Set<string>();
  for (const [name, r] of RING_CASES) {
    eq(r.kind, name, `podRing returned the wrong kind for ${name}`);
    const sig = `${r.width}|${r.dash}|${r.arc < 1 ? "partial" : "closed"}`;
    assert(!seen.has(sig), `${name} is indistinguishable from another state without colour (${sig})`);
    seen.add(sig);
  }
  eq(seen.size, RING_CASES.length, "expected one signature per state");
});

test("ring: a frame in flight outranks every other state", () => {
  const r = podRing({
    ...RING_BASE, progress: 0.25, note: "exposing · 3s left", looping: true, starting: true,
    captureBlocked: "A sequence owns the camera — stop it first",
  });
  eq(r.kind, "exposing", "a real in-flight frame beats a blocker about the NEXT tap");
  eq(r.arc, 0.25, "the arc is the progress fraction");
});

test("ring: the exposing state speaks the narrator's own sentence, not a second one", () => {
  // Two countdowns 18px apart that disagree is the failure this reuse prevents.
  const r = podRing({ ...RING_BASE, progress: 0.5, note: "exposing · 2s left" });
  eq(r.label, "exposing · 2s left", "the ring must reuse frameWaitNote's text");
});

test("ring: a DROPPED frame does not draw like a finished one", () => {
  // frameWaitNote flips to warn at exposure + 60s, but `progress` is clamped at
  // 1, so without a branch of its own the ring drew "the camera may have dropped
  // it" as a full solid accent circle — pixel-identical to a clean exposure.
  const dropped = podRing({
    ...RING_BASE, progress: 1, tone: "warn",
    note: "no frame in 74s for a 4s exposure — the camera may have dropped it",
  });
  const done = podRing({ ...RING_BASE, progress: 1, tone: "info", note: "reading out…" });
  eq(dropped.kind, "stalled");
  assert(`${dropped.width}|${dropped.dash}` !== `${done.width}|${done.dash}`,
    "a fault must be legible without hue — weight and dash are the channel");
  assert(dropped.label.includes("74s"), "and it speaks the narrator's own sentence");
});

test("ring: progress is clamped, so a late tick cannot draw more than a full circle", () => {
  eq(podRing({ ...RING_BASE, progress: 1.8 }).arc, 1);
  eq(podRing({ ...RING_BASE, progress: -0.4 }).arc, 0);
});

test("ring: blocked speaks the blocker's SENTENCE, never a boolean", () => {
  const reason = "Autofocus owns the camera until the sweep finishes";
  const r = podRing({ ...RING_BASE, captureBlocked: reason });
  eq(r.kind, "blocked");
  eq(r.label, reason, "the ring's label IS the blocker sentence");
});

// ============================================================== the chips
function spies() {
  const log: string[] = [];
  const base: PodActionInputs = {
    looping: false, starting: null, exposing: false, step: 100,
    shootReason: null, loopReason: null, stopReason: null,
    focuserReason: null, autofocusReason: null,
    onShoot: () => log.push("shoot"),
    onLoop: () => log.push("loop"),
    onStop: () => log.push("stop"),
    onNudge: (d) => log.push(`nudge:${d}`),
    onAutofocus: () => log.push("af"),
  };
  return { log, base };
}
function byId(list: PodAction[], id: string): PodAction {
  const a = list.find((x) => x.id === id);
  if (!a) throw new Error(`no chip with id ${id}`);
  return a;
}

test("podActions: five chips, in arc order, with the nudges nearest the thumb", () => {
  const { base } = spies();
  eq(podActions(base).map((a) => a.id).join(","), "af,loop,shoot,in,out",
    "arc order is a reach decision — +step must stay at the top end, AF at the far end");
});

test("podActions: a live chip dispatches the CALLER's handler, nothing of its own", () => {
  const { log, base } = spies();
  const list = podActions(base);
  byId(list, "shoot").press();
  byId(list, "loop").press();
  byId(list, "af").press();
  eq(log.join(","), "shoot,loop,af", "the pod must fire the panel's own handlers");
});

test("podActions: the nudges carry the sign AND the current step magnitude", () => {
  const { log, base } = spies();
  const list = podActions({ ...base, step: 1000 });
  byId(list, "out").press();
  byId(list, "in").press();
  eq(log.join(","), "nudge:1000,nudge:-1000", "+ is out/positive, − is negative, both at the live step");
  eq(byId(list, "out").face, "+", "the + face");
  eq(byId(list, "in").face, "−", "the − face");
});

test("podActions: LOOP becomes STOP while a loop runs, and fires the rail's Stop", () => {
  const { log, base } = spies();
  const list = podActions({ ...base, looping: true });
  const loop = byId(list, "loop");
  eq(loop.face, "STOP", "a pod that can start a loop but not stop it forces the scroll it removes");
  loop.press();
  eq(log.join(","), "stop", "STOP dispatches the rail's own stopCapture");
});

test("podActions: a blocked chip carries the blocker's exact SENTENCE", () => {
  const { base } = spies();
  const shootReason = "A capture loop is running — press Stop first";
  const focuserReason = "No focuser is connected — connect one on the Equipment page";
  const afReason = "Read-only — focusing needs operator access";
  const list = podActions({ ...base, shootReason, focuserReason, autofocusReason: afReason });
  eq(byId(list, "shoot").reason, shootReason, "singleReason must arrive verbatim");
  eq(byId(list, "in").reason, focuserReason);
  eq(byId(list, "out").reason, focuserReason);
  eq(byId(list, "af").reason, afReason);
  eq(byId(list, "loop").reason, null, "Loop has its own, shorter blocker chain");
});

test("podActions: `hint` is what the chip DOES, and never doubles as the refusal", () => {
  // A blocked chip is a LockedChip, whose accessible name is Tooltip's
  // "Unavailable — <reason>"; `hint` is read only on the live branch. Folding
  // the reason in here produced a sentence no screen reader could ever reach —
  // and a test asserting it was asserting nothing about what is spoken.
  const { base } = spies();
  const focuserReason = "Autofocus is running — let the sweep finish first";
  const live = podActions({ ...base, step: 10 });
  eq(byId(live, "in").hint, "Move focuser -10 steps", "the rail's own nudge phrasing, verbatim");
  const blocked = podActions({ ...base, step: 10, focuserReason });
  eq(byId(blocked, "in").hint, byId(live, "in").hint,
    "a blocker must not rewrite the description of what the chip does");
  eq(byId(blocked, "in").reason, focuserReason, "the refusal rides in `reason`, which IS spoken");
});

test("podActions: an in-flight frame refuses SHOOT with a sentence, not a dead button", () => {
  const { base } = spies();
  eq(
    podActions({ ...base, exposing: true }).find((a) => a.id === "shoot")?.reason,
    "A frame is already exposing — press Stop to abandon it",
  );
  eq(
    podActions({ ...base, starting: "single" }).find((a) => a.id === "shoot")?.reason,
    "Waiting for the camera to accept this exposure — no frame has started yet",
  );
  eq(
    podActions({ ...base, starting: "loop" }).find((a) => a.id === "loop")?.reason,
    "Waiting for the camera to accept this exposure — no frame has started yet",
  );
});

test("podActions: the rig blocker outranks the in-flight one", () => {
  const { base } = spies();
  const shootReason = "No camera is connected — connect one on the Equipment page";
  eq(
    podActions({ ...base, exposing: true, shootReason }).find((a) => a.id === "shoot")?.reason,
    shootReason,
    "a hardware fact is bigger news than a frame that is already open",
  );
});

// ============================================================== disc copy
test("podDiscLabel names the number and its direction, not the widget", () => {
  const label = podDiscLabel({
    open: false, hfr: 4.25, unmeasured: null, trend: "sharper", ringLabel: "exposing · 2s left",
  });
  assert(label.includes("HFR 4.25"), `no HFR in "${label}"`);
  assert(label.includes("sharper than the previous frame"), `no trend in "${label}"`);
  assert(label.includes("exposing · 2s left"), `no ring state in "${label}"`);
});

test("podDiscLabel says there is no measurement rather than inventing one", () => {
  const label = podDiscLabel({
    open: false, hfr: null, unmeasured: null, trend: "flat", ringLabel: "no exposure in flight",
  });
  assert(label.includes("no frame measured yet"), `expected the empty case, got "${label}"`);
  assert(!/HFR \d/.test(label), `a number appeared with no frame: "${label}"`);
});

test("podDiscLabel speaks the ceiling as a ceiling, never as an HFR", () => {
  // The pairing this pins: the header says "Far out of focus — <instruction>"
  // and prints no number; the disc must not answer the same frame with
  // "HFR 4.62, sharper than the previous frame".
  const label = podDiscLabel({
    open: false, hfr: 4.62, unmeasured: "too far out of focus to measure",
    trend: "flat", ringLabel: "no exposure in flight",
  });
  assert(label.includes("too far out of focus to measure"), `expected the refusal, got "${label}"`);
  assert(!/HFR/.test(label), `the box's ceiling was spoken as a measurement: "${label}"`);
  assert(!label.includes("4.62"), `the number survived: "${label}"`);
});

test("podDiscLabel flips to the close action when the arc is open", () => {
  eq(podDiscLabel({ open: true, hfr: 4.25, unmeasured: null, trend: "sharper", ringLabel: "idle" }),
    "Close the focus controls");
});

// ------------------------------------------------------------ slot placement
test("podSlotStyle: the real builder sets touch-action on every slot it makes", () => {
  // Asserted against the COMPONENT's function, not a stub: the arc's positioned
  // wrappers are the only place this reaches, and a test that supplies its own
  // slot() is testing its own stub.
  for (const frac of [0, 0.25, 0.5, 0.875, 1]) {
    const s = podSlotStyle({ frac, radius: POD_CHIP_R, bloom: true, reduced: false });
    eq(s.touchAction, "none", `frac ${frac} would let the page scroll under the thumb`);
    eq(s.position, "absolute", `frac ${frac} must be placed, not flowed`);
  }
});

test("podSlotStyle: the stagger follows the arc, so a chip and its badge fly together", () => {
  const at = (frac: number) =>
    podSlotStyle({ frac, radius: 100, bloom: true, reduced: false }).transitionDelay;
  eq(at(0.5), at(0.5), "same fraction, same delay");
  assert(at(0) !== at(1), "the ends of the arc must not arrive at the same instant");
});

test("podSlotStyle: reduced motion keeps the LAYOUT and drops only the flight", () => {
  const moved = podSlotStyle({ frac: 1, radius: 100, bloom: false, reduced: true });
  const flying = podSlotStyle({ frac: 1, radius: 100, bloom: false, reduced: false });
  assert(String(moved.transform).includes("-100px"),
    "a stilled animation must not cost the position it was carrying");
  assert(String(flying.transform).includes("scale(0.7)"), "…while the bloom still starts small");
  eq(moved.transitionDelay, "0ms", "no stagger when motion is reduced");
});

// ============================================================ rendered pod
const POD_PROPS: FocusPodProps = {
  hfr: 4.25, prevHfr: 4.9, hfrState: "measured",
  exposureProgress: null, exposureNote: null, exposureNoteTone: null, captureBlocked: null,
  stepValues: [1, 10, 100, 1000],
  onStep: () => {},
  looping: false, starting: null, exposing: false, step: 100,
  shootReason: null, loopReason: null, stopReason: null,
  focuserReason: null, autofocusReason: null,
  onShoot: () => {}, onLoop: () => {}, onStop: () => {}, onNudge: () => {}, onAutofocus: () => {},
};

test("collapsed pod: the disc carries the HFR so the shortcut is worth its 56px unopened", () => {
  const html = renderToStaticMarkup(createElement(FocusPod, POD_PROPS));
  assert(html.includes("4.25"), "the disc must show the live HFR");
  assert(/aria-expanded="false"/.test(html), "the disc must be a collapsed menu button");
  assert(/aria-haspopup="menu"/.test(html), "the disc must advertise the menu it opens");
  assert(!html.includes('role="menu"'), "the arc must not be in the DOM while collapsed");
});

test("collapsed pod: no frame means an em dash, never a stale or invented number", () => {
  const html = renderToStaticMarkup(createElement(FocusPod, {
    ...POD_PROPS, hfr: null, prevHfr: null, hfrState: "no-frame",
  }));
  assert(html.includes("—"), "expected the empty marker");
  assert(!html.includes("4.25"), "a number survived a frameless pod");
});

test("collapsed pod: a frame past the measurement box shows NO number and no caret", () => {
  // The whole finding, rendered: header says "Far out of focus" and prints
  // nothing; the disc 40px below said "4.62 ▼", two saturated readings apart.
  const html = renderToStaticMarkup(createElement(FocusPod, {
    ...POD_PROPS, hfr: 4.62, prevHfr: 4.9, hfrState: "defocused",
  }));
  assert(!html.includes("4.62"), "the box's ceiling was printed as a measurement");
  assert(html.includes("—"), "expected the em dash where the number would be");
  assert(html.includes("too far out of focus to measure"),
    "the disc must say WHY there is no number");
  assert(!html.includes("data-pod-trend"),
    "a trend caret between two ceilings is direction invented from noise");
  assert(!html.includes("sharper than the previous frame"), "…and so is speaking it");
  // …and the marker is real: the same pod on a MEASURED frame does draw it.
  const measured = renderToStaticMarkup(createElement(FocusPod, {
    ...POD_PROPS, hfr: 4.62, prevHfr: 4.9, hfrState: "measured",
  }));
  assert(measured.includes('data-pod-trend="sharper"'),
    "the caret vanished for a frame that CAN be measured — the gate is too wide");
});

test("collapsed pod: few stars gets its own sentence, still with no number", () => {
  const html = renderToStaticMarkup(createElement(FocusPod, {
    ...POD_PROPS, hfr: 2.1, hfrState: "few-stars",
  }));
  assert(!html.includes("2.1"), "an HFR from three stars is not a measurement of the frame");
  assert(html.includes("too few stars to measure"), "…and the disc must say which problem it is");
});

test("pod root sets touch-action:none AND stays pointer-transparent while closed", () => {
  const html = renderToStaticMarkup(createElement(FocusPod, POD_PROPS));
  assert(/touch-action:none/.test(html), "touch-action must be inline — a sibling overlay defaults to auto");
  assert(html.includes("pointer-events-none"), "a closed pod must not eat a pan starting in that corner");
  assert(html.includes(`width:${POD_DISC_PX}px`), "the disc keeps its 56px hero size");
  assert(html.includes("overflow-hidden"),
    "nothing the pod draws may leave the stage — above it is the verdict line it mirrors");
});

// ------------------------------------------------------------- the open arc
// FocusPodArc is hook-free precisely so it can be rendered here without a DOM.
// The slot builder is the COMPONENT's own (podSlotStyle), not a stub: the arc's
// positioned wrappers are the only place touch-action reaches them, so a stub
// here would make every "the slots set touch-action" assertion unfalsifiable.
type ArcProps = Parameters<typeof FocusPodArc>[0];
function arcProps(over: Partial<ArcProps> = {}, actionOver: Partial<PodActionInputs> = {}): ArcProps {
  const { base } = spies();
  return {
    actions: podActions({ ...base, ...actionOver }),
    step: 100, nextStep: 1000,
    onStep: () => {},
    slot: (frac: number, radius: number) =>
      podSlotStyle({ frac, radius, bloom: true, reduced: false }),
    onPress: () => {},
    ...over,
  };
}
const arcEl = (over: Partial<ArcProps> = {}, actionOver: Partial<PodActionInputs> = {}) =>
  createElement(FocusPodArc, arcProps(over, actionOver));
/** The element TREE (the component called as the pure function it is), for
 *  assertions about order and handlers rather than about markup. */
const arcTree = (over: Partial<ArcProps> = {}, actionOver: Partial<PodActionInputs> = {}) =>
  FocusPodArc(arcProps(over, actionOver));

test("open arc: a labelled role=menu holding five chips and ONE cycling badge", () => {
  const html = renderToStaticMarkup(arcEl());
  assert(/role="menu"/.test(html), "the arc must be a menu");
  assert(/aria-label="Focus quick controls"/.test(html), "the menu needs a name");
  eq((html.match(/role="menuitem"/g) ?? []).length, 6, "5 chips + 1 badge");
  ["AF", "LOOP", "SHOOT"].forEach((f) => assert(html.includes(f), `chip ${f} missing`));
  assert(html.includes("100"), "the step badge must show the magnitude in force");
});

// -------------------------------------------------------------------- #180
// THE POD CARRIES NO CAMERA SETTING. It used to hold a cycling exposure badge
// while the CameraDial in the opposite corner of the same stage held an
// exposure ring — two radial controls over one image, both showing a bare
// "Ns", editing two different values (the shutter's, and the sweep's). This is
// the guard for the split: actions right, settings left.
test("open arc: no exposure anywhere on the actions pod (#180)", () => {
  const html = renderToStaticMarkup(arcEl());
  assert(!html.includes('data-pod-badge="exposure"'),
    "the exposure badge is back on the actions pod — the stage now has two "
    + "radial controls carrying an exposure again");
  assert(!/aria-label="Exposure/.test(html),
    "a control on the actions pod names an exposure");
  // …and the badge that DOES belong here is still here, so this is not passing
  // on an empty arc.
  assert(html.includes('data-pod-badge="step"'), "the step badge went with it");
});

test("open arc: NO native `disabled` anywhere, blocked or not (house rule §11.8)", () => {
  const html = renderToStaticMarkup(arcEl({}, {
    shootReason: "No camera is connected — connect one on the Equipment page",
    focuserReason: "Autofocus is running — let the sweep finish first",
  }));
  assert(!/\sdisabled(=|>|\s)/.test(html),
    "a native disabled attribute appeared — it deletes the control AND its reason from the a11y tree");
  assert(/aria-disabled="true"/.test(html), "a blocked chip must still be aria-disabled and focusable");
});

test("open arc: a blocked chip renders the SENTENCE, not a boolean or a shrug", () => {
  const reason = "A sequence owns the camera — stop it first";
  const html = renderToStaticMarkup(arcEl({}, { shootReason: reason }));
  assert(html.includes(reason), `the blocker sentence is missing from the markup: ${reason}`);
  assert(html.includes(`Unavailable — ${reason}`), "LockedChip's spoken name must carry the reason");
});

test("open arc: every surface the ARC owns sets touch-action:none", () => {
  const html = renderToStaticMarkup(arcEl());
  const slots = (html.match(/data-pod-slot/g) ?? []).length;
  eq(slots, 6, "one positioned slot per menu item");
  // 6 slots + 5 chip buttons + 1 badge button + the menu root. Counted rather
  // than merely `>= slots` so that stripping it from the chip boxes — the
  // surface a thumb actually presses and slides on — fails this.
  eq((html.match(/touch-action:none/g) ?? []).length, 13,
    "a press that starts on a chip and slides must drive the control, not scroll the page");
});

test("open arc: DOM order IS arc order, so the arrow keys walk what the eye sees", () => {
  // The roving index (FocusPod.onArcKey) walks the DOM. Emitting five chips and
  // then the badges put the exposure badge — which rides at SHOOT's angle, in
  // the middle of the arc — after the last chip, so ArrowRight from the top of
  // the arc threw focus back down into its middle.
  const names = collectByRole(arcTree(), "menuitem").map((i) => ariaLabel(i).split(" —")[0]);
  eq(names.join(" | "), [
    "Run an autofocus sweep",          // frac 0    — due left
    "Loop frames at this exposure until you stop", // 0.25
    "Take one focus frame",            // 0.5
    "Move focuser -100 steps",         // 0.75
    "Focuser step 100",                // 0.875, between the nudges
    "Move focuser +100 steps",         // 1     — due up
  ].join(" | "), "arc order and DOM order have drifted apart");
});

test("open arc: the step badge names the value it will move to", () => {
  const html = renderToStaticMarkup(arcEl({ step: 10, nextStep: 100 }));
  assert(html.includes("Focuser step 10 — tap for 100"),
    "a cycling badge has to say where the next tap lands");
});

// ------------------------------------------------- press paths (element tree)
test("open arc: pressing a live chip fires its handler and then closes the arc", () => {
  const { log, base } = spies();
  const closes: number[] = [];
  const el = FocusPodArc({
    actions: podActions(base),
    step: 100, nextStep: 1000,
    onStep: () => {},
    slot: () => ({}), onPress: () => closes.push(1),
  });
  eq(collectByRole(el, "menuitem").length, 6, "5 chips + 1 badge");
  (itemNamed(el, "Take one focus frame").props.onClick as () => void)();
  eq(log.join(","), "shoot", "the chip fired FocusView's shoot()");
  eq(closes.length, 1, "an action closes the arc behind itself");
});

test("open arc: the badge cycles WITHOUT closing — three taps must not cost three reopens", () => {
  const steps: number[] = [];
  const closes: number[] = [];
  const { base } = spies();
  const el = FocusPodArc({
    actions: podActions(base),
    step: 100, nextStep: 1000,
    onStep: (v) => steps.push(v),
    slot: () => ({}), onPress: () => closes.push(1),
  });
  (itemNamed(el, "Focuser step 100").props.onClick as () => void)();
  eq(steps.join(","), "1000", "the step badge dispatches the next magnitude");
  eq(closes.length, 0, "cycling must leave the arc open");
});

test("open arc: a blocked chip is not a menuitem button — it is the locked stand-in", () => {
  const { base } = spies();
  const el = FocusPodArc({
    actions: podActions({ ...base, focuserReason: "No focuser is connected" }),
    step: 100, nextStep: 1000,
    onStep: () => {}, slot: () => ({}), onPress: () => {},
  });
  eq(collectByRole(el, "menuitem").length, 4, "the two blocked nudges left the pressable set");
});

test("open arc: the clamped radius reaches the slots, not just the layout maths", () => {
  // podLayout can compute all it likes; if the arc keeps drawing at 140 the
  // chips still land on the Panel header.
  const placed: { frac: number; radius: number }[] = [];
  renderToStaticMarkup(arcEl({
    radius: 120, badgeRadius: 73,
    slot: (frac: number, radius: number) => { placed.push({ frac, radius }); return {}; },
  }));
  eq(placed.length, 6, "one placement per item");
  assert(placed.every((p) => p.radius === 120 || p.radius === 73),
    `a slot ignored the clamped radii: ${JSON.stringify(placed)}`);
  eq(placed.filter((p) => p.radius === 73).length, 1, "the badge rides the inner ring");
});

// ==================================================== StepDial touch-action
// The same defect as the pod's, on the control the pod's step badge mirrors.
// StepDial's designed gesture is press-and-slide UP; without touch-action the
// browser reads that as a page scroll, the page wins, and the release commits
// whatever the finger happened to be over.
test("StepDial's root sets touch-action:none so its slide is not a page scroll", () => {
  const html = renderToStaticMarkup(createElement(StepDial, {
    values: [1, 10, 100, 1000], value: 100, onChange: () => {}, ariaLabel: "Focuser step size",
  }));
  assert(/touch-action:none/.test(html), "StepDial's root must set touch-action inline");
  assert(/role="spinbutton"/.test(html), "the dial is still the same control");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`\nfocusPod.test: ${passed}/${total} passed`);
if (failures.length) {
  console.error(failures.join("\n"));
  (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1);
}
export const result = { passed, failed, total };
