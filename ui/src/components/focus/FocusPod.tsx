// FocusPod.tsx — the reach shortcut over the Focus preview (#125).
//
// WHAT THIS IS NOT: a second implementation of anything. Every control on this
// dial already exists in the right-hand rail — the exposure presets, Single,
// Loop, Stop, the ±step nudges and the autofocus hero — and every chip here
// dispatches the SAME handler that panel's button dispatches, against the same
// state. FocusView owns the state and the blockers; this file owns only "where
// your thumb is".
//
// That distinction is the whole reason it is allowed to exist. This codebase
// deleted a duplicate autofocus button once (FocusView.tsx, "NO second run
// button here") and the lesson stands — but what was removed then was a second
// control with its OWN behaviour, which is a question the user has to answer
// before every run. A shortcut that fires the identical action is not that.
//
// WHY IT EXISTS: on a phone the rail sits BELOW the image it changes, so a
// focus loop (shoot → look → nudge → look) was scroll-down / tap / scroll-up /
// look, and the frame and the control that changes it could never be in the
// same glance. That is a reach problem, not a capability gap.
//
// FOUR THINGS THAT BREAK IF THEY ARE MOVED:
//
//  1. This must render as a SIBLING of PreviewStage, never inside it.
//     usePreviewGestures attaches its listeners imperatively to the stage root
//     and only `onPointerDown` honours the `[data-no-pan]` escape hatch
//     (usePreviewGestures.ts:112-113) — the wheel handler calls
//     preventDefault() unconditionally and the double-tap Fit/100% accelerator
//     has no check at all. A chip inside the stage would zoom the image under a
//     scroll wheel and flip the zoom on a double tap. (PreviewStage takes no
//     `children` prop anyway; FocusView wraps the two in a `relative` div.)
//
//  2. `touchAction: "none"` is set INLINE, here, on every surface that takes a
//     pointer. The stage sets it for itself; a sibling overlay defaults to
//     `auto`, so a press that starts on a chip and slides — which is how this is
//     used one-handed — scrolls the PAGE under the thumb instead of driving the
//     control. Every other drag surface in this repo sets it the same way
//     (SlewPad.tsx:314, TouchGuard.tsx:166, StretchHistogram.tsx:232).
//
//  3. A blocked chip renders <LockedChip> with the blocker's SENTENCE, never
//     the native `disabled` attribute: `disabled` strips the control AND its
//     reason out of the accessibility tree and leaves a shape that cannot even
//     be focused to ask why, and `title=` never fires on the tablet this rig is
//     driven from (house rule §11.8). The sentences come from the existing
//     chain — focusCaptureBlocker → captureReason → singleReason — so the pod
//     and the panel two inches away can never give different answers.
//
//  4. Nothing here starts a timer. The exposure ring is fed a fraction that
//     FocusView derives from the ONE second-hand it already runs for the
//     in-flight move and the exposure narrator, and the ring's spoken state is
//     that narrator's own sentence (lib/focusCapture frameWaitNote). A second
//     interval would drift against the first and print two different countdowns
//     18px apart.
import {
  useCallback, useEffect, useRef, useState,
  type CSSProperties, type JSX, type KeyboardEvent as RKeyboardEvent, type RefObject,
} from "react";
import { Icon, type IconName } from "../icons";
import { LockedChip, useMediaQuery } from "../ui";
import { segmentedNextIndex } from "../ui/SegmentedControl";
import { presetAction } from "../../lib/focusCapture";
import { nudgeLabel } from "../../lib/stepDial";

// ---------------------------------------------------------------- geometry
/** Disc diameter. 56px = `.tap-lg`, the house size for a one-tap hero. */
export const POD_DISC_PX = 56;
/** Where the action chips sit. 140px is not a taste call: five 48px chips over
 *  a quarter turn are 22.5° apart, and the chord between neighbours is
 *  2·R·sin(11.25°) — at R=140 that is 54.6px, so two 48px chips clear each
 *  other by 6.6px. Shrink this and they overlap in the dark, where a mistap
 *  costs a 1000-step focuser move. */
