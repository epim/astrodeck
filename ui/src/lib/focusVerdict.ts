// focusVerdict.ts — is this frame in focus, or so far out that HFR is a lie?
//
// detect_stars measures inside a 15px box, so its HFR SATURATES around 5-7px no
// matter how defocused the frame is. On 2026-07-31 an 880px donut field —
// annuli with the secondary obstruction and spider vanes plainly visible —
// reported "median HFR 4.5px" and 1393 "stars", and the Focus panel called it
// FAIR. A number that cannot exceed 7 cannot describe a 440px blob.
//
// The server publishes defocus_r80 from imaging/defocus.py, which has no such
// ceiling. When it says the blob is large, that outranks HFR.

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

export type FocusState =
  | { kind: "no-frame" }
  /** Too big for HFR to describe, but within reach of an autofocus sweep. */
  | { kind: "soft"; r80: number }
  /** Beyond a sweep's reach — coarse focus first. */
  | { kind: "defocused"; r80: number }
  | { kind: "few-stars" }
  | { kind: "measured"; hfr: number };

/**
 * What the Focus panel should say about a frame.
 *
 * Order matters: blob size is checked FIRST, because once the star outgrows the
 * measurement box both the star count and the HFR are artefacts — the count is
 * ring fragments and the HFR is the box's ceiling. Reporting "FAIR" there is
 * worse than reporting nothing, because it tells the user to stop adjusting.
 */
export function focusState(p: {
  hfr?: number | null;
  stars?: number | null;
  defocus_r80?: number | null;
} | null | undefined): FocusState {
  if (!p) return { kind: "no-frame" };
  const r80 = p.defocus_r80;
  if (r80 != null && r80 > DEFOCUS_COARSE_R80_PX) return { kind: "defocused", r80 };
  if (r80 != null && r80 > HFR_MEANINGLESS_R80_PX) return { kind: "soft", r80 };
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
