// filterSlots.ts — the naming rule behind the blackout checkbox.
//
// The name and the blackout flag were independent fields: you ticked "blackout"
// and then separately typed a name, and nothing stopped the dark slot being
// called "L". That name is not decoration — it lands in the FITS FILTER header
// and in the saved-filename token, so darks would file themselves under
// whatever the slot used to be.

/** The name a blackout slot takes. Upper case because it is a frame-type
 *  token in a filename, and the other tokens are too. */
export const DARK_SLOT_NAME = "DARK";

/** True when `name` is a placeholder rather than something a person chose:
 *  empty, or the "Slot N" the wheel reports when a position is unset. */
export function isPlaceholderName(name: string, index: number): boolean {
  const n = (name ?? "").trim();
  return n === "" || n.toLowerCase() === `slot ${index + 1}`;
}

/**
 * The name after ticking or unticking blackout on slot `index`.
 *
 * Ticking fills in DARK only when the current name is a placeholder — silently
 * overwriting a name somebody deliberately typed is the one variant to avoid,
 * and someone who wants "DARK 2" must be able to keep it.
 *
 * Unticking restores what was there before, because leaving DARK on a slot that
 * now passes light is exactly the mislabel this is meant to prevent.
 */
export function nameForOpaqueToggle(
  current: string,
  index: number,
  nowOpaque: boolean,
  previous: string | undefined,
): string {
  if (nowOpaque) {
    return isPlaceholderName(current, index) ? DARK_SLOT_NAME : current;
  }
  // Coming back off blackout: restore the prior name if we replaced it, else
  // clear our own DARK so the slot does not keep claiming to be one.
  if (current.trim().toUpperCase() === DARK_SLOT_NAME) {
    return previous ?? `Slot ${index + 1}`;
  }
  return current;
}