export const POD_CHIP_R = 140;
/** The two cycling badges ride inside the arc so they read as belonging to it
 *  rather than as two more actions. 85px clears the chips by ~55px and clears
 *  each other by 49px (they are 33.75° apart). */
export const POD_BADGE_R = 85;

/**
 * A point on the quarter arc that opens up-and-left from the disc.
 *
 * `frac` 0 is due LEFT of the disc, 1 is due UP. Returned as an offset in px
 * from the disc centre, y negative = upward (screen coordinates), so a caller
 * can drop it straight into a translate().
 *
 * Up-and-left because the disc lives in the preview's lower-right corner: that
 * is the only quadrant that is still over the image. An arc that opened the
 * other way would put half its chips outside the frame.
 */
export function podPolar(frac: number, radius: number): { x: number; y: number } {
  const a = (Math.max(0, Math.min(1, frac)) * Math.PI) / 2;
  const round = (v: number) => Math.round(v * 10) / 10;
  return { x: round(-radius * Math.cos(a)), y: round(-radius * Math.sin(a)) };
}

// ------------------------------------------------------------ cycling badges
/**
 * The next value in a cycling badge, wrapping at the top.
 *
 * A badge, not a menu: one target, the current value always visible, nothing to
 * open one-handed in the dark. `current` does not have to BE one of the values
 * — the exposure box is typeable and the setting that actually worked at the
 * scope on 2026-07-31 was FOUR seconds, which is not a preset — so this walks
 * to the next value ABOVE it rather than indexing, and 4 → 5 rather than
 * silently snapping to 1.
 *
 * `null` (an empty or unparseable exposure box) starts the cycle at the bottom,
 * which is also the repair: one tap on the badge puts a valid number in a box
 * that was blocking the shutter.
 */
export function nextInCycle(values: readonly number[], current: number | null): number {
  if (values.length === 0) return current ?? 0;
  if (current == null) return values[0];
  return values.find((v) => v > current) ?? values[0];
}

// -------------------------------------------------------------- HFR trend
/** How much sharper a frame has to be before the caret flips. Mirrors the
 *  threshold FocusVerdict.tsx:135-137 uses for the same caret on the same two
 *  frames — the pod is showing the reader's own number back to them in the
 *  corner of the image, so it must not disagree with the line above the stage
 *  about which way focus went. */
export const HFR_TREND_EPS = 0.05;

export type HfrTrend = "sharper" | "softer" | "flat";

/** Sharper = SMALLER HFR. The caret points down for sharper, which is also the
 *  direction the number moves — the arrow and the digits agree. */
export function hfrTrend(hfr: number | null, prev: number | null): HfrTrend {
  if (hfr == null || prev == null) return "flat";
  if (hfr < prev - HFR_TREND_EPS) return "sharper";
  if (hfr > prev + HFR_TREND_EPS) return "softer";
  return "flat";
}

// ------------------------------------------------------------------- the ring
export type PodRingKind = "idle" | "starting" | "exposing" | "looping" | "blocked";

export interface PodRing {
  kind: PodRingKind;
  /** Stroke weight in px. */
  width: number;
  /** SVG stroke-dasharray; "" is solid. */
  dash: string;
  /** Fraction of the circumference that is drawn (1 = closed ring). */
  arc: number;
  /** What the ring means, as a sentence, for aria and the tooltip. */
  label: string;
}

/**
 * The collapsed disc's ring — what the camera is doing, without opening
 * anything.
 *
 * WEIGHT AND DASH CARRY THE STATE, never hue. In night mode `--good`, `--warn`
 * and `--bad` all resolve to reds and `--accent` is red too, so a ring that
 * distinguished "exposing" from "looping" by colour distinguishes nothing at
 * the eyepiece (house rule: every night-mode distinction needs a non-hue
 * channel). The five states are pairwise distinct in (width, dash) alone, and
 * `podRingKinds` in the test asserts exactly that.
 *
 * Ranked by what is happening RIGHT NOW rather than by severity: a frame in
 * flight outranks a blocker, because the frame is real and the blocker is only
 * about the next tap.
 */
