// How much clipping is worth telling the operator about.

/** Fraction of the frame that must be railed before "stars saturated" is news.
 *
 *  Every deep-sky sub rails something — the brightest star core in a 60s frame
 *  is saturated on any sane exposure, and shortening the exposure to avoid it
 *  would throw away the faint signal the sub exists to collect. The advice
 *  "shorten exposure or lower gain" is only right when clipping has spread far
 *  enough to be eating real stars.
 *
 *  Calibration point: the NGC 7129 B sub measured 169 clipped px of 26,108,352
 *  (0.0006%) on a night whose frames were good. 0.02% of that sensor is ~5,200
 *  px — roughly a hundred blown star cores — which is a field worth acting on. */
export const CLIPPED_WARN_FRAC = 0.0002;

/** Absolute floor, so a small ROI or a binned preview cannot trip on a handful
 *  of pixels just because the frame is small. */
const CLIPPED_WARN_MIN = 500;

/** Clipped-pixel count at or above which the warning is worth showing, for a
 *  frame of `dataWidth` x `dataHeight` sensor pixels. */
export function clippedFloor(dataWidth: number, dataHeight: number): number {
  const n = Math.max(0, dataWidth) * Math.max(0, dataHeight);
  return Math.max(CLIPPED_WARN_MIN, Math.round(n * CLIPPED_WARN_FRAC));
}
