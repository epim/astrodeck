// Where the per-star marker goes. Extracted so the geometry is testable without
// a DOM — the defect it fixes was pure arithmetic.

/** How far a stretched star's visible disc reaches, in units of its HFR.
 *
 *  HFR is the HALF-flux radius: half the star's light is inside it. On a
 *  screen-stretched frame the disc keeps going well past that — about 2.5-3x
 *  HFR before it falls into the noise. A marker drawn at 1.6x HFR therefore
 *  lands INSIDE the star, and with a dark `--halo` under-stroke it cuts a band
 *  through the core. Reported from the rig 2026-08-18 at 282% zoom as "the
 *  stars are pretty doughnut-y"; the pixels were fine (FWHM 3.34px, no central
 *  dip in any of 12 profiles) and this ring was the whole effect. */
export const VISIBLE_EDGE_HFR = 2.6;

/** Clear space between the visible disc and the ring, in units of HFR. Small —
 *  the ring must still read as belonging to THIS star in a crowded field. */
const CLEARANCE_HFR = 0.4;

/** Smallest marker in display px: a cold-thumb tap target, not a measurement.
 *  At fit-zoom this dominates for every star, so zoomed-out views are
 *  unchanged by the clearance above. */
const MIN_RADIUS_PX = 4;

/** Ring radius in DISPLAY units for a star of `hfr` (data px). */
export function markerRadius(hfr: number, displayScale: number): number {
  return Math.max(hfr * displayScale * (VISIBLE_EDGE_HFR + CLEARANCE_HFR),
                  MIN_RADIUS_PX);
}
