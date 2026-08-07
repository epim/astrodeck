/* PolarSolveRing — what the solve frame is doing RIGHT NOW, over the reticle.

   A TPPA solve is up to a second of shutter and then 5-15 s of ASTAP, during
   which nothing on the Align screen moves — and a screen that changes nothing
   for 15 s reads as hung, which is why the error "hasn't shown up yet" kept
   getting reported as a fault (operator feedback 2026-08-07). The native
   driver publishes `activity` around every frame; this renders it where the
   eye already is: the reticle's top-right corner, which the reticle leaves
   empty (its zoom announce owns the top-LEFT).

   Two visually distinct states, by SHAPE and not hue (night mode turns every
   token red):

     capturing — a ring that FILLS, animation-duration set to the frame's own
                 exposure_s, so the ring closing is the shutter closing. A
                 0.3 s default fill is a blink; a 5 s rescue exposure is five
                 honest seconds. After the fill the ring sits closed until the
                 download hands over to…
     solving   — a short arc that ORBITS: indeterminate, because ASTAP's
                 runtime is unknowable from here.

   The word beneath is the state ("capturing" / "solving") — under reduced
   motion the animations still and the word carries everything. Deliberately
   NOT a live region: the quick bar's activity chip is already aria-live, and
   a second region announcing the same transition would say everything twice.
*/
import type { JSX } from "react";

/** Radius of the ring — 100.53 = 2πr is PINNED in index.css's
 *  solve-ring-fill keyframes, which cannot read attributes. Change both. */
const R = 16;
const C = 2 * Math.PI * R; // 100.53

export type SolveActivity = "exposing" | "solving" | null | undefined;

export default function PolarSolveRing({ activity, exposureS }: {
  activity: SolveActivity;
  /** The live solve exposure (solve_settings.exposure_s) — the fill's clock. */
  exposureS: number;
}): JSX.Element | null {
  if (!activity) return null;
  const capturing = activity === "exposing";
  return (
    <div
      className="absolute top-2 right-2 flex flex-col items-center gap-0.5 pointer-events-none"
      data-solve-ring={capturing ? "capturing" : "solving"}
    >
      {/* key remounts the SVG on each state change so the fill animation
          restarts from zero for every new frame (exposing→solving→exposing). */}
      <svg
        key={activity}
        viewBox="0 0 40 40" width={40} height={40} aria-hidden
        className={`-rotate-90 ${capturing ? "" : "solve-ring-spin"}`}
      >
        {/* the track: always a full faint circle, so a stilled (reduced-motion)
            or just-started ring still reads as an instrument, not a glitch */}
        <circle cx="20" cy="20" r={R} fill="none"
          stroke="var(--line-bright)" strokeWidth={3} />
        {capturing ? (
          <circle cx="20" cy="20" r={R} fill="none"
            className="solve-ring-fill"
            stroke="var(--accent)" strokeWidth={3} strokeLinecap="butt"
            strokeDasharray={C.toFixed(2)}
            style={{ strokeDashoffset: C, animationDuration: `${Math.max(0.2, exposureS)}s` }}
          />
        ) : (
          <circle cx="20" cy="20" r={R} fill="none"
            stroke="var(--accent)" strokeWidth={3} strokeLinecap="butt"
            strokeDasharray={`${(C * 0.28).toFixed(2)} ${C.toFixed(2)}`}
          />
        )}
      </svg>
      <span className="text-[10px] tracking-widest uppercase text-dim leading-none">
        {capturing ? "capturing" : "solving"}
      </span>
    </div>
  );
}
