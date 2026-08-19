// Which repair to put in front of the operator when a frame is unusable.
//
// Extracted from FocusVerdict because the ORDER is the whole behaviour and it
// was wrong: `clipped` was computed and then the `fewStars` branch returned
// before anything read it. A frame railed at full scale finds no stars, so the
// screen advised "lengthen the exposure" — the exact inverse of the repair.
// Measured on the rig 2026-08-19, cap on in daylight: MIN = MEDIAN = MEAN =
// MAX = 65535, 0 stars, that advice on screen.

export type Advice = { kind: "clipped" | "few-stars"; text: string } | null;

/** Saturation outranks few-stars BECAUSE IT CAUSES IT. A white frame has no
 *  stars for the same reason it has no dynamic range. */
export function adviceFor(f: { fewStars: boolean; clipped: boolean }): Advice {
  if (f.clipped) {
    return {
      kind: "clipped",
      text: "the frame is saturated — shorten the exposure or lower the gain",
    };
  }
  if (f.fewStars) {
    return {
      kind: "few-stars",
      text: "check focus/clouds, or lengthen the exposure",
    };
  }
  return null;
}

/** The headline word for each case. */
export function adviceLabel(kind: NonNullable<Advice>["kind"]): string {
  return kind === "clipped" ? "Frame saturated" : "Few stars";
}
