// FlipTile.tsx - the MERIDIAN FLIP vitals tile.
//
// THE DECISION LOGIC IS TRANSCRIBED, NOT REDERIVED, from
// `views/MonitorView.tsx:1241-1300` (`MeridianCountdown`), including the
// `status === "due"` branch and the comment that explains why it is calm. Every
// other tile on this band can be re-thought; this one cannot, because it is the
// one indicator that must never cry wolf.

import type { JSX } from "react";
import type { MeridianInfo } from "../../../../types";
import { accessPhrase } from "../../../../lib/caps";
import { fmtDuration } from "../../../lib/format";
import { ReadoutTile, type Tone } from "../../../ui";

/** Window after the crossing in which a flip really can still be in flight.
 *  (`MonitorView.tsx:1239` FLIP_IN_FLIGHT_S) */
export const FLIP_IN_FLIGHT_S = 300;

/** THE ONE SENTENCE for a flip clock a principal is not allowed to see, shared
 *  by all three surfaces that render one (this tile, `session/now/VitalsBand`'s
 *  FLIP cell and the Safety sheet's meridian line).
 *
 *  `meridian.hours_to_flip` is computed from the site: `lst - ra_hours` inverts
 *  to the rig's longitude to within ~120 m, which is the leak the 2.9 km audit
 *  recovery exists to stop. So the server now nulls the countdown and collapses
 *  `meridian.status` to "unknown" for a principal without `view.site_derived`.
 *  Rendered raw, that reads as "the mount does not report a flip" - a statement
 *  about the HARDWARE, which is false, and which sends someone to check a mount
 *  that is working. The withholding is the truth, so say it. */
export const FLIP_SITE_REASON = `flip clock needs ${accessPhrase("view.site_derived")}`;

export interface TileFace {
  value: string;
  sub: string;
  tone: Tone;
}

/** Pier clause for the sub-line, dropped when the mount does not know its side
 *  - "pier unknown" is not information, it is the absence of it. */
function pier(side: MeridianInfo["pier_side"] | undefined): string {
  return side && side !== "unknown" ? ` · pier ${side}` : "";
}

export function flipFace(
  meridian: MeridianInfo | null | undefined,
  runActive: boolean,
  canSiteDerived: boolean,
): TileFace {
  // FIRST, and before any reading of the payload: what a non-holder receives is
  // a redacted block, not a mount that has nothing to say, and every branch
  // below would describe the wrong thing. Same shape as `dawnFace` two tiles
  // along, which withholds on the same capability for the same reason.
  if (!canSiteDerived) {
    return { value: "--", sub: FLIP_SITE_REASON, tone: "dim" };
  }
  if (!meridian) {
    return { value: "flip n/a", sub: "the mount reports no flip data", tone: "dim" };
  }
  const { status, hours_to_flip, pier_side, flip_enabled } = meridian;

  if (status === "counting") {
    const secs = hours_to_flip != null ? Math.max(0, hours_to_flip * 3600) : null;
    if (secs == null) {
      return { value: "flip n/a", sub: `no countdown reported${pier(pier_side)}`, tone: "dim" };
    }
    const soon = secs <= 300;
    return {
      value: fmtDuration(secs),
      sub: `${soon ? "FLIP SOON · " : ""}${flip_enabled ? "auto" : "flip disabled"}${pier(pier_side)}`,
      tone: soon ? "warn" : "accent",
    };
  }

  // UX-2026-07-26 #21. `status: "due"` is RAW HOUR-ANGLE GEOMETRY: it says the
  // meridian crossing is behind us (hours_to_flip <= 0), not that the sequencer
  // owes a flip. The engine arms `_flip_armed` only for a target ACQUIRED EAST
  // of the meridian and disarms it the moment it flips, so a negative countdown
  // means either "we already flipped" or "this target was acquired west and was
  // never armed" - nothing is owed either way. The old tile clamped the
  // countdown at zero, which drove CountdownTile's `due` branch, so a target
  // sitting west rendered a red blinking "FLIP DUE" for the whole ~12 h it
  // stayed there - a permanent alarm on the one indicator that must never cry
  // wolf (pro + designer, three runs, ~90 frames of log with no flip in them).
  //
  // Trade-off, stated: if a flip were genuinely armed and the mount then FAILED
  // to execute it, this tile would read calm. That failure is loud elsewhere
  // (the engine logs it and the run state moves), and a real alarm you can't
  // trust is worth less than no alarm at all.
  if (status === "due") {
    const agoS = hours_to_flip != null ? Math.abs(hours_to_flip) * 3600 : null;
    const inFlight = runActive && agoS != null && agoS <= FLIP_IN_FLIGHT_S;
    if (inFlight) {
      return { value: "CROSSING NOW", sub: `flip if armed${pier(pier_side)}`, tone: "warn" };
    }
    return {
      value: "no flip owed",
      sub: agoS != null
        ? `meridian passed ${fmtDuration(agoS)} ago${pier(pier_side)}`
        : `meridian passed${pier(pier_side)}`,
      tone: "dim",
    };
  }

  // UX-2026-07-26 #21: `flip_disabled` is emitted whenever the ACTIVE PLAN
  // doesn't ask for a flip - and `hub._plan_flip_enabled()` returns False when
  // there is no plan at all, so an idle rig with nothing loaded rendered an
  // amber "FLIP DISABLED - risk near meridian" permanently. The risk it names is
  // real only while something is driving the mount across the meridian.
  if (status === "flip_disabled") {
    if (!runActive) {
      return { value: "not scheduled", sub: "no run - flip not scheduled", tone: "dim" };
    }
    return { value: "FLIP DISABLED", sub: "pier risk near the meridian", tone: "warn" };
  }

  // `n_a_over_pole` reads calm on purpose. It is not a disabled flip and not a
  // mount that cannot answer: it is a target whose tube never swings down toward
  // the pier, so there is nothing owed and nothing at risk. Saying WHY keeps it
  // from looking like the flip quietly stopped working.
  if (status === "n_a_fork") {
    return { value: "no flip needed", sub: "fork mount", tone: "dim" };
  }
  if (status === "n_a_over_pole") {
    return { value: "no flip needed", sub: "the target stays above the pole", tone: "dim" };
  }
  return { value: "flip n/a", sub: "the mount does not report a flip", tone: "dim" };
}

export function FlipTile({ meridian, runActive, canSiteDerived }: {
  meridian: MeridianInfo | null | undefined;
  runActive: boolean;
  /** `view.site_derived`. Passed in rather than read here, so the band that
   *  already asks the question for TO DAWN asks it once. */
  canSiteDerived: boolean;
}): JSX.Element {
  const face = flipFace(meridian, runActive, canSiteDerived);
  return (
    <ReadoutTile
      label="MERIDIAN FLIP"
      value={face.value}
      sub={face.sub}
      tone={face.tone}
      data-testid="vital-flip"
    />
  );
}
