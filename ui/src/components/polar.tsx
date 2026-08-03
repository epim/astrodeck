/** Polar alignment bullseye reticle (HERO 2 — the TPPA wizard's spatial view).
 *  Center = the true pole; the dot is the mount's axis; the vector is the skew.
 *
 *  The reticle ZOOMS. Its outer ring is whichever rung of `CEIL_LADDER` currently
 *  contains the error, from 300′ (tripod is pointing at the wrong bit of sky)
 *  down to 1′ (chasing the last of it in the dark), and every ring is labelled
 *  with its own arcminute value so the scale is never something you have to
 *  remember. The tier boundaries — good inside 2′, warn inside 10′ — carry the
 *  verdict, as a stroked, labelled EDGE and not merely a change of fill tint
 *  (`tierMarks`); the ladder rings carry the ruler. Two scales, two questions.
 *
 *  Two motions, both sanctioned (spec §0.6) and both tweens of REAL readings
 *  rather than canned loops: the vector CONVERGES toward center as the user
 *  turns the knobs, and a ladder step GLIDES the ruler rather than swapping it.
 *  The second one is not decoration — see `useEasedScale`, a silent rescale can
 *  make an improving alignment look like a worsening one. Under
 *  prefers-reduced-motion both snap to the final state and the scale change is
 *  still announced in words.
 *
 *  Everything is token-driven (no hardcoded hex) so it redshifts in .night and
 *  lightens on the light ground with no component change. */
import { useEffect, useRef, useState } from "react";

/* Knob-direction hint. The native engine emits an authoritative knob label
   (astro-tppa error_det → knob_label): which way to physically turn each bolt.
   NINA/sim don't, so we fall back to the signed error's sense. Exported so the
   Align view's stat row and this reticle decode identically (one source). */
export type KnobDir =
  | "up" | "down" | "left_west" | "left_east" | "right_west" | "right_east";

export interface Hint {
  arrow: string; // ◀ ▶ ▲ ▼
  text: string; // "turn W" | "raise" | …
}

export function knobHint(
  dir: KnobDir | null | undefined,
  signedArcmin: number,
  axis: "az" | "alt",
): Hint | null {
  if (axis === "alt") {
    // authoritative native label, else the signed error's sense
    if (dir === "up") return { arrow: "▲", text: "raise" };
    if (dir === "down") return { arrow: "▼", text: "lower" };
    if (dir === "left_west" || dir === "left_east" || dir === "right_west" || dir === "right_east")
      return null; // wrong-axis label; ignore
    if (!Number.isFinite(signedArcmin) || signedArcmin === 0) return null;
    return signedArcmin > 0 ? { arrow: "▼", text: "lower" } : { arrow: "▲", text: "raise" };
  }
  // azimuth
  if (dir === "left_west") return { arrow: "◀", text: "turn W" };
  if (dir === "left_east") return { arrow: "◀", text: "turn E" };
  if (dir === "right_west") return { arrow: "▶", text: "turn W" };
  if (dir === "right_east") return { arrow: "▶", text: "turn E" };
  if (dir === "up" || dir === "down") return null; // wrong-axis label
  if (!Number.isFinite(signedArcmin) || signedArcmin === 0) return null;
  return signedArcmin < 0
    ? { arrow: "◀", text: "turn E" }
    : { arrow: "▶", text: "turn W" };
}

/* Verdict tier from total error (spec §HERO2): <2′ excellent · 2–10′ good ·
   >10′ keep going. Drives the reticle ZONE hue (tokened good/warn/bad) and the
   panel's verdict text; the error vector/dot/hints stay --accent (the "guide
   star") so the mark survives night mode where the hue collapses. Exported so
   the panel verdict and the reticle zones agree on the tier. */
export type PolarTier = "excellent" | "good" | "keepgoing";
export function polarTier(total: number): PolarTier {
  return total < 2 ? "excellent" : total < 10 ? "good" : "keepgoing";
}

/** Per-tier INSTRUCTION copy. This deliberately does NOT share `polarTier`'s
 *  2′/10′ boundaries: the tier answers "how good is my alignment" (and feeds the
 *  tested verdict strings), while this answers "what do I physically turn next",
 *  and the two questions break at different errors. Above 30′ no bolt has the
 *  travel — the tripod itself is pointing wrong — so telling the user to keep
 *  turning bolts sends them on a fruitless five minutes. Below 1′ the honest
 *  answer is "stop", because further refinement is inside the noise of most
 *  mounts. Two independent scales is correct here, not an inconsistency. */