export function podRing(p: {
  /** 0..1 through the in-flight single exposure, or null when none is. */
  progress: number | null;
  /** The exposure narrator's own sentence (frameWaitNote), when it has one. */
  note: string | null;
  looping: boolean;
  /** The POST has been sent but the server has not accepted it yet. */
  starting: boolean;
  /** Why the shutter cannot open, or null. */
  captureBlocked: string | null;
}): PodRing {
  if (p.progress != null) {
    return {
      kind: "exposing", width: 4, dash: "",
      arc: Math.max(0, Math.min(1, p.progress)),
      // The narrator already says "exposing · 3s left"; saying it a second way
      // 18px away is how two countdowns end up disagreeing.
      label: p.note ?? "exposing",
    };
  }
  if (p.starting) {
    return {
      kind: "starting", width: 2.5, dash: "3 4", arc: 1,
      label: "waiting for the camera to accept this exposure — no frame has started yet",
    };
  }
  if (p.looping) {
    return {
      kind: "looping", width: 2.5, dash: "9 6", arc: 1,
      label: "a capture loop is running — frames repeat until you stop it",
    };
  }
  if (p.captureBlocked) {
    return { kind: "blocked", width: 1.5, dash: "1.5 5", arc: 1, label: p.captureBlocked };
  }
  return { kind: "idle", width: 1.5, dash: "", arc: 1, label: "no exposure in flight" };
}

// ------------------------------------------------------------------ the chips
export type PodActionId = "af" | "loop" | "shoot" | "in" | "out";

export interface PodAction {
  id: PodActionId;
  /** The face, kept to a glyph or one short word: at 48px in the dark a
   *  sentence is unreadable and the aria-label is where the sentence goes. */
  face: string;
  icon: IconName | null;
  /** The full sentence — what this does, or (when `reason` is set) the reason
   *  it will not. Never a bare boolean anywhere in this file. */
  hint: string;
  /** Non-null => blocked, and this IS the sentence the LockedChip speaks. */
  reason: string | null;
  press: () => void;
}

export interface PodActionInputs {
  looping: boolean;
  /** Which shutter command is waiting on the server, if any. */
  starting: "single" | "loop" | null;
  /** A single frame the server accepted is still open. */
  exposing: boolean;
  step: number;
  /** singleReason — capture blockers PLUS "a loop already owns the camera". */
  shootReason: string | null;
  /** captureReason — the rig-level half, which is what Loop is gated on. */
  loopReason: string | null;
  /** Stop only needs operator access; it is the way OUT of everything else. */
  stopReason: string | null;
  focuserReason: string | null;
  autofocusReason: string | null;
  onShoot: () => void;
  onLoop: () => void;
  onStop: () => void;
  onNudge: (delta: number) => void;
  onAutofocus: () => void;
}

/**
 * The five chips, in arc order (index 0 sits due LEFT of the disc, index 4 due
 * UP), each already resolved to either an action or the sentence refusing it.
 *
 * ORDER IS A REACH DECISION. Held one-handed the thumb pivots at the lower-right
 * corner and sweeps up first, so the position directly ABOVE the disc is the
 * cheapest reach and gets `+step`, the chip pressed most often in a focus loop.
 * Autofocus — pressed once, deliberately, and never in a hurry — is furthest
 * away at the far end of the arc, where a stray thumb does not land on it.
 *
 * Kept as a pure function of its inputs so the "same handler as the panel"
 * claim is testable: the test hands it spies and asserts which one fired.
 */
