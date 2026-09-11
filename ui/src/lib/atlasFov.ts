// atlasFov.ts — where the telescope is ACTUALLY pointing, as Atlas geometry.
//
// The Atlas could draw where you INTEND to point (FovOverlay's planned box) and
// nothing whatsoever about where the tube is. This file is the other half: a
// pure readout of live mount + rotator telemetry, projected onto the same sky
// the survey tiles are drawn on.
//
// THE RULE THAT SHAPES EVERY SIGNATURE HERE: nothing in this module knows the
// target, the planned PA, or that a slew was ever commanded. There is no
// destination to tween toward and no state to snap to. The footprint moves only
// because a NEW TELEMETRY SAMPLE said the hardware moved — so a slew that fails,
// is refused, or is aborted leaves the previous sample standing and the frame
// stays exactly where the scope actually is. Passing a target (or the session's
// rotation_deg) into `pointingFov` would destroy that guarantee: don't add it.
// `atlasFov.test.ts` fails the moment it does.
//
// Screen mapping is the Atlas's own — North-up, East-LEFT:
//     x = view/2 − ξ·pxPerDeg,  y = view/2 − η·pxPerDeg
// byte-identical to lib/tileView.tileMesh and lib/surveyView, so the footprint
// is glued to the survey pixels underneath it through every pan and zoom rather
// than to the viewport. That mapping exists only on the hemisphere around the
// view centre — see TAN_HORIZON_DEG for what the gnomonic does with the other
// half (it draws it MIRRORED onto this one) and why it is refused, not drawn.
//
// Epoch: everything here is J2000, which is what lib/framing.ts's projection
// demands. That is safe because the hub publishes an already-converted position
// — "The RA/Dec PUBLISHED in status are canonical J2000" (hub.py status(),
// `from_mount_frame`) — NOT the raw JNow the driver reads. Feed this raw driver
// coordinates and the footprint sits ~0.4° off the survey in 2026, which on a
// half-degree sensor is most of a frame.

import { project, deproject } from "./framing";
import { mod360 } from "./rotation";

const DEG = Math.PI / 180;

export interface SkyPoint {
  ra_hours: number;
  dec_deg: number;
}

export interface ViewPoint {
  x: number;
  y: number;
}

/** The Atlas canvas as geometry: which sky point is at the middle of the square
 *  viewBox, and how many viewBox px one degree spans there. */
export interface AtlasViewGeom {
  centerRaHours: number;
  centerDecDeg: number;
  /** VIEW / fovZoomDeg — uniform, because the backdrop is a TAN projection. */
  pxPerDeg: number;
  /** viewBox edge length (SkyCanvas uses 1000). */
  view: number;
}

/** Live mount telemetry. Structurally satisfied by types.ts MountStatus — this
 *  file declares its own shape so it never has to import the app's status
 *  types, and so a test can hand it a bare sample. */
export interface MountSample {
  ra_hours: number;
  dec_deg: number;
  /** The mount's own in-motion report; only used to say "still moving". */
  slewing?: boolean;
}

/** Live rotator telemetry (types.ts RotatorStatus satisfies it). `synced` is
 *  load-bearing: an unsynced rotator's sky_deg is just its mechanical angle
 *  (devices/base.py Rotator — offset 0 until sync()), so it is NOT a sky
 *  position angle and must not be drawn as one. */
export interface RotatorSample {
  sky_deg: number;
  synced: boolean;
  moving: boolean;
}

export interface PointingInputs {
  /** null/undefined = no mount is reporting. We draw nothing, not a guess. */
  mount: MountSample | null | undefined;
  /** null/undefined = no rotator in the rig, so nothing measures camera angle. */
  rotator: RotatorSample | null | undefined;
  /** Sensor footprint from optics, degrees at bin 1. 0 = optics unknown. */
  fovXDeg: number;
  fovYDeg: number;
}

/** Why the frame's sky angle is what it is. "none" = nothing measured it, and
 *  the caller must NOT substitute the planned PA. */
export type PaSource = "rotator" | "none";

