// Where the per-star marker goes. Extracted so the geometry is testable without
// a DOM — the defect it fixes was pure arithmetic, and the FIRST attempt at it
// was wrong in a way only arithmetic against the real numbers exposes.

/** How far a stretched star's visible disc reaches, in units of its HFR.
 *
 *  HFR is the HALF-flux radius: half the light is inside it. On a screen-stretched
 *  frame the disc runs to roughly 2.6x HFR before falling into the noise. */
export const VISIBLE_EDGE_HFR = 2.6;

/** Clear space between that edge and the ring, in units of HFR. Small — the ring
 *  must still read as belonging to THIS star in a crowded field. */
const CLEARANCE_HFR = 0.4;

/** Smallest marker, in SCREEN pixels — a cold-thumb tap target.
 *
 *  THIS USED TO BE IN DISPLAY UNITS AND THAT WAS THE BUG. The overlay's viewBox
 *  is the display image (PreviewStage: `0 0 dispW dispH`) and zoom is a CSS
 *  transform on the layer, so a radius in display units grows with the zoom.
 *  On this rig displayScale is 1400/6252 = 0.224, which makes an in-focus star
 *  0.8 display units — so a 4-unit floor beat the star-sized term for every
 *  star with HFR below 5.95px, i.e. all of them, and then scaled up with the
 *  image to sit exactly on the star's rendered edge at pixel-peep zoom. Both
 *  the old 1.6x factor and its replacement were floored to the same 4 and the
 *  first fix changed nothing at all (reported from the rig 2026-08-19).
 *
 *  Dividing by the zoom keeps the tap target constant on screen: it still
 *  protects tiny stars at fit, and it gets out of the way when the operator
 *  zooms in to look at one. */
const MIN_RADIUS_SCREEN_PX = 4;

/** Ring radius in DISPLAY units for a star of `hfr` (data px).
 *
 *  `zoom` is the stage's current scale — the same value StarOverlay already
 *  takes for decimation. Defaulting it to 1 keeps the fit-zoom behaviour of the
 *  original code for any caller that does not pass it. */
export function markerRadius(hfr: number, displayScale: number,
                             zoom: number = 1): number {
  const fromStar = hfr * displayScale * (VISIBLE_EDGE_HFR + CLEARANCE_HFR);
  const tapFloor = MIN_RADIUS_SCREEN_PX / Math.max(zoom, 0.05);
  return Math.max(fromStar, tapFloor);
}
