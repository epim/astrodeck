// focusVerdict.ts — is this frame in focus, or so far out that HFR is a lie?
//
// detect_stars measures inside a 15px box, so its HFR SATURATES around 5-7px no
// matter how defocused the frame is. On 2026-07-31 an 880px donut field —
// annuli with the secondary obstruction and spider vanes plainly visible —
// reported "median HFR 4.5px" and 1393 "stars", and the Focus panel called it
// FAIR. A number that cannot exceed 7 cannot describe a 440px blob.
//
// The server now publishes defocus_r80 from imaging/defocus.py, which has no
// such ceiling. When it says the blob is enormous, that outranks HFR.

/** Above this the frame is not "soft", it is a defocus donut and every
 *  HFR-derived judgement about it is meaningless. Matches the server's
 *  HANDOVER_R80_PX (imaging/defocus.py) with margin: below the handover bar the
 *  V-curve owns the verdict, and this only speaks up well past it. */
export const DEFOCUS_R80_LIE_PX = 25;

export type FocusState =
  | { kind: "no-frame" }
  | { kind: "defocused"; r80: number }
  | { kind: "few-stars" }
  | { kind: "measured"; hfr: number };

/**
 * What the Focus panel should say about a frame.
 *
 * Order matters: grossly-defocused is checked FIRST, because in that state both
 * the star count and the HFR are artefacts — the count is ring fragments and
 * the HFR is the measurement box's ceiling. Reporting "FAIR" there is worse
 * than reporting nothing, because it tells the user to stop adjusting.
 */
export function focusState(p: {
  hfr?: number | null;
  stars?: number | null;
  defocus_r80?: number | null;
} | null | undefined): FocusState {
  if (!p) return { kind: "no-frame" };
  const r80 = p.defocus_r80;
  if (r80 != null && r80 > DEFOCUS_R80_LIE_PX) {
    return { kind: "defocused", r80 };
  }
  const hfr = p.hfr;
  if ((p.stars ?? 0) < 3 || hfr == null) return { kind: "few-stars" };
  return { kind: "measured", hfr };
}

/** The sentence for a grossly-defocused frame. Says the SIZE, because "very
 *  defocused" gives the user no idea whether they are one turn out or twenty. */
export function defocusMessage(r80: number): string {
  return `Blob is ${Math.round(2 * r80)} px across — far outside focus. `
       + "Run coarse focus; star measurements mean nothing at this size.";
}
