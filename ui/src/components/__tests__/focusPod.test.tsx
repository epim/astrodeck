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
  FocusPodArc, HFR_TREND_EPS, POD_BADGE_R, POD_CHIP_R, POD_DISC_PX,
  hfrTrend, nextInCycle, podActions, podDiscLabel, podPolar, podRing,
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

// ================================================================= the ring
const RING_CASES = [
  ["idle", podRing({ progress: null, note: null, looping: false, starting: false, captureBlocked: null })],
  ["starting", podRing({ progress: null, note: null, looping: false, starting: true, captureBlocked: null })],
  ["exposing", podRing({ progress: 0.4, note: null, looping: false, starting: false, captureBlocked: null })],
  ["looping", podRing({ progress: null, note: null, looping: true, starting: false, captureBlocked: null })],
  ["blocked", podRing({ progress: null, note: null, looping: false, starting: false, captureBlocked: "No camera" })],
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
    progress: 0.25, note: "exposing · 3s left", looping: true, starting: true,
    captureBlocked: "A sequence owns the camera — stop it first",
  });
  eq(r.kind, "exposing", "a real in-flight frame beats a blocker about the NEXT tap");
  eq(r.arc, 0.25, "the arc is the progress fraction");
});

test("ring: the exposing state speaks the narrator's own sentence, not a second one", () => {
  // Two countdowns 18px apart that disagree is the failure this reuse prevents.
  const r = podRing({ progress: 0.5, note: "exposing · 2s left", looping: false, starting: false, captureBlocked: null });
  eq(r.label, "exposing · 2s left", "the ring must reuse frameWaitNote's text");
});

test("ring: progress is clamped, so a late tick cannot draw more than a full circle", () => {
  eq(podRing({ progress: 1.8, note: null, looping: false, starting: false, captureBlocked: null }).arc, 1);
  eq(podRing({ progress: -0.4, note: null, looping: false, starting: false, captureBlocked: null }).arc, 0);
});

test("ring: blocked speaks the blocker's SENTENCE, never a boolean", () => {
  const reason = "Autofocus owns the camera until the sweep finishes";
  const r = podRing({ progress: null, note: null, looping: false, starting: false, captureBlocked: reason });
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

test("podActions: a blocked nudge's spoken hint names the reason too", () => {
  const { base } = spies();
  const focuserReason = "Autofocus is running — let the sweep finish first";
  const list = podActions({ ...base, step: 10, focuserReason });
  eq(byId(list, "in").hint, `Move focuser -10 steps — ${focuserReason}`,
    "the rail's own nudge phrasing, plus the reason");
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
    open: false, hfr: 4.25, trend: "sharper", ringLabel: "exposing · 2s left",
  });
  assert(label.includes("HFR 4.25"), `no HFR in "${label}"`);
  assert(label.includes("sharper than the previous frame"), `no trend in "${label}"`);
  assert(label.includes("exposing · 2s left"), `no ring state in "${label}"`);
});

test("podDiscLabel says there is no measurement rather than inventing one", () => {
  const label = podDiscLabel({ open: false, hfr: null, trend: "flat", ringLabel: "no exposure in flight" });
  assert(label.includes("no frame measured yet"), `expected the empty case, got "${label}"`);
  assert(!/HFR \d/.test(label), `a number appeared with no frame: "${label}"`);
});

test("podDiscLabel flips to the close action when the arc is open", () => {
  eq(podDiscLabel({ open: true, hfr: 4.25, trend: "sharper", ringLabel: "idle" }),
    "Close the focus controls");
});

// ============================================================ rendered pod
const POD_PROPS: FocusPodProps = {
  hfr: 4.25, prevHfr: 4.9,
  exposureProgress: null, exposureNote: null, captureBlocked: null,
  exposureS: 3, exposurePresets: [1, 2, 3, 5, 10], stepValues: [1, 10, 100, 1000],
  onExposure: () => {}, onStep: () => {},
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
  const html = renderToStaticMarkup(createElement(FocusPod, { ...POD_PROPS, hfr: null, prevHfr: null }));
  assert(html.includes("—"), "expected the empty marker");
  assert(!html.includes("4.25"), "a number survived a frameless pod");
});

test("pod root sets touch-action:none AND stays pointer-transparent while closed", () => {
  const html = renderToStaticMarkup(createElement(FocusPod, POD_PROPS));
  assert(/touch-action:none/.test(html), "touch-action must be inline — a sibling overlay defaults to auto");
  assert(html.includes("pointer-events-none"), "a closed pod must not eat a pan starting in that corner");
  assert(html.includes(`width:${POD_DISC_PX}px`), "the disc keeps its 56px hero size");
});

