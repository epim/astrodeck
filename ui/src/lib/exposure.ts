// exposure.ts — pure bounds check for the Capture view's manual exposure
// field. No React, no DOM: npx-tsx testable (safety.ts/schedule.ts precedent).
//
// Blocks both ends of the range: <=0s silently produced a blank frame with a
// misleading "few stars" error downstream (CAP-02-gemini / r1 CAP-01-neg),
// and unbounded input — including scientific notation like "1e10", which
// `Number()` parses to a perfectly finite value and would otherwise sail
// through a finite/positive-only check — let an absurd exposure reach the
// camera (R3-CAP-02 note). 3600s (1h) is a sane camera-agnostic ceiling: no
// consumer astro camera runs a single sub anywhere near that long.

export const EXPOSURE_MAX_S = 3600;

export function isExposureInvalid(raw: string): boolean {
  const n = Number(raw);
  return raw.trim() === "" || !Number.isFinite(n) || n <= 0 || n > EXPOSURE_MAX_S;
}