export interface PointingReadout {
  /** false = no live position at all; the caller draws nothing. */
  known: boolean;
  /** The optical axis in viewBox px, or null when there is no such px — either
   *  no position at all (`known` false) or a position on the far hemisphere,
   *  which this projection cannot place (see TAN_HORIZON_DEG). Both mean the
   *  same thing to a caller: draw nothing. */
  axis: ViewPoint | null;
  /** Sensor outline, 4 corners in viewBox px, closed cyclically. null when the
   *  outline cannot be drawn honestly — no optics (size unknown) or no measured
   *  sky angle (orientation unknown). */
  outline: ViewPoint[] | null;
  /** Radius in viewBox px of the angle-unknown disc — the circle the sensor
   *  fits inside whatever its angle. Non-null EXACTLY when the size is known
   *  and the angle is not. */
  discRPx: number | null;
  /** Sky position angle actually used, degrees. null when nothing measured it. */
  paDeg: number | null;
  paSource: PaSource;
  /** Hardware reports itself in motion right now (slewing or rotating). */
  moving: boolean;
  /** Angular distance from the Atlas view centre to the scope, degrees. This is
   *  a real great-circle distance (haversine), NOT a screen distance, so it
   *  stays meaningful past the projection horizon where screen px stop being. */
  sepDeg: number;
  /** The footprint is not on the canvas: either it projects outside the viewBox
   *  (scrolled off, exactly like any other sky object) or it is past the
   *  projection horizon and has no place on this map at all. */
  offView: boolean;
  /** What this drawing does NOT know, as a sentence. null when fully known. */
  caveat: string | null;
  /** Whether that caveat is something to FIX (optics unset, a rotator that was
   *  never synced, a dropped reading) or a NOTE about how this rig is built (no
   *  rotator at all). A permanent, correct fact about someone's rig must not be
   *  painted as a warning every night — that is how warnings stop being read. */
  caveatTone: "fix" | "note" | null;
}

// ------------------------------------------------------------- projection
/**
 * The edge of what a tangent plane can hold. A gnomonic projection covers the
 * hemisphere around its tangent point and nothing else, and it does NOT fail
 * loudly past that: `framing.project` divides by h = cos(separation), which goes
 * NEGATIVE beyond 90°, so a scope on the far side of the sky comes back MIRRORED
 * onto the near side. At the exact antipode h = −1 and (ξ,η) = (0,0) — the
 * middle of the canvas. Measured before this guard existed: mount at the
 * antipode of the view centre drew a full confident rectangle at the rotator's
 * PA, dead centre, `offView` false, no caveat. That is precisely the failure
 * this whole feature exists to refuse — the frame ARRIVING on the planned box
 * while the hardware is 180° away — so the far hemisphere is refused outright
 * rather than drawn. (Near the horizon itself, h → 0 and the coordinates blow up
 * to millions of px; those are honest, correctly-directed, and simply clip.)
 */
export const TAN_HORIZON_DEG = 90;

/** Sky → viewBox px, North-up / East-left (see header). Pure; no clamping —
 *  a point off-canvas returns a large finite number and clips, which is what
 *  "scrolled out of view" means. null = the point is at or past the projection
 *  horizon, i.e. it has no position on this map (NOT a position off its edge). */
export function skyToView(
  ra_hours: number,
  dec_deg: number,
  g: AtlasViewGeom,
): ViewPoint | null {
  const sep = angularSepDeg(
    { ra_hours, dec_deg },
    { ra_hours: g.centerRaHours, dec_deg: g.centerDecDeg },
  );
  if (!(sep < TAN_HORIZON_DEG)) return null; // !( < ) also catches NaN
  const { xi, eta } = project(ra_hours, dec_deg, g.centerRaHours, g.centerDecDeg);
  const half = g.view / 2;
  return { x: half - xi * g.pxPerDeg, y: half - eta * g.pxPerDeg };
}

/** Great-circle separation in degrees (haversine — stable at small angles,
 *  where the law-of-cosines form loses most of its digits). */
export function angularSepDeg(a: SkyPoint, b: SkyPoint): number {
  const d1 = a.dec_deg * DEG;
  const d2 = b.dec_deg * DEG;
  const dd = d2 - d1;
  const dr = (b.ra_hours - a.ra_hours) * 15 * DEG;
  const h =
    Math.sin(dd / 2) ** 2 +
    Math.cos(d1) * Math.cos(d2) * Math.sin(dr / 2) ** 2;
  return (2 * Math.asin(Math.min(1, Math.sqrt(h)))) / DEG;
}

