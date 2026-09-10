// slewStops.ts - the two ladders the mount sheet's tiles offer, and the one
// sentence that says what the deadman costs at the top of the first (D-RIG-4).
// Pure: no store, no fetch, no React.
//
// WHY THE RATE LADDER IS COMPUTED AND NOT A CONSTANT. `SLEW_RATES` in
// `lib/slewController.ts` tops out at 0.5 deg/s because 0.6 was the only
// ceiling the client knew: `TOUCH_MAX_RATE_DEG_S`, a conservative guess about
// somebody else's gearbox that the server applied to every mount alike. A
// driver that can say how fast it will actually slew now publishes
// `Telescope.max_rate_deg_s` (`devices/base.py:290-296`) and the server clamp
// prefers it (`hub.py:221-232`); the AM5N reports 1.44 deg/s, nearly three
// times the shipped top stop. So the ladder is built from whatever the mount
// said, and `null` - WHICH MEANS THE DRIVER DID NOT SAY, not "no limit" -
// returns exactly the three stops that shipped.
//
// The step ladder exists because `POST /api/mount/nudge` is the first
// per-tap arcminute verb this UI has ever had (it closes deviation E9). Its
// stops are bounded by `parse_nudge`'s own 1..600, so a stop can never earn a
// 422: the picker cannot ask for something the route will refuse.

import type { SlewRateOption } from "../../../../types";
import { SLEW_RATES, TOUCH_MAX_RATE_DEG_S } from "../../../../lib/slewController";

/** `mount_offset.py:20-28`'s MIN_NUDGE_ARCMIN / MAX_NUDGE_ARCMIN, which
 *  `api/mount.ts` also exports as `NUDGE_MIN_ARCMIN`/`NUDGE_MAX_ARCMIN`.
 *
 *  Written out again rather than imported from there, and the reason is
 *  mechanical: `api/mount.ts` reaches `api.ts`, which reads `window.location`
 *  at module load, and this module has to stay importable with no DOM at all -
 *  it is graded by a pure test, and a ladder that can only be checked inside
 *  jsdom is a ladder nobody checks. The test beside it pins these two against
 *  the wrapper's own constants, so the two copies cannot drift apart quietly. */
const MIN_STEP_ARCMIN = 1;
const MAX_STEP_ARCMIN = 600;

/** The server's move-axis deadman window, `hub.py:233 MOVE_DEADMAN_MS`. Held
 *  here as well because the sentence below multiplies it by the ceiling, and a
 *  worst-case travel figure that quietly stops matching the server's window is
 *  worse than no figure at all. */
export const MOVE_DEADMAN_MS = 1200;

/** Two adjacent stops closer than this ratio are one stop as far as a thumb is
 *  concerned. It is what decides whether the half-ceiling stop below is worth
 *  offering: on a 1.44 deg/s mount the gap from the shipped top (0.5) to the
 *  ceiling is a factor of nearly three and a middle stop earns its place; on a
 *  0.7 deg/s mount it would sit 0.35 from one neighbour and 0.35 from the other
 *  and teach nobody anything. */
const DISTINCT_RATIO = 1.4;

/** A rate as the label under a dial stop. NEVER ROUNDS UP: the top stop's label
 *  is what an operator reads as the mount's ceiling, and a mount labelled
 *  faster than it is will be believed. Two decimals, trailing zeros dropped, so
 *  1.44 reads as `1.44 deg/s` and 1 as `1 deg/s` - one number for one thing,
 *  the same one the deadman sentence below uses. */
export function rateStopLabel(degS: number): string {
  const floored = Math.floor(degS * 100) / 100;
  return `${String(floored)} deg/s`;
}

/** The rate ladder for a mount that reports `maxRateDegS` deg/s.
 *
 *  `null`/`undefined`/a nonsense number returns `SLEW_RATES` ITSELF, unchanged
 *  and by identity, because that is what `#/classic` and every existing call
 *  site already offer and this module may not move them.
 *
 *  Otherwise: every shipped stop the ceiling actually allows (a mount slower
 *  than 0.5 deg/s must not be offered 0.5), then the half-ceiling stop where it
 *  is distinguishable, then the ceiling. No stop above the ceiling ever
 *  survives - the server would clamp it, and a control that shows a number the
 *  rig silently replaces is the defect this whole decision exists to end. */
