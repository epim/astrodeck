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

// The numeric half of the check, shared with callers that already hold a
// parsed number (e.g. a Sequence plan step's `exposure_s`, or preflight.ts's
// buildPreflight()) rather than a raw text-field string — see
// isExposureInvalid below for the string/text-field variant.
export function isExposureValueInvalid(n: number): boolean {
  return !Number.isFinite(n) || n <= 0 || n > EXPOSURE_MAX_S;
}

export function isExposureInvalid(raw: string): boolean {
  return raw.trim() === "" || isExposureValueInvalid(Number(raw));
}