export function podActions(p: PodActionInputs): PodAction[] {
  // Exposing/starting are not blockers in the rig sense, but they are still
  // "not now, and here is why". The rail expresses them by SWAPPING Single for
  // an inert "Exposing…" button carrying this exact sentence; the pod has no
  // room for a third chrome, so it routes them through the same locked
  // treatment. Same words either way, which is the part that matters.
  const shootReason =
    p.shootReason
    ?? (p.exposing ? "A frame is already exposing — press Stop to abandon it" : null)
    ?? (p.starting === "single"
      ? "Waiting for the camera to accept this exposure — no frame has started yet"
      : null);
  const loopReason =
    p.loopReason
    ?? (p.starting === "loop"
      ? "Waiting for the camera to accept this exposure — no frame has started yet"
      : null);

  // LOOP becomes STOP while a loop runs. Not a second control: it is the rail's
  // own Stop button, dispatching the rail's own handler. A pod that could start
  // a loop over the preview but not stop it would force the scroll it exists to
  // remove, at the one moment the user is in a hurry.
  const loop: PodAction = p.looping
    ? {
        id: "loop", face: "STOP", icon: "stop",
        hint: p.stopReason ?? "Stop the capture loop",
        reason: p.stopReason, press: p.onStop,
      }
    : {
        id: "loop", face: "LOOP", icon: "refresh",
        hint: loopReason ?? "Loop frames at this exposure until you stop",
        reason: loopReason, press: p.onLoop,
      };

  const nudge = (id: "in" | "out", sign: 1 | -1): PodAction => ({
    id,
    face: sign > 0 ? "+" : "−",
    icon: null,
    // Word-for-word the rail's own nudge label, built from the same helper, so
    // a screen reader hears one control described one way wherever it is
    // reached from.
    hint: `Move focuser ${nudgeLabel(p.step, sign)} steps`
      + (p.focuserReason ? ` — ${p.focuserReason}` : ""),
    reason: p.focuserReason,
    press: () => p.onNudge(sign * p.step),
  });

  return [
    {
      id: "af", face: "AF", icon: "focus",
      hint: p.autofocusReason ?? "Run an autofocus sweep",
      reason: p.autofocusReason, press: p.onAutofocus,
    },
    loop,
    {
      id: "shoot", face: "SHOOT", icon: "capture",
      hint: shootReason ?? "Take one focus frame",
      reason: shootReason, press: p.onShoot,
    },
    nudge("in", -1),
    nudge("out", 1),
  ];
}

// ----------------------------------------------------------------- disc copy
/**
 * What the closed disc is called.
 *
 * It has to answer "what is my focus doing" without being opened, because that
 * is the only reason it carries numbers instead of being a plain button. So the
 * name is the HFR, which way it moved since the last frame, and what the ring
 * is saying — not "focus controls", which describes what the user can already
 * see.
 */
export function podDiscLabel(p: {
  open: boolean;
  hfr: number | null;
  trend: HfrTrend;
  ringLabel: string;
}): string {
  if (p.open) return "Close the focus controls";
  const hfr = p.hfr == null
    ? "no frame measured yet"
    : `HFR ${p.hfr.toFixed(2)}`
      + (p.trend === "sharper" ? ", sharper than the previous frame"
        : p.trend === "softer" ? ", softer than the previous frame"
          : "");
  return `Focus controls — ${hfr}; ${p.ringLabel}`;
}

// ======================================================== the expanded arc
/**
 * The bloomed arc: five chips plus the two cycling badges.
 *
 * Split out of `FocusPod` and kept HOOK-FREE on purpose — a pure function of
 * its props renders under plain `tsx` with no DOM, which is the only way this
 * repo's runner can assert the thing that actually matters here: that a blocked
 * chip renders the blocker's SENTENCE rather than a boolean, and that pressing
 * a live chip dispatches the caller's handler. Same trick as
 * ui/SegmentedControl.tsx. `slot()` (which needs the bloom/reduced-motion state)
 * is passed in rather than computed here for the same reason.
 */
