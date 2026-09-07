// focusVerdict.ts — is this frame in focus, or so far out that HFR is a lie?
//
// detect_stars measures inside a 15px box, so its HFR SATURATES around 5-7px no
// matter how defocused the frame is. On 2026-07-31 an 880px donut field —
// annuli with the secondary obstruction and spider vanes plainly visible —
// reported "median HFR 4.5px" and 1393 "stars", and the Focus panel called it
// FAIR. A number that cannot exceed 7 cannot describe a 440px blob.
//
// The server publishes defocus_r80 from imaging/defocus.py, which has no such
// ceiling. When it says the blob is large, that outranks HFR — UNLESS the frame
// also carries a resolved star population, which is the 2026-09-07 repair
// below.

// The two bars below are MEASURED, not chosen. Both come from the ground-truth
// sweep committed at server/tests/fixtures/focus_sweep (one rich field, true
// focus 9900), run through the current measure_blob:
//
//     offset from focus:     0    ±300    ±600   ±1000   ±2000
//     r80 (px):            6.0   26-34   58-62   86-94   50-94*
//                                                        (*signal failing)
//
// A default autofocus sweep reaches ±4×350 = ±1400 steps, i.e. everything up to
// about r80 90-100. That is why ONE threshold could not do both jobs.

/** Above this, HFR is measured inside a box smaller than the star and stops
 *  meaning anything — so it must not be the verdict. Deliberately equal to the
 *  server's HANDOVER_R80_PX (imaging/defocus.py): the same bar at which coarse
 *  focus hands over to the V-curve. */
export const HFR_MEANINGLESS_R80_PX = 12;

/** Above this the frame is beyond what a default autofocus sweep can bracket,
 *  so coarse focus is the honest instruction.
 *
 *  It was 25, chosen against the OLD measure_blob (80%-encircled flux in a
 *  1200px window), which read 445px on a frame of 200 sharp stars. The metric
 *  was replaced; nobody re-derived the bar. Against the new one, 25 fires at
 *  ±300 focuser steps — ONE autofocus step from perfect — and sent the user to
 *  coarse focus from a position an ordinary sweep fixes in a minute. */
export const DEFOCUS_COARSE_R80_PX = 100;

/** Detections a frame needs before its star population outranks a blob.
 *
 *  A floor, not a discriminator, and the fixtures say why it cannot be one: the
 *  real donut frame L_0001 yields 200 detections (rim fragments) while a clean
 *  512px crop yields 19, so no count separates the two. What separates them is
 *  the HFR bar below. 30 is set where a preview always clears it — a full frame
 *  reports the detector's 200-star cap — while a handful of coincidental
 *  detections cannot vote. */
export const RESOLVED_MIN_STARS = 30;

/** And a box HFR the 15px box can still faithfully describe. Equal to the
 *  server's SIZE_FINE_MAX_BOX_HFR (imaging/stars.py), where the same question
 *  is decided on the pixels themselves.
 *
 *  MEASURED on the 2026-09-05/06 frames: every clean frame grades 2.61-3.43 and
 *  every defocused one grades 3.94-5.00, because past this point the box is
 *  measuring its own geometry. Anything above the bar therefore keeps the
 *  blob's vote — a donut field looks like "hundreds of stars at HFR 4.5" and
 *  must never be allowed to talk its way out of the defocus verdict. */
export const RESOLVED_MAX_HFR = 3.8;

export type FocusState =
  | { kind: "no-frame" }
  /** Too big for HFR to describe, but within reach of an autofocus sweep. */
  | { kind: "soft"; r80: number }
  /** Beyond a sweep's reach — coarse focus first. */
  | { kind: "defocused"; r80: number }
  | { kind: "few-stars" }
  | { kind: "measured"; hfr: number };

/**
 * Does this frame carry stars the optics have plainly resolved?
 *
 * THE 2026-09-07 DEFECT. A 60s L sub of NGC 604 that the run's own grader read
 * at HFR 3.32 with 1294 stars was shown as "Far out of focus — blob is 2268 px
 * across — further out than an autofocus sweep can bracket. Run coarse focus
 * first", because the server's blob measurer had locked onto M33's extended
 * light and this file let any large blob outrank HFR. A frame with a healthy
 * star population is not far out of focus, whatever a blob measurer says.
 *
 * The server now declines to publish defocus_r80 at all on such a frame
 * (imaging/stars.compact_star_population, which screens the actual pixels).
 * This is the second layer, for an older server and for a blob that clears the
 * server's gates on a frame the star count plainly contradicts.
 */
export function resolvedStarField(p: {
  hfr?: number | null;
  stars?: number | null;
} | null | undefined): boolean {
  if (!p) return false;
  const hfr = p.hfr;
  return (p.stars ?? 0) >= RESOLVED_MIN_STARS
    && hfr != null && hfr > 0 && hfr < RESOLVED_MAX_HFR;
}

/**
 * What the Focus panel should say about a frame.
 *
 * Blob size is checked before HFR, because once the star outgrows the
 * measurement box both the star count and the HFR are artefacts — the count is
 * ring fragments and the HFR is the box's ceiling. Reporting "FAIR" there is
 * worse than reporting nothing, because it tells the user to stop adjusting.
 *
 * But it is checked SECOND to the star population, because that inversion is
 * only true where the box is lying: on a frame whose stars are resolved and
 * compact, HFR is the faithful measurement and the blob is describing
 * something that is not the PSF.
 */
export function focusState(p: {
  hfr?: number | null;
  stars?: number | null;
  defocus_r80?: number | null;
} | null | undefined): FocusState {
  if (!p) return { kind: "no-frame" };
  const r80 = p.defocus_r80;
  if (!resolvedStarField(p)) {
    if (r80 != null && r80 > DEFOCUS_COARSE_R80_PX) return { kind: "defocused", r80 };
    if (r80 != null && r80 > HFR_MEANINGLESS_R80_PX) return { kind: "soft", r80 };
  }
  const hfr = p.hfr;
  if ((p.stars ?? 0) < 3 || hfr == null) return { kind: "few-stars" };
  return { kind: "measured", hfr };
}

/** The sentence for a frame whose stars have outgrown the HFR box.
 *
 *  Says the SIZE, because "very defocused" gives no idea whether you are one
 *  turn out or twenty — and names the tool that can actually fix THIS distance,
 *  which is the difference the two thresholds exist to draw. */
export function defocusMessage(r80: number): string {
  const across = Math.round(2 * r80);
  return r80 > DEFOCUS_COARSE_R80_PX
    ? `Blob is ${across} px across — further out than an autofocus sweep can `
      + "bracket. Run coarse focus first; star measurements mean nothing at "
      + "this size."
    : `Stars are ${across} px across — too big for HFR to describe, but well `
      + "within an autofocus sweep. Run autofocus.";
}