export function polarInstruction(total: number): string {
  if (!Number.isFinite(total)) return "Fine bolt adjustment.";
  if (total > 30) return "Rotate the tripod, or reposition the pier.";
  if (total >= 10) return "Use the azimuth and altitude bolts.";
  if (total >= 1) return "Fine bolt adjustment.";
  return "Good enough for most imaging.";
}

/* ===== the zoom ladder =====================================================
   The reticle's outer ring is worth `CEIL_LADDER[i]` arcminutes and everything
   inside scales with it. The ladder used to start at 10, so the view could only
   ever zoom OUT: a user who had worked their error down to 40″ was still staring
   at a 10′ reticle with the dot parked on the centre pip, unable to see which
   way the last of the skew pointed. The sub-10 rungs are the zoom-IN half.

   Rungs are multiplicative (roughly 1.5x per step) so a step is always a visible
   rescale and never a nudge. */
export const CEIL_LADDER = [1, 2, 3, 5, 10, 15, 20, 30, 50, 75, 100, 150, 200, 300];

/* The two margins ARE the hysteresis band, and the band is the whole point.
   The old code had a single 1.08 margin, which meant rung 10 was chosen exactly
   while total <= 9.259′. A live plate-solve series jittering across 9.26′ — an
   utterly ordinary reading — flipped 10↔15 on consecutive updates, and with it
   `k` (16.5↔11.0 px/arcmin), every ring radius and the dot. The display pumped
   while the mount sat still.

   So stepping OUT and stepping IN now use different thresholds. Leaving rung 10
   upward needs total > 10/1.08 = 9.26′; coming back down to it needs total to
   fall to 10/1.35 = 7.41′. Anything in between HOLDS whatever rung we are on,
   which is what makes it hysteresis rather than a rounder threshold. */
const OUT_MARGIN = 1.08; // the error must fit inside the rung with 8% to spare
const IN_MARGIN = 1.35;  // …and clear it by 35% before we dare zoom back in

/** The rung whose outer ring should sit at the reticle's edge.
 *
 *  `current` is the rung already on screen. Pass it and you get hysteresis; omit
 *  it (first render, or a deliberate reset) and you get the plain best fit.
 *  Idempotent — `boundaryArcmin(t, boundaryArcmin(t, c)) === boundaryArcmin(t, c)`
 *  — which is what makes it safe to drive from a ref written during render, and
 *  safe under StrictMode's double invocation. */
export function boundaryArcmin(total: number, current?: number): number {
  const t = Number.isFinite(total) ? Math.max(0, total) : 0;
  const fit = (margin: number) => {
    const want = t * margin;
    for (const s of CEIL_LADDER) if (s >= want) return s;
    return CEIL_LADDER[CEIL_LADDER.length - 1];
  };
  const out = fit(OUT_MARGIN);
  if (current == null || !CEIL_LADDER.includes(current)) return out;
  if (out > current) return out;      // error outgrew the ring — step out at once
  const inward = fit(IN_MARGIN);
  if (inward < current) return inward; // …but only zoom in with room to spare
  return current;                      // inside the band: hold, do not pump
}

/** The labelled rings to draw for a boundary rung: the boundary itself plus the
 *  nearest rungs below it that are FAR ENOUGH apart to read as a scale.
 *
 *  Walking straight down the ladder gives neighbours like 20 and 30, whose
 *  circles land 55px apart on a 380-unit viewBox with their labels stacked in
 *  the same 13px gutter — a smear, not a scale. Requiring each kept rung to be
 *  at most 0.62x the last one throws those away; three rings is what the box
 *  holds without the innermost colliding with the centre pip.
 *
 *  Returned ascending so the caller paints the outer ring last. Every rung here
 *  gets its arcminute label at the call site: that labelling is what replaces
 *  the old "pin 2′ and 10′ so 2′ is never a moving target" rule, which this
 *  feature deliberately overturns (below 3′ a fixed 10′ ring is off-screen). */
export function ladderRings(smax: number): number[] {
  const i = CEIL_LADDER.indexOf(smax);
  if (i < 0) return [smax];
  const out = [smax];
  for (let j = i - 1; j >= 0 && out.length < 3; j--) {
    if (CEIL_LADDER[j] <= out[out.length - 1] * 0.62) out.push(CEIL_LADDER[j]);
  }
  return out.reverse();
}