export function FocusPodArc({
  actions, exposureS, nextExposure, step, nextStep, looping,
  onExposure, onStep, slot, onPress, arcRef, onKeyDown,
}: {
  actions: PodAction[];
  exposureS: number | null;
  nextExposure: number;
  step: number;
  nextStep: number;
  looping: boolean;
  onExposure: (s: number) => void;
  onStep: (v: number) => void;
  /** Absolute placement + fly-in for one item, by arc fraction and radius. */
  slot: (frac: number, radius: number, delayIndex: number) => CSSProperties;
  /** Run after a chip fires — the arc closes behind an action, but NOT behind a
   *  badge: cycling 1s → 2s → 3s is one gesture repeated, and closing after
   *  each tap would make the third tap cost three reopens. */
  onPress: () => void;
  arcRef?: RefObject<HTMLDivElement>;
  onKeyDown?: (e: RKeyboardEvent) => void;
}): JSX.Element {
  // `.btn` is not a flex box, so the icon-over-word stack has to say so; the
  // 48px floor is the spec's chip minimum, above the app's 44px tap floor
  // because these are aimed at over a live image rather than in a settled list.
  const chipClass = "btn tap inline-flex flex-col items-center justify-center gap-0.5 leading-none";
  const chipBox: CSSProperties = {
    minWidth: 48, minHeight: 48, paddingLeft: 6, paddingRight: 6, touchAction: "none",
  };
  const badgeBox: CSSProperties = { minWidth: 44, minHeight: 44, touchAction: "none" };

  return (
    <div
      ref={arcRef}
      role="menu"
      aria-label="Focus quick controls"
      onKeyDown={onKeyDown}
      className="absolute inset-0"
      style={{ touchAction: "none" }}
    >
      {actions.map((a, i) => (
        <div key={a.id} data-pod-slot role="none"
          style={slot(i / Math.max(1, actions.length - 1), POD_CHIP_R, i)}>
          {a.reason ? (
            // LockedChip owns its own trigger element, so the sizing goes
            // through className rather than a style prop. It stays focusable,
            // tappable and aria-disabled, and it SPEAKS the sentence on tap —
            // the whole reason `disabled` is not used anywhere in this app.
            <LockedChip
              reason={a.reason}
              className="btn !flex-col justify-center !gap-0.5 min-w-[48px] min-h-[48px] !px-1.5"
            >
              <span className="text-[11px] tracking-wide">{a.face}</span>
            </LockedChip>
          ) : (
            <button
              type="button"
              role="menuitem"
              className={chipClass}
              style={chipBox}
              aria-label={a.hint}
              onClick={() => { a.press(); onPress(); }}
            >
              {a.icon && <Icon name={a.icon} size={15} aria-hidden />}
              <span className={a.icon ? "text-[9px] tracking-wide" : "text-[19px] leading-none"}>
                {a.face}
              </span>
            </button>
          )}
        </div>
      ))}

      {/* The two cycling badges. Deliberately NOT menus: one target, the current
          value on its face, and a tap moves to the next — there is nothing to
          open, aim into and dismiss with one thumb in the dark. The exposure
          badge rides at SHOOT's angle and the step badge sits between − and +,
          which is the same minus·value·plus silhouette the rail's thumb row
          already has, so the two read as the same control in two places. */}
      <div data-pod-slot role="none" style={slot(0.5, POD_BADGE_R, 2)}>
        <button
          type="button"
          role="menuitem"
          data-pod-badge="exposure"
          className="btn tap mono !normal-case justify-center px-2"
          style={badgeBox}
          // presetAction is the rail's own copy for this tap, and it carries the
          // thing that is easy to get wrong: while a loop is running a preset
          // RESTARTS it, because hub.start_loop closed over the exposure it was
          // handed. A badge that quietly changed a highlight would be a control
          // that looks applied and is ignored.
          aria-label={`Exposure ${exposureS == null ? "not set" : `${exposureS} seconds`} — `
            + presetAction(looping, nextExposure).hint}
          onClick={() => onExposure(nextExposure)}
        >
          {exposureS == null ? "—" : `${exposureS}s`}
        </button>
      </div>

      <div data-pod-slot role="none" style={slot(0.875, POD_BADGE_R, 4)}>
        <button
          type="button"
          role="menuitem"
          data-pod-badge="step"
          className="btn tap mono !normal-case justify-center px-2"
          style={badgeBox}
          aria-label={`Focuser step ${step} — tap for ${nextStep}`}
          onClick={() => onStep(nextStep)}
        >
          {step}
        </button>
      </div>
    </div>
  );
}

