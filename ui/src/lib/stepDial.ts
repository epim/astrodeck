// stepDial.ts — the geometry and the commit rule behind the step-size dial
// (spec: docs/superpowers/specs/2026-07-30-scope-controls-design.md §The
// step-size dial).
//
// Replacing the eight-button grid. Collapsed it is ONE control showing the
// current magnitude; press and hold and it blooms into a vertical arc; slide the
// thumb to a value; release to commit.
//
// Why an arc rather than a menu: one continuous gesture, no second tap, no
// precision required, and the thumb never leaves the glass. Above all it is
// SELF-CANCELLING — slide back toward the origin and release and nothing
// changes — which matters when you are cold and half asleep and the mount is
// worth more than the phone.
//
// The maths lives here rather than in the component for the reason
// profileDelete.ts does: "which option is under the thumb" is the part that can
// be wrong in a way nobody notices until they are at the scope in the dark.

/** How far the thumb must travel from the press point before the arc treats the
 *  gesture as a SELECTION rather than a tap. Below this the user has not moved,
 *  and a stray pixel of jitter must not silently change the step size. */
export const DEAD_ZONE_PX = 12;

/** Vertical pitch between arc options. Comfortably larger than a fingertip so a
 *  slide lands where it looks like it lands. */
export const OPTION_PITCH_PX = 44;

/**
 * Which option index the thumb is over, or `null` for "none — release changes
 * nothing".
 *
 * The arc opens UPWARD from the collapsed dial (the design's "upward and
 * inward"), so travelling up means moving toward LATER options in a list drawn
 * bottom-to-top. `dy` is `pressY - currentY`, i.e. positive when moving up.
 *
 * Returning null inside the dead zone is the self-cancel: it is what makes
 * "slide back to the centre and release" mean "never mind".
 */
export function optionAt(dy: number, count: number): number | null {
  if (count <= 0) return null;
  if (dy < DEAD_ZONE_PX) return null;
  const steps = Math.floor((dy - DEAD_ZONE_PX) / OPTION_PITCH_PX);
  return Math.min(steps, count - 1);
}

/**
 * The value a gesture commits to, given where it started and where it ended.
 *
 * `current` is returned unchanged when the gesture never left the dead zone, so
 * a press-and-release with no travel is a no-op rather than a silent reset to
 * the first option — the failure that would bite hardest, because a tap is what
 * you do by accident.
 */
export function commitValue<T>(values: readonly T[], current: T, dy: number): T {
  const i = optionAt(dy, values.length);
  return i === null ? current : values[i];
}

/**
 * The next value for a keyboard user. Arrow up / right goes to a BIGGER step,
 * matching the arc's upward-is-more geometry, and both ends clamp rather than
 * wrap: wrapping from 1000 to 1 on one extra keypress is how you move a focuser
 * a thousand steps by mistake.
 */
export function stepByKey<T>(values: readonly T[], current: T, key: string): T {
  const i = values.indexOf(current);
  if (i < 0) return values[0] ?? current;
  if (key === "ArrowUp" || key === "ArrowRight") {
    return values[Math.min(i + 1, values.length - 1)];
  }
  if (key === "ArrowDown" || key === "ArrowLeft") {
    return values[Math.max(i - 1, 0)];
  }
  if (key === "Home") return values[0];
  if (key === "End") return values[values.length - 1];
  return current;
}

/** Label for a step magnitude. Kept here so the dial, the +/- buttons and any
 *  readout cannot disagree about how a number is written. */
export function stepLabel(n: number): string {
  return String(n);
}

/** The signed label for a nudge button ("+100" / "-100"). */
export function nudgeLabel(n: number, sign: 1 | -1): string {
  return `${sign > 0 ? "+" : "-"}${stepLabel(Math.abs(n))}`;
}