/** The two VERDICT boundaries — 2′ "stop", 10′ "keep going" — that must also be
 *  marked on the reticle, wherever the ladder has not already drawn them.
 *
 *  The ladder puts a ring on 2′ at exactly one of fourteen rungs (`ladderRings`
 *  yields 2 only for `smax === 2`, which with hysteresis holds from 0.74′ to
 *  1.85′), so the goal ring appeared only once the goal was already reached. At
 *  3′–30′ — the entire range in which the bolts can still move you — the target
 *  had no stroke and no label; its only trace was the `--good` disk's change of
 *  tint. In `.night` every status token is a red at 0.05–0.10 fill opacity and
 *  that edge composites to 1.10:1, which is the case `index.css:154-157` calls
 *  out by name: "any status that must survive night mode needs a non-hue
 *  channel". So the tier edges are drawn as strokes and labelled, and this
 *  function says which ones are worth drawing at a given scale.
 *
 *  Excluded when the ladder already draws that ring (one circle, one label — a
 *  second copy of "10′ keep going" in the other gutter is noise), and when the
 *  mark would land on the centre pip: `TIER_MIN_FRAC` is 10 of the reticle's 165
 *  units, comfortably clear of the 3-unit pip. */
export const TIER_BOUNDS = [2, 10];
const TIER_MIN_FRAC = 10 / 165;
export function tierMarks(smax: number): number[] {
  const ladder = ladderRings(smax);
  return TIER_BOUNDS.filter(
    (t) => t < smax && !ladder.includes(t) && t / smax >= TIER_MIN_FRAC,
  );
}

/** What a ring is FOR, where it happens to coincide with a verdict boundary.
 *  The tier and the scale are separate questions, but where they line up it
 *  costs nothing to answer both. Shared by the ladder labels and the tier ones
 *  so the same radius never gets two different words. */
export function ringNote(r: number): string {
  return r === 2 ? " stop" : r === 10 ? " keep going" : "";
}

/** Ascending union — the ring set on screen during a step is the outgoing one
 *  plus the incoming one, so nothing pops out of existence mid-glide. */
function union(a: number[], b: number[]): number[] {
  return Array.from(new Set([...a, ...b])).sort((x, y) => x - y);
}

/** A ring on screen, with the opacity its fade STARTS from if it is leaving.
 *  A ring that is not leaving is drawn at full strength and ignores `fade`. */
export type FadingRing = { r: number; fade: number };

/** The half-strength a ring drops to the moment it begins leaving. */
const FADE_START = 0.5;

export function freshRings(rs: number[]): FadingRing[] {
  return rs.map((r) => ({ r, fade: FADE_START }));
}

/** Merge the rings currently on screen with the ring set of `rung`, carrying
 *  each leaving ring's fade level forward by `prog` of the step it was in.
 *
 *  The carry is what makes a mid-step retarget continuous rather than merely
 *  non-fatal. Restarting every leaving ring at FADE_START would take a circle
 *  that had faded to 0.11 and brighten it back to 0.5 — a smaller pop than the
 *  one being fixed, but still a pop, and in the same rare window (two readings
 *  inside the 700ms step). Rings belonging to `rung` are staying, so they reset:
 *  a ring only starts fading when it starts leaving. Pure, and tested. */
export function mergeStepRings(on: FadingRing[], rung: number, prog: number): FadingRing[] {
  const staying = ladderRings(rung);
  const known = new Map(on.map(({ r, fade }) => [r, fade]));
  return union(on.map((x) => x.r), staying).map((r) => ({
    r,
    fade: staying.includes(r) ? FADE_START : (known.get(r) ?? FADE_START) * (1 - prog),
  }));
}

/** prefers-reduced-motion, live. Under reduce we snap to final readings (spec
 *  §0.6: show the final state, no convergence animation). */