// ------------------------------------------------------------- the open arc
// FocusPodArc is hook-free precisely so it can be rendered here without a DOM.
function arcEl(over: Partial<Parameters<typeof FocusPodArc>[0]> = {},
  actionOver: Partial<PodActionInputs> = {}) {
  const { base } = spies();
  return createElement(FocusPodArc, {
    actions: podActions({ ...base, ...actionOver }),
    exposureS: 3, nextExposure: 5, step: 100, nextStep: 1000, looping: false,
    onExposure: () => {}, onStep: () => {},
    slot: () => ({ position: "absolute" as const, touchAction: "none" as const }),
    onPress: () => {},
    ...over,
  });
}

test("open arc: a labelled role=menu holding five chips and two cycling badges", () => {
  const html = renderToStaticMarkup(arcEl());
  assert(/role="menu"/.test(html), "the arc must be a menu");
  assert(/aria-label="Focus quick controls"/.test(html), "the menu needs a name");
  eq((html.match(/role="menuitem"/g) ?? []).length, 7, "5 chips + 2 badges");
  ["AF", "LOOP", "SHOOT"].forEach((f) => assert(html.includes(f), `chip ${f} missing`));
  assert(html.includes("3s"), "the exposure badge must show the value in force");
  assert(html.includes("100"), "the step badge must show the magnitude in force");
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

test("open arc: every slot sets touch-action:none, because a chip is press-and-slide", () => {
  const html = renderToStaticMarkup(arcEl());
  const slots = (html.match(/data-pod-slot/g) ?? []).length;
  eq(slots, 7, "one positioned slot per menu item");
  assert((html.match(/touch-action:none/g) ?? []).length >= slots,
    "each slot must carry touch-action:none or the page scrolls under the thumb");
});

test("open arc: the exposure badge warns that a preset RESTARTS a running loop", () => {
  // hub.start_loop closes over the exposure it was handed, so a tap that only
  // moved a highlight would be a control that looks applied and is ignored.
  const running = renderToStaticMarkup(arcEl({ looping: true, nextExposure: 5 }, { looping: true }));
  assert(running.includes("Restart the loop at 5s"),
    "a looping rig must be told the tap restarts the loop");
  const idle = renderToStaticMarkup(arcEl({ looping: false, nextExposure: 5 }));
  assert(idle.includes("Use 5s for the next frame"), "an idle rig gets the plain phrasing");
});

test("open arc: the step badge names the value it will move to", () => {
  const html = renderToStaticMarkup(arcEl({ step: 10, nextStep: 100 }));
  assert(html.includes("Focuser step 10 — tap for 100"),
    "a cycling badge has to say where the next tap lands");
});

test("open arc: an unset exposure reads as unset and does not fake a number", () => {
  const html = renderToStaticMarkup(arcEl({ exposureS: null, nextExposure: 1 }));
  assert(html.includes("Exposure not set"), "the label must admit the box is empty");
});

// ------------------------------------------------- press paths (element tree)
test("open arc: pressing a live chip fires its handler and then closes the arc", () => {
  const { log, base } = spies();
  const closes: number[] = [];
  const el = FocusPodArc({
    actions: podActions(base),
    exposureS: 3, nextExposure: 5, step: 100, nextStep: 1000, looping: false,
    onExposure: () => {}, onStep: () => {},
    slot: () => ({}), onPress: () => closes.push(1),
  });
  const items = collectByRole(el, "menuitem");
  eq(items.length, 7, "5 chips + 2 badges");
  (items[2].props.onClick as () => void)();   // SHOOT
  eq(log.join(","), "shoot", "the chip fired FocusView's shoot()");
  eq(closes.length, 1, "an action closes the arc behind itself");
});

test("open arc: a badge cycles WITHOUT closing — three taps must not cost three reopens", () => {
  const exposures: number[] = [];
  const steps: number[] = [];
  const closes: number[] = [];
  const { base } = spies();
  const el = FocusPodArc({
    actions: podActions(base),
    exposureS: 3, nextExposure: 5, step: 100, nextStep: 1000, looping: false,
    onExposure: (s) => exposures.push(s), onStep: (v) => steps.push(v),
    slot: () => ({}), onPress: () => closes.push(1),
  });
  const items = collectByRole(el, "menuitem");
  (items[5].props.onClick as () => void)();   // exposure badge
  (items[6].props.onClick as () => void)();   // step badge
  eq(exposures.join(","), "5", "the exposure badge dispatches the next preset");
  eq(steps.join(","), "1000", "the step badge dispatches the next magnitude");
  eq(closes.length, 0, "cycling must leave the arc open");
});

test("open arc: a blocked chip is not a menuitem button — it is the locked stand-in", () => {
  const { base } = spies();
  const el = FocusPodArc({
    actions: podActions({ ...base, focuserReason: "No focuser is connected" }),
    exposureS: 3, nextExposure: 5, step: 100, nextStep: 1000, looping: false,
    onExposure: () => {}, onStep: () => {}, slot: () => ({}), onPress: () => {},
  });
  eq(collectByRole(el, "menuitem").length, 5, "the two blocked nudges left the pressable set");
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