/**
 * The four sensor corners ON THE SKY, for a frame of `fovXDeg × fovYDeg`
 * centred at `pointing` at sky position angle `paDeg`.
 *
 * Built in the FRAME's own tangent plane and deprojected there, so the corners
 * are real sky coordinates rather than a rectangle stamped on the Atlas plane.
 * That matters once the scope is far from the Atlas centre: meridians converge,
 * and a footprint at the edge of a wide view is genuinely not axis-aligned with
 * one at the middle. Reusing framing.deproject keeps one projection in the app.
 *
 * Rotation matches `mosaicGrid` exactly — ξ = gx·cosθ − gy·sinθ,
 * η = gx·sinθ + gy·cosθ — which is what makes the live footprint land ON the
 * planned box when the hardware genuinely arrives, instead of near it.
 *
 * Order is cyclic (frame right-top, left-top, left-bottom, right-bottom), so
 * consecutive points share an edge and the polygon never self-intersects.
 */
export function fovCornersSky(
  pointing: SkyPoint,
  fovXDeg: number,
  fovYDeg: number,
  paDeg: number,
): SkyPoint[] {
  const theta = paDeg * DEG;
  const cosT = Math.cos(theta);
  const sinT = Math.sin(theta);
  const hx = fovXDeg / 2;
  const hy = fovYDeg / 2;
  const corners: [number, number][] = [
    [hx, hy],
    [-hx, hy],
    [-hx, -hy],
    [hx, -hy],
  ];
  return corners.map(([gx, gy]) => {
    const xi = gx * cosT - gy * sinT;
    const eta = gx * sinT + gy * cosT;
    return deproject(xi, eta, pointing.ra_hours, pointing.dec_deg);
  });
}

// ------------------------------------------------------------- the readout
/**
 * Everything the Atlas can honestly say about where the scope is pointing right
 * now, from this telemetry sample alone.
 *
 * Three separate things can be unknown and they are kept separate, because each
 * has a different fix and lumping them produced the "green while wrong" class of
 * bug this whole night was about:
 *   • position unknown (no mount) → nothing is drawn;
 *   • size unknown (no optics)    → the axis is marked, no outline is invented;
 *   • angle unknown (no rotator, or a rotator that has never been synced to the
 *     sky) → the disc is drawn instead of a rectangle. The planned PA is NEVER
 *     substituted: assuming the camera is at the angle you asked for is exactly
 *     the lie that makes a frame "arrive" when the hardware didn't.
 */
