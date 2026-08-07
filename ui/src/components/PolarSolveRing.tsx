/* PolarSolveRing — what the solve frame is doing RIGHT NOW, over the reticle.

   A TPPA solve is up to a second of shutter and then 5-15 s of ASTAP, during
   which nothing on the Align screen moves — and a screen that changes nothing
   for 15 s reads as hung, which is why the error "hasn't shown up yet" kept
   getting reported as a fault (operator feedback 2026-08-07). The native
   driver publishes `activity` around every frame; this renders it where the
   eye already is: the reticle's top-right corner, which the reticle leaves
   empty (its zoom announce owns the top-LEFT).

   The ring itself is the shared ActivityRing (capturing = fill on the frame's
   own exposure clock; solving = indeterminate orbit); this wrapper only owns
   the polar vocabulary and the corner placement. */
import type { JSX } from "react";
import ActivityRing from "./ui/ActivityRing";

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
      className="absolute top-2 right-2 pointer-events-none"
      data-solve-ring={capturing ? "capturing" : "solving"}
    >
      <ActivityRing
        mode={capturing ? "fill" : "orbit"}
        seconds={exposureS}
        word={capturing ? "capturing" : "solving"}
        resetKey={activity}
      />
    </div>
  );
}