export function slewStops(maxRateDegS: number | null | undefined): SlewRateOption[] {
  if (maxRateDegS == null || !Number.isFinite(maxRateDegS) || maxRateDegS <= 0) {
    return SLEW_RATES;
  }
  const ceiling = maxRateDegS;
  const out = SLEW_RATES.filter((r) => r.rateDegS <= ceiling);
  const top = () => out[out.length - 1].rateDegS;

  // `id` stays `"set"` for both: `SlewRateId` in `types.ts` is a closed union of
  // three and this task does not own that file. Nothing here looks a stop up by
  // id (the tile and the pad address stops by INDEX), and `rateGlyph` reads the
  // id only to size its shape glyph, where "one of the fast ones" is what it
  // means anyway - the label carries the number.
  const half = ceiling / 2;
  if (half >= top() * DISTINCT_RATIO && ceiling >= half * DISTINCT_RATIO) {
    out.push({ id: "set", label: rateStopLabel(half), rateDegS: half });
  }
  if (ceiling >= top() * DISTINCT_RATIO) {
    out.push({ id: "set", label: rateStopLabel(ceiling), rateDegS: ceiling });
  }
  return out;
}

/** One stop on the RA STEP / DEC STEP ladders. */
export interface NudgeStop {
  /** Arcminutes, always inside `parse_nudge`'s 1..600. */
  arcmin: number;
  /** `10'` below a degree, `1 deg` at and above it. The prime matches
   *  `fmtArcmin` in the same sheet, which is the other place this screen prints
   *  an arcminute. */
  label: string;
}

/** The ladder, coarse where a finger is coarse: single arcminutes where a
 *  centring correction lives, then jumps, because the difference between 300
 *  and 310 arcminutes is not a thing anybody means. Bounded at both ends by the
 *  route's own limits (`NUDGE_MIN_ARCMIN`/`NUDGE_MAX_ARCMIN`), asserted rather
 *  than assumed by the test beside this file. */
const LADDER = [1, 2, 5, 10, 20, 30, 60, 120, 300, 600];

export function nudgeStopLabel(arcmin: number): string {
  return arcmin >= 60 ? `${arcmin / 60} deg` : `${arcmin}′`;
}

export function nudgeStops(): NudgeStop[] {
  return LADDER
    .filter((a) => a >= MIN_STEP_ARCMIN && a <= MAX_STEP_ARCMIN)
    .map((arcmin) => ({ arcmin, label: nudgeStopLabel(arcmin) }));
}

/** The ceiling the CLIENT will actually slew at, mirroring the server clamp
 *  `getattr(tel, "max_rate_deg_s", None) or TOUCH_MAX_RATE_DEG_S` - including
 *  its `or`, so a driver that reports 0.0 falls back exactly as the server
 *  does rather than pinning the pad at zero. */
export function effectiveCeiling(maxRateDegS: number | null | undefined): number {
  return maxRateDegS != null && Number.isFinite(maxRateDegS) && maxRateDegS > 0
    ? maxRateDegS
    : TOUCH_MAX_RATE_DEG_S;
}

/** A number with up to `dp` decimals and no trailing zeros: 1.728 -> `1.73`,
 *  0.6 -> `0.6`. */
function trim(v: number, dp: number): string {
  return String(Number(v.toFixed(dp)));
}

/** What the deadman costs AT THIS MOUNT'S CEILING (controller ruling 5).
 *
 *  Both numbers are computed, never written down: the whole point of shipping
 *  the driver's ceiling is that it differs per mount, and a sentence that said
 *  "1.73 degrees" on a mount that tops out at 0.6 would be a promise nothing
 *  keeps. At the AM5N's 1.44 deg/s it reads exactly as the ruling wrote it. */
export function deadmanNote(maxRateDegS: number | null | undefined): string {
  const ceiling = effectiveCeiling(maxRateDegS);
  const seconds = trim(MOVE_DEADMAN_MS / 1000, 1);
  const travel = trim(ceiling * (MOVE_DEADMAN_MS / 1000), 2);
  return `If the link drops mid-slew the rig stops the mount within ${seconds} seconds`
    + ` - at ${trim(ceiling, 2)} deg/s that is up to ${travel} degrees of travel.`;
}

/** The line under the SLEW RATE tile that says where the ceiling came from.
 *  A mount that did not say is NOT a mount with no limit, and the number it is
 *  held at is the server's own fallback, so the sentence names it. */
export function ceilingNote(maxRateDegS: number | null | undefined): string {
  return maxRateDegS != null && Number.isFinite(maxRateDegS) && maxRateDegS > 0
    ? `The mount reports ${trim(maxRateDegS, 2)} deg/s as its fastest slew.`
    : `This mount did not report a ceiling - held at ${trim(TOUCH_MAX_RATE_DEG_S, 2)} deg/s.`;
}