export function pointingFov(inp: PointingInputs, g: AtlasViewGeom): PointingReadout {
  const nothing: PointingReadout = {
    known: false,
    axis: null,
    outline: null,
    discRPx: null,
    paDeg: null,
    paSource: "none",
    moving: false,
    sepDeg: 0,
    offView: false,
    caveat: "No mount is reporting a position, so nothing here knows where the scope is pointing.",
    caveatTone: "note",
  };
  const m = inp.mount;
  if (!m || !Number.isFinite(m.ra_hours) || !Number.isFinite(m.dec_deg)) return nothing;

  const here: SkyPoint = { ra_hours: m.ra_hours, dec_deg: m.dec_deg };
  // Real great-circle distance, computed BEFORE any projection and never from
  // screen px — it is the only honest number left once the scope is past the
  // projection horizon, where there is no screen px to measure.
  const sepDeg = angularSepDeg(here, {
    ra_hours: g.centerRaHours,
    dec_deg: g.centerDecDeg,
  });
  // null = the scope is somewhere real but not on THIS map (see TAN_HORIZON_DEG).
  const axis = skyToView(m.ra_hours, m.dec_deg, g);
  const offView =
    axis === null || axis.x < 0 || axis.x > g.view || axis.y < 0 || axis.y > g.view;

  const sizeKnown = inp.fovXDeg > 0 && inp.fovYDeg > 0;
  const rot = inp.rotator;
  // A rotator that has never been synced reports mechanical degrees dressed as
  // sky degrees (offset 0). Reading that as a position angle would point the
  // drawn frame confidently in a direction nobody measured. A non-finite angle
  // (a truncated/failed status payload) is the same claim — unknown, not zero.
  const paKnown = !!rot && rot.synced && Number.isFinite(rot.sky_deg);
  const paDeg = paKnown ? mod360(rot!.sky_deg) : null;
  const moving = !!m.slewing || !!rot?.moving;

  let caveat: string | null = null;
  let caveatTone: "fix" | "note" | null = null;
  if (!sizeKnown) {
    caveat =
      "Frame size is unknown — focal length, pixel size and sensor size aren't set, so only the axis is marked.";
    caveatTone = "fix";
  } else if (!rot) {
    caveat =
      "Camera angle isn't measured — there's no rotator in the rig, so the sensor sits at some angle inside this circle.";
    caveatTone = "note";
  } else if (!rot.synced) {
    caveat =
      "The rotator has never been synced to the sky, so its reading is mechanical only - plate-solve and sync to place the frame.";
    caveatTone = "fix";
  } else if (!paKnown) {
    caveat = "The rotator didn't report an angle in this update, so the frame's orientation is unknown right now.";
    caveatTone = "fix";
  }

  const corners =
    axis !== null && sizeKnown && paDeg !== null
      ? fovCornersSky(here, inp.fovXDeg, inp.fovYDeg, paDeg).map((c) =>
          skyToView(c.ra_hours, c.dec_deg, g),
        )
      : null;
  // All four corners or none. A footprint straddling the projection horizon —
  // axis just inside 90°, one corner just outside — has no honest polygon: the
  // missing corners do not exist on this plane, and closing the path through
  // them would draw a bow-tie across sky nobody is pointing at.
  const outline = corners && !corners.includes(null) ? (corners as ViewPoint[]) : null;

  // The circumscribed circle: whatever the camera angle turns out to be, the
  // sensor is inside this. Half the frame diagonal.
  const discRPx =
    axis !== null && sizeKnown && paDeg === null
      ? (Math.hypot(inp.fovXDeg, inp.fovYDeg) / 2) * g.pxPerDeg
      : null;

  return {
    known: true,
    axis,
    outline,
    discRPx,
    paDeg,
    paSource: paKnown ? "rotator" : "none",
    moving,
    sepDeg,
    offView,
    caveat,
    caveatTone,
  };
}

// ------------------------------------------------------------------- copy
/** Arcmin under a degree, degrees above — the Atlas's own convention. */
function fmtSep(deg: number): string {
  if (deg < 1) return `${(deg * 60).toFixed(1)}′`;
  return `${deg.toFixed(1)}°`;
}

/**
 * The sentence under the canvas. It carries the numbers the drawing cannot —
 * where the scope is, what angle was measured, how far off-view it has scrolled
 * — and never narrates what the user can already see.
 *
 * `where` is the mount's own formatted position (status.mount.ra_str/dec_str);
 * pass null if unavailable and the sentence drops it rather than inventing one.
 */
export function pointingCaption(r: PointingReadout, where: string | null): string | null {
  if (!r.known) return null; // no mount: the page's Go-to control already says so
  const at = where ? ` (${where})` : "";

  // Two different "you can't see it" claims, and collapsing them was a bug worth
  // its own sentence. Past the horizon the footprint is not off the EDGE of this
  // map — it is not on this map, and no amount of panning in the direction it
  // seems to lie will reach it. The separation is the only thing the user can
  // act on, so it leads.
  if (r.sepDeg >= TAN_HORIZON_DEG) {
    return `Scope is pointing ${fmtSep(r.sepDeg)} from this view${at} — past 90°, so it is off this map entirely, not just past its edge.`;
  }
  if (r.offView) {
    return `Scope is pointing ${fmtSep(r.sepDeg)} from this view${at} — its frame is off the edge of the map.`;
  }
  if (r.caveat) {
    return r.moving
      ? `${r.caveat} Position is live from the mount as it moves.`
      : r.caveat;
  }
  const pa = `PA ${Math.round(r.paDeg ?? 0)}° measured at the rotator`;
  return r.moving
    ? `Scope now${at}, ${pa} — this follows the mount's reported position, so it reaches the target only when the mount does.`
    : `Scope now${at}, ${pa}.`;
}