// =========================================================== the component
export interface FocusPodProps extends PodActionInputs {
  /** The live frame's median HFR, and the frame before it. Both are already
   *  derived in FocusView for the verdict line above the stage — passed in
   *  rather than re-read so the corner and the header cannot disagree. */
  hfr: number | null;
  prevHfr: number | null;
  /** 0..1 through an in-flight single exposure, or null. Derived from the ONE
   *  1s interval FocusView already runs (see header note 4). */
  exposureProgress: number | null;
  /** frameWaitNote's sentence, when there is one. */
  exposureNote: string | null;
  /** What the shutter refuses for, if anything — the ring's blocked state. */
  captureBlocked: string | null;
  /** The exposure the next frame will actually use, or null when the box is
   *  empty/unparseable (in which case the shutter is blocked anyway and one tap
   *  of the badge repairs it). */
  exposureS: number | null;
  exposurePresets: readonly number[];
  stepValues: readonly number[];
  /** The rail's own `applyPreset` — which RESTARTS a running loop, because
   *  hub.start_loop closes over the exposure it was handed. */
  onExposure: (s: number) => void;
  /** The rail's own `setStep`, so the dial in the panel and the badge here are
   *  one value and cannot drift apart. */
  onStep: (v: number) => void;
}
// NOTE: there is deliberately no `onBlocked` toast prop, unlike StepRow's dial.
// A blocked chip here IS a LockedChip, and LockedChip's tooltip opens on TAP —
// the reason is already reachable by the finger that pressed it. Firing a toast
// as well would say the same sentence twice, in two places, from one press.