function usePrefersReducedMotion(): boolean {
  const [reduce, setReduce] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduce(mq.matches);
    const on = () => setReduce(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduce;
}

/** Ease the (az, alt) point from the last reading to the new one over ~600ms so
 *  the vector visibly CONVERGES between successive plate-solve updates instead
 *  of teleporting. This is a tween of REAL readings, not a scripted loop: each
 *  new prop pair starts a fresh ease from wherever the dot currently sits. Snaps
 *  instantly when animation is off (reduced motion / no rAF). */
function useEasedPoint(az: number, alt: number, animate: boolean): [number, number] {
  const [pt, setPt] = useState<[number, number]>([az, alt]);
  const cur = useRef<[number, number]>([az, alt]);
  const raf = useRef<number | null>(null);
  useEffect(() => {
    if (!animate || typeof requestAnimationFrame === "undefined") {
      cur.current = [az, alt];
      setPt([az, alt]);
      return;
    }
    const from = cur.current;
    const to: [number, number] = [az, alt];
    const t0 = (typeof performance !== "undefined" ? performance.now() : Date.now());
    const dur = 600;
    const tick = (now: number) => {
      const raw = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - raw, 3); // easeOutCubic — quick then settling
      const p: [number, number] = [
        from[0] + (to[0] - from[0]) * e,
        from[1] + (to[1] - from[1]) * e,
      ];
      cur.current = p;
      setPt(p);
      if (raw < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current != null) cancelAnimationFrame(raf.current);
    };
  }, [az, alt, animate]);
  return pt;
}

/** Ease the boundary rung itself, so a ladder step GLIDES the whole reticle
 *  instead of snapping it.
 *
 *  This is the defect that mattered most. `useEasedPoint` has always tweened the
 *  dot, but `k` and every ring radius were plain render values, so a rung change
 *  teleported the scale under a still-moving dot. That is not a cosmetic
 *  problem: **a silent rescale makes a shrinking error look like a growing one.**
 *  Zoom from 15′ to 10′ and the marker's distance from centre *increases* — the
 *  frame after the user improved their alignment shows the dot further out. A
 *  snap therefore reads as backwards progress and the user turns the bolt back.
 *  Watching the rings expand is what identifies the motion as a rescale.
 *
 *  Interpolated in LOG space because the ladder is multiplicative: a linear
 *  tween of the boundary makes `k = R/boundary` whip early and crawl late, which
 *  looks like the instrument snagging. Returns [scale, progress]; `progress` is
 *  the eased 0..1 of the step in flight and is 1 at rest, which is how the caller
 *  cross-fades the rings that are leaving. */
function useEasedScale(target: number, animate: boolean): [number, number] {
  const [st, setSt] = useState<[number, number]>([target, 1]);
  const cur = useRef(target);
  const raf = useRef<number | null>(null);
  useEffect(() => {
    if (!animate || typeof requestAnimationFrame === "undefined") {
      cur.current = target;
      setSt([target, 1]);
      return;
    }
    const from = cur.current;
    if (from === target) return;
    const t0 = (typeof performance !== "undefined" ? performance.now() : Date.now());
    // Longer than the point's 600ms on purpose: the rescale moves everything on
    // screen at once, and matching the dot's duration made the two motions read
    // as one jump rather than "the ruler changed, then the dot settled".
    const dur = 700;
    const tick = (now: number) => {
      const raw = Math.min(1, (now - t0) / dur);
      const e = 1 - Math.pow(1 - raw, 3); // easeOutCubic, same feel as the point
      // Land on the rung EXACTLY. `from * (target/from)` is not bit-identical to
      // `target` for rungs like 3→5, and the caller decides the step is over by
      // comparing the two — a half-ulp of drift would leave the reticle
      // permanently mid-step, holding a stale set of retired rings.
      const v = raw >= 1 ? target : from * Math.pow(target / from, e);
      cur.current = v;
      setSt([v, e]);
      if (raw < 1) raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => {
      if (raf.current != null) cancelAnimationFrame(raf.current);
    };
  }, [target, animate]);
  // With animation off, answer from the prop rather than from state: state only
  // catches up in an effect, which would leave one frame drawn at the OLD scale
  // with the NEW rings — a flicker, and the one thing a reduced-motion user
  // asked not to see.
  return animate ? st : [target, 1];
}

export function PolarReticle({
  az,
  alt,
  azDir,
  altDir,
  active = true,
}: {
  az: number;
  alt: number;
  azDir?: KnobDir | null;
  altDir?: KnobDir | null;
  /** false while idle/measuring (no fitted error yet): draw the empty target. */
  active?: boolean;
}) {
  const reduce = usePrefersReducedMotion();
  const [eAz, eAlt] = useEasedPoint(az, alt, active && !reduce);

  const size = 380, cx = size / 2, cy = size / 2, R = 165;

  /* TWO totals, and which one drives what is a deliberate split.
     `total` is the SERVER's reading (the raw props). `eTotal` is where the dot
     currently is mid-tween. The ladder is driven by the raw reading because a
     rung is a property of the MEASUREMENT, not of an animation frame: driven by
     the eased magnitude (as it was), a single 12′→3′ update swept the dot
     through 9.26′ on its way in and tripped a rung change mid-flight, so the
     ruler moved while the dot was still travelling against it. One reading now
     decides one rung, once, and the point tween and the scale tween start
     together. The aria-label uses the raw reading for the same reason — the
     eased one made a screen reader recite ~36 intermediate values per update. */
  const total = Math.hypot(az, alt);
  const eTotal = Math.hypot(eAz, eAlt);

  /* The rung on screen, remembered across renders so `boundaryArcmin` can apply
     its hysteresis band. A ref (not state) because the rung is derived from the
     props, not owned: putting it in state would need an effect and would render
     one frame at the stale scale. `boundaryArcmin` is idempotent in `current`,
     so this render-time write is stable under StrictMode's double invocation.

     With no fitted error the ladder is pinned to 10′ rather than fed a zero:
     zero fits the floor rung, and an idle reticle drawn at 1′ full-scale claims
     you are already aligned to an arcminute before the first solve has run. */
  const rungRef = useRef<number>(10);
  const smaxTarget = active ? boundaryArcmin(total, rungRef.current) : 10;
  rungRef.current = smaxTarget;

  // The animated scale. Radii and `k` read from this; the ring SET reads from
  // the target rung, so a step is "the rings you are about to have, gliding
  // into place" rather than a new set appearing at final size.
  const [smax] = useEasedScale(smaxTarget, !reduce);
  const k = R / smax;

  /* Announce the rescale. A silent scale change is the failure mode this whole
     feature exists to fix, so the step states its direction AND both rungs —
     "ZOOM IN 15′ → 10′" — which is precisely the information the user cannot
     recover from the screen once the old rings are gone. */
  const [announce, setAnnounce] = useState<{ from: number; to: number } | null>(null);
  const [announceFade, setAnnounceFade] = useState(false);
  const shownRung = useRef(smaxTarget);
  const wasActive = useRef(active);
  useEffect(() => {
    // The idle→first-solve transition is not a rescale the user caused: the
    // reticle was parked at 10′ with nothing in it. Announcing "ZOOM OUT 10′ →
    // 30′" there describes the empty state, not a change in the alignment.
    const firstReading = !wasActive.current;
    wasActive.current = active;
    if (!active) {
      /* Going idle is not a rescale — and the message must not outlive the
         reading that produced it. This branch returns no cleanup, so the two
         fade timers were cancelled by the PREVIOUS run's cleanup and never
         re-armed: a session that converged, announced "ZOOM IN 2′ → 1′", and
         was then restarted (`start()` resets state to idle → total 0 → no
         reading → active false) left that string burned into the corner of an
         empty reticle for the rest of the session. */
      shownRung.current = smaxTarget;
      setAnnounce(null);
      return;
    }
    if (firstReading || shownRung.current === smaxTarget) {
      shownRung.current = smaxTarget;
      return;
    }
    setAnnounce({ from: shownRung.current, to: smaxTarget });
    setAnnounceFade(false);
    shownRung.current = smaxTarget;
    const hold = setTimeout(() => setAnnounceFade(true), 1400);
    const gone = setTimeout(() => setAnnounce(null), 2100);
    return () => { clearTimeout(hold); clearTimeout(gone); };
  }, [smaxTarget, active]);

  // clamp the dot to the boundary so a large error still points the right way
  const rawD = Math.hypot(eAz * k, eAlt * k);
  const scale = rawD > R ? R / rawD : 1;
  const dx = cx + eAz * k * scale;
  const dy = cy - eAlt * k * scale;

  /* RINGS — re-derived from the rung, not the old fixed 2′/10′ pair.
     During a step we draw the union of the outgoing and incoming sets so no
     circle pops out of existence: rings that are leaving fade out while the
     arriving ones glide in from beyond the clip. Ascending, so the boundary
     paints last (it is the one that has to survive crossing the labels).

     Step progress is measured off the SCALE, not off a clock. The naive version
     — read the ease's own 0..1 — is wrong for exactly one frame: the render in
     which `smaxTarget` changes still has the pre-step `smax` and a progress of
     1, so the ring being retired would render at zero opacity and blink out
     before its fade began. Deriving progress from where `smax` actually sits
     between the two rungs is 0 on that frame by construction.

     The step is snapshotted when the TARGET changes, not when the previous step
     FINISHES, and that distinction is worth a defect. A reading landing mid-glide
     used to leave `from` naming a rung the scale had already travelled past:
     step 10′→50′, retarget to 20′ at smax≈35, and log(35/10)/log(20/10) = 1.81
     clamps to 1, so every leaving ring rendered at ZERO opacity on its first
     frame instead of fading. Worse, the union was built from the rung we left
     two steps ago, so the 15′ and 30′ circles actually on screen were not in it
     at all and vanished in a single frame with no fade path — precisely the pop
     this animation exists to prevent. Remembering the scale, the ring set AND
     each ring's current fade level (`mergeStepRings`) at the instant of the
     retarget fixes all of it. */
  const ringSet = ladderRings(smaxTarget);
  const settled = smax === smaxTarget;
  const step = useRef<{ from: number; fromRung: number; rings: FadingRing[]; target: number }>({
    from: smaxTarget, fromRung: smaxTarget, rings: freshRings(ringSet), target: smaxTarget,
  });
  const progOf = (from: number, to: number) => {
    const span = Math.log(to / from);
    return span === 0 ? 1 : Math.min(1, Math.max(0, Math.log(smax / from) / span));
  };
  // Both writes are guarded by a comparison against what they store, so
  // StrictMode's second invocation of this render is a no-op.
  if (step.current.target !== smaxTarget) {
    const prev = step.current;
    step.current = {
      from: smax,                    // wherever the glide had actually reached
      fromRung: prev.target,         // the boundary that is now on its way out
      rings: mergeStepRings(prev.rings, prev.target, progOf(prev.from, prev.target)),
      target: smaxTarget,
    };
  }
  if (settled) {
    step.current = {
      from: smaxTarget, fromRung: smaxTarget, rings: freshRings(ringSet), target: smaxTarget,
    };
  }
  const prog = progOf(step.current.from, smaxTarget);
  const rings = settled
    ? freshRings(ringSet).map(({ r, fade }) => ({ r, leaving: false, fade }))
    // prog 0 here because this merge only ADDS the incoming rings; the fade the
    // leaving ones are already carrying is applied per-ring at paint time.
    : mergeStepRings(step.current.rings, smaxTarget, 0)
      .map(({ r, fade }) => ({ r, leaving: !ringSet.includes(r), fade }));

  /* The verdict boundaries the ladder is not already drawing (see `tierMarks`).
     Read off the TARGET rung so the set is stable for the whole step. */
  const marks = tierMarks(smaxTarget);
  /* A tier edge fades in as the scale opens past it: at `t === smax` the disk's
     edge IS the boundary ring, and a second stroke there only thickens the line.
     Rungs step by at least 1.5x, so any settled scale that shows a mark at all
     shows it at full strength — the ramp only does work mid-glide. */
  const tierEdge = (t: number) =>
    marks.includes(t) ? Math.min(1, Math.max(0, (smax / t - 1) * 6)) : 0;

  // Halo so labels stay legible over rings/vector in day AND night (paint a --bg
  // stroke UNDER the fill).
  const labelHalo = {
    paintOrder: "stroke" as const,
    stroke: "var(--bg)",
    strokeWidth: 3,
    strokeLinejoin: "round" as const,
  };

  const azHint = active ? knobHint(azDir, az, "az") : null;
  const altHint = active ? knobHint(altDir, alt, "alt") : null;

  // vector/dot glide: the eased point already moves smoothly; a short CSS
  // transition on the dot's radius keeps the settle from snapping.
  const dotTrans = reduce ? undefined : "r 200ms ease-out";

  return (
    <>
    <svg viewBox={`0 0 ${size} ${size}`} className="w-full mx-auto block instr-fit"
      style={{ aspectRatio: "1 / 1", maxWidth: "338px" }}
      role="img"
      aria-label={
        active
          ? `Polar error ${total.toFixed(1)} arcminutes, view scale ${smaxTarget} arcminute${smaxTarget === 1 ? "" : "s"}${azHint ? `, azimuth ${azHint.text}` : ""}${altHint ? `, altitude ${altHint.text}` : ""}`
          : "Polar alignment reticle — no measurement yet"
      }>
      <defs>
        <marker id="pa-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill="var(--accent)" />
        </marker>
        {/* Rings mid-step live OUTSIDE R — a ring arriving during a zoom-out
            starts beyond the edge and glides in. Without this clip the viewBox
            (380 wide vs a 165 radius) shows them as four stray corner arcs. */}
        <clipPath id="pa-clip">
          <circle cx={cx} cy={cy} r={R + 6} />
        </clipPath>
      </defs>

      {/* Tier zones — bad annulus (outside 10′), warn disk (inside 10′), good
          disk (inside 2′). Clamped to the boundary so they never spill past the
          outer ring; now that the ladder reaches below 10′ that clamp does real
          work — at a 3′ scale the whole reticle is inside the "good" tier and
          fills accordingly.

          The two inner disks carry a STROKE and not only a fill. A fill edge is
          a hue step, and in .night --good/--warn/--bad are all reds at 0.05–0.10
          opacity: the good disk's edge composites to rgb(48,13,11) against
          rgb(25,9,6), i.e. 1.10:1 — invisible, and exactly the failure
          index.css:154-157 forbids. The stroke is a luminance step (4.9:1 for
          --good, 6.7:1 for --warn on the night ground), which is the non-hue
          channel that rule demands. `tierEdge` is 0 where the ladder already
          draws that ring or where the mark would sit on the centre pip. */}
      <circle cx={cx} cy={cy} r={R} fill="var(--bad)" fillOpacity={0.05} />
      <circle cx={cx} cy={cy} r={Math.min(10, smax) * k} fill="var(--warn)" fillOpacity={0.05}
        stroke="var(--warn)" strokeWidth={1} strokeOpacity={tierEdge(10)} />
      <circle cx={cx} cy={cy} r={Math.min(2, smax) * k} fill="var(--good)" fillOpacity={0.1}
        stroke="var(--good)" strokeWidth={1} strokeOpacity={tierEdge(2)} />

      {/* Scale rings. The outermost (the boundary) is solid and heavier; inner
          references are thin and dashed. Weight + dash, never hue, so the two
          read apart in .night where every stroke collapses to the same red.
          None of them takes --accent: that is reserved for the error mark, which
          has to stay the one thing your eye finds on this instrument. */}
      <g clipPath="url(#pa-clip)">
        {rings.map(({ r, leaving, fade }) => {
          // A retiring boundary keeps its solid stroke while it fades. Flipping
          // it to the dashed inner style for the 700ms of the step would read as
          // the ring changing meaning rather than leaving. `leaving` is the load
          // -bearing half of that test: on a zoom-out 5′→10′ the 5′ ring is in
          // BOTH sets, so matching `fromRung` alone drew it solid at width 1.4
          // for 700ms and then snapped it to dashed 0.8 the frame the animation
          // landed — a pop at the end of an animation whose job is to remove pops.
          const boundary = r === smaxTarget || (leaving && r === step.current.fromRung);
          return (
            <circle key={r} cx={cx} cy={cy} r={r * k} fill="none"
              stroke="var(--line-bright)"
              strokeWidth={boundary ? 1.4 : 0.8}
              // A ring that is on its way out dims as the step proceeds rather
              // than blinking off at the end of it.
              strokeOpacity={leaving ? fade * (1 - prog) : 1}
              strokeDasharray={boundary ? "" : "2 5"} />
          );
        })}
      </g>

      {/* crosshair */}
      <line x1={cx - R} y1={cy} x2={cx + R} y2={cy} stroke="var(--line)" />
      <line x1={cx} y1={cy - R} x2={cx} y2={cy + R} stroke="var(--line)" />

      {/* EVERY ring carries its arcminute label. That is the promise that lets
          the rings move at all: the old code pinned 2′/10′ so the scale could be
          read from memory, and this feature gives that up in exchange for a
          reticle that still works at 40″. Labels sit just inside their own ring
          on the top vertical. Rungs that coincide with a verdict boundary say so
          — the tier and the scale are separate questions, but where they happen
          to line up it costs nothing to answer both. */}
      {rings.map(({ r, leaving, fade }) => {
        const rp = r * k;
        // Off-scale mid-step, or crushed against the centre pip: no label.
        if (rp > R + 1 || rp < 16) return null;
        const boundary = r === smaxTarget || (leaving && r === step.current.fromRung);
        return (
          <text key={r} x={cx + 6} y={cy - rp + 13}
            fill={boundary ? "var(--text)" : "var(--text-dim)"}
            fontSize={boundary ? 11 : 10} fontFamily="IBM Plex Mono"
            opacity={leaving ? fade * (1 - prog) : 1} style={labelHalo}>
            {r}′{ringNote(r)}
          </text>
        );
      })}

      {/* …and the tier marks carry theirs in the BOTTOM gutter. Every ladder
          label stacks in the top one, and the 2′ mark sits close inside its
          neighbouring rung at exactly the scales where it matters (at 15′ the 2′
          and 3′ circles are 11 units apart) — the 0.62 separation gate exists
          because two labels in one gutter read as a smear. A second gutter costs
          nothing and lets the target keep its word. */}
      {marks.map((t) => {
        const rp = Math.min(t, smax) * k;
        if (rp < 16) return null; // crushed onto the centre pip: stroke only
        return (
          <text key={`t${t}`} x={cx + 6} y={cy + rp - 5}
            fill="var(--text)" fontSize={10} fontFamily="IBM Plex Mono"
            opacity={tierEdge(t)} style={labelHalo}>
            {t}′{ringNote(t)}
          </text>
        );
      })}

      {/* The rescale, said out loud. Both rungs and the direction, because once
          the old rings are gone there is nothing on screen to compare against —
          and a zoom-in moves the dot OUTWARD, which without this reads as the
          alignment getting worse at the exact moment it got better. */}
      {active && announce && (
        <text x={10} y={18} fill="var(--accent)" fontSize={11} fontFamily="IBM Plex Mono"
          fontWeight={600} letterSpacing="0.5" style={{
            ...labelHalo,
            opacity: announceFade ? 0 : 1,
            // Reduced motion still gets the message — it is information, not
            // decoration — it just stops rather than fades.
            transition: reduce ? undefined : "opacity 650ms ease",
          }}>
          {announce.to < announce.from ? "ZOOM IN" : "ZOOM OUT"} {announce.from}′ → {announce.to}′
        </text>
      )}

      {/* orientation axis labels (kept minimal so the knob hints stand out) */}
      <text x={cx - R + 2} y={cy - 5} fill="var(--text-dim)" fontSize={10} fontFamily="IBM Plex Mono"
        letterSpacing="2" textAnchor="start" style={labelHalo}>AZ E</text>
      <text x={cx} y={cy + R - 1} fill="var(--text-dim)" fontSize={10} fontFamily="IBM Plex Mono"
        letterSpacing="2" textAnchor="middle" style={labelHalo}>ALT −</text>

      {/* knob-direction hints — the actionable "which way" cue, from the native
          engine's knob labels (fallback: the error's sign). Top = altitude bolt,
          right = azimuth bolt, matching the design reference. */}
      {altHint && (
        <text x={cx} y={cy - R + 1} fill="var(--accent)" fontSize={11} fontFamily="IBM Plex Mono"
          fontWeight={600} textAnchor="middle" style={labelHalo}>
          {altHint.arrow} ALT {altHint.text}
        </text>
      )}
      {azHint && (
        <text x={cx + R - 2} y={cy + 13} fill="var(--accent)" fontSize={11} fontFamily="IBM Plex Mono"
          fontWeight={600} textAnchor="end" style={labelHalo}>
          {azHint.arrow} AZ {azHint.text}
        </text>
      )}

      {/* skew vector + error dot (converges toward center as the user adjusts).
          Gated on the EASED magnitude, not the reading: this asks "is there a
          mark to draw right now", which is a question about the tween. */}
      {active && eTotal > 0.02 && (
        <>
          <line x1={cx} y1={cy} x2={dx} y2={dy} stroke="var(--accent)" strokeWidth={2} markerEnd="url(#pa-arrow)" filter="drop-shadow(0 0 4px var(--glow))" />
          <circle cx={dx} cy={dy} r={7} fill="var(--bg)" stroke="var(--accent)" strokeWidth={2} filter="drop-shadow(0 0 4px var(--glow))"
            style={{ transition: dotTrans }} />
          <circle cx={dx} cy={dy} r={2.5} fill="var(--accent)" style={{ transition: dotTrans }} />
        </>
      )}

      {/* true-pole target */}
      <circle cx={cx} cy={cy} r={3} fill="none" stroke="var(--good)" strokeWidth={1.2} />
      <circle cx={cx} cy={cy} r={1} fill="var(--good)" />
    </svg>

    {/* The rescale, said out loud a second time, for the readers who cannot see
        it. `role="img"` on the SVG makes every child presentational, so the
        announcement text above reaches no assistive tech at all, and the
        aria-label is not a live region — a rescale was silent, which the spec
        names as the failure mode. Same information, outside the graphic, in a
        polite live region so it queues behind whatever is being read. */}
    <span className="sr-only" role="status" aria-live="polite">
      {active && announce
        ? `View scale ${announce.to < announce.from ? "zoomed in" : "zoomed out"}, ${announce.from} to ${announce.to} arcminute${announce.to === 1 ? "" : "s"} full scale`
        : ""}
    </span>
    </>
  );
}