export default function FocusPod(props: FocusPodProps): JSX.Element {
  const {
    hfr, prevHfr, exposureProgress, exposureNote, captureBlocked,
    exposureS, exposurePresets, stepValues, onExposure, onStep,
  } = props;
  const [open, setOpen] = useState(false);
  // The bloom is a two-frame affair: the chips mount at the disc centre and are
  // pushed out along the radius on the NEXT frame, so the browser has a
  // from-state to transition out of. Mounting them already in place would draw
  // them instantly and there would be nothing to stagger.
  const [bloom, setBloom] = useState(false);
  const arcRef = useRef<HTMLDivElement>(null);
  const discRef = useRef<HTMLButtonElement>(null);
  // prefers-reduced-motion drops the bloom to a plain fade — the arc still
  // appears, it just does not fly. Same contract as index.css's reduced-motion
  // block, which stills animations without removing the state they carry.
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");

  useEffect(() => {
    if (!open) { setBloom(false); return; }
    const id = requestAnimationFrame(() => setBloom(true));
    return () => cancelAnimationFrame(id);
  }, [open]);

  const close = useCallback(() => {
    setOpen(false);
    // Focus has to come back to the disc or it lands on <body> and the next
    // Tab restarts at the top of the page — the classic menu-close trap.
    discRef.current?.focus();
  }, []);

  // Escape closes from ANYWHERE, not just from inside the arc: a keyboard user
  // who has tabbed back out to the disc would otherwise be stuck with an open
  // menu and no way to dismiss it. Window-level, exactly as StepDial does it
  // (StepDial.tsx:56-61) for the same reason.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  // Opening moves focus INTO the arc. Without this, Enter on the disc bloomed
  // the menu and then the arrow keys did nothing, because the arc's key handler
  // only fires for focus inside it — the menu was openable by keyboard and not
  // operable by one. `preventScroll` so a touch open does not also yank the
  // page to wherever the first chip landed.
  useEffect(() => {
    if (!open) return;
    arcRef.current
      ?.querySelector<HTMLElement>('[data-pod-slot] button, [data-pod-slot] [role="button"]')
      ?.focus({ preventScroll: true });
  }, [open]);

  const ring = podRing({
    progress: exposureProgress, note: exposureNote,
    looping: props.looping, starting: props.starting != null, captureBlocked,
  });
  const trend = hfrTrend(hfr, prevHfr);
  const actions = podActions(props);

  const nextExposure = nextInCycle(exposurePresets, exposureS);
  const nextStep = nextInCycle(stepValues, props.step);

  // Arrow keys walk the arc. `segmentedNextIndex` is the app's one navigation
  // model (wrap on the arrows, Home/End to the ends) — reused rather than
  // re-derived so the pod behaves like every other roving-focus control here.
  const onArcKey = (e: RKeyboardEvent) => {
    // Escape is handled window-level above, so it is deliberately absent here.
    const items = arcRef.current
      ? Array.from(arcRef.current.querySelectorAll<HTMLElement>(
        '[data-pod-slot] button, [data-pod-slot] [role="button"]',
      ))
      : [];
    if (items.length === 0) return;
    const here = items.findIndex((el) => el === document.activeElement);
    const next = segmentedNextIndex(e.key, here < 0 ? 0 : here, items.length);
    if (next == null) return;
    e.preventDefault();
    items[next]?.focus();
  };

  // One place decides where a slot sits and how it flies in, so a chip and a
  // badge cannot drift into two different animations.
  const slot = (frac: number, radius: number, delayIndex: number): CSSProperties => {
    const { x, y } = podPolar(frac, radius);
    const placed = `translate(calc(-50% + ${x}px), calc(-50% + ${y}px))`;
    return {
      position: "absolute",
      left: POD_DISC_PX / 2,
      top: POD_DISC_PX / 2,
      // Reduced motion still gets the LAYOUT immediately — only the flight is
      // dropped, to a fade. Stilling an animation must never cost the state it
      // was carrying (the same contract index.css's reduced-motion block keeps,
      // where .led-bad loses its pulse but gains a static outline).
      transform: reduced || bloom ? placed : "translate(-50%, -50%) scale(0.7)",
      opacity: bloom ? 1 : 0,
      transition: reduced
        ? "opacity 120ms ease"
        : "transform 190ms cubic-bezier(0.2, 0.9, 0.3, 1), opacity 150ms ease",
      transitionDelay: reduced ? "0ms" : `${delayIndex * 30}ms`,
      touchAction: "none",
    };
  };

  const C = 2 * Math.PI * 25; // circumference of the r=25 progress ring

  return (
    // pointer-events-none so a CLOSED pod cannot eat a pan that starts in the
    // preview's lower-right corner; every interactive descendant turns them
    // back on explicitly.
    <div className="absolute inset-0 pointer-events-none z-20" style={{ touchAction: "none" }}>
      {/* Tap-away. Scoped to the stage rather than `fixed inset-0` on purpose:
          the pod lives inside a `.panel`, whose backdrop-filter makes a fixed
          element resolve against the PANEL box instead of the viewport
          (measured, index.css §overlay) — so "fixed" would be a lie about where
          this lands. Covering exactly the stage is what it means anyway. */}
      {open && (
        <div className="absolute inset-0 pointer-events-auto" aria-hidden
          style={{ touchAction: "none" }} onPointerDown={close} />
      )}

      {/* Lower-right, but 38px up rather than the usual 12: PreviewStage parks
          its star-decimation disclosure at `right-2 / bottom: 8`
          (PreviewStage.tsx:736), which is ~22px tall and is exactly the chip a
          focuser wants to read — "Showing 240/2100 stars" is how you know the
          overlay is thinning what you are judging. Sitting on it would hide it.
          (The loupe, the other bottom-right tenant, cannot appear here at all:
          `loupeShown` is gated on `!compact` and Focus passes `compact`.) */}
      <div
        className="absolute pointer-events-auto"
        style={{ right: 12, bottom: 38, width: POD_DISC_PX, height: POD_DISC_PX, touchAction: "none" }}
      >
        {/* Radial scrim, behind the arc ONLY. The chips have to read over a
            star field, but the frame being judged must not be dimmed — you are
            looking at it to decide whether to nudge — so this dies out well
            inside the arc instead of tinting the stage. */}
        {open && (
          <div
            aria-hidden
            className="absolute pointer-events-none"
            style={{
              left: POD_DISC_PX / 2, top: POD_DISC_PX / 2,
              width: 2 * (POD_CHIP_R + 44), height: 2 * (POD_CHIP_R + 44),
              transform: "translate(-50%, -50%)", borderRadius: "50%",
              background: "radial-gradient(circle, rgba(0,0,0,0.62) 0%, rgba(0,0,0,0.52) 52%,"
                + " rgba(0,0,0,0.22) 76%, rgba(0,0,0,0) 92%)",
              opacity: bloom ? 1 : 0,
              transition: "opacity 150ms ease",
            }}
          />
        )}

        {open && (
          <FocusPodArc
            actions={actions}
            exposureS={exposureS}
            nextExposure={nextExposure}
            step={props.step}
            nextStep={nextStep}
            looping={props.looping}
            onExposure={onExposure}
            onStep={onStep}
            slot={slot}
            onPress={close}
            arcRef={arcRef}
            onKeyDown={onArcKey}
          />
        )}

        {/* The disc. Collapsed it is not a bare button: it carries the two
            numbers you stare at while focusing, so the shortcut earns its 56px
            even when it is never opened.

            Deliberately NOT an aria-live region. The FocusVerdict line above
            the stage already announces every new HFR politely; a second live
            region reading the same number from the same frame would say it
            twice per exposure, which is how a screen-reader user learns to
            ignore the channel entirely. */}
        <button
          ref={discRef}
          type="button"
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={podDiscLabel({ open, hfr, trend, ringLabel: ring.label })}
          title={podDiscLabel({ open, hfr, trend, ringLabel: ring.label })}
          className="absolute inset-0 rounded-full flex flex-col items-center justify-center
                     border border-line2 bg-panel"
          style={{ touchAction: "none" }}
          onClick={() => (open ? close() : setOpen(true))}
        >
          <svg
            viewBox="0 0 56 56" width={POD_DISC_PX} height={POD_DISC_PX} aria-hidden
            className="absolute inset-0 -rotate-90"
          >
            <circle
              cx="28" cy="28" r="25" fill="none"
              stroke={ring.kind === "idle" ? "var(--line-bright)"
                : ring.kind === "blocked" ? "var(--text-faint)" : "var(--accent)"}
              strokeWidth={ring.width}
              strokeLinecap="butt"
              // Progress and pattern share one attribute: a partial arc is
              // "dash of N, gap of everything else", which is why `arc` is a
              // fraction rather than a second drawing mode.
              strokeDasharray={ring.arc < 1 ? `${(C * ring.arc).toFixed(2)} ${C.toFixed(2)}` : (ring.dash || undefined)}
            />
          </svg>
          {ring.kind === "blocked" && (
            <Icon name="lock" size={11} className="text-faint -mb-0.5" aria-hidden />
          )}
          <span className="mono text-[11px] leading-none text-dim">
            {hfr == null ? "—" : hfr.toFixed(2)}
          </span>
          {/* Shape, not hue: in night mode the good/warn tokens are both red, so
              the caret's DIRECTION is what says which way focus went. */}
          {trend !== "flat" && (
            <Icon
              name={trend === "sharper" ? "arrow-down" : "arrow-up"}
              size={11}
              className={trend === "sharper" ? "text-good" : "text-warn"}
              aria-hidden
            />
          )}
        </button>
      </div>
    </div>
  );
}
