// photometry.ts — F-A: the pure photometry/SNR core (no React, no DOM; tsx-testable).
// The tested heart shared by NOV-4 (Suggest) and PRO-6 (integration/SNR estimator).
// Model follows the standard CMOS sub-exposure treatment (Robin Glover / SharpCap
// Smart Histogram): per-sub variance = sky + dark shot noise + read noise²; a sub is
// "sky-limited" once sky-noise variance dominates read-noise variance; stacked SNR ∝ √N.

// Sky-limited factor: require sky-noise variance >= FACTOR * read-noise variance. At
// 10, total sub noise sits within ~sqrt(1+1/10)-1 ≈ 4.9% of the read-noise-free ideal
// (the usual "good enough" target). Exposed so callers can tighten/loosen it.
export const SKY_LIMIT_FACTOR = 10;
// Verdict bands (multiples of the sky-limited length): < 1x => read-noise-limited
// (too short); [1x, SUB_LONG_MULT] => good; > SUB_LONG_MULT => longer than needed.
export const SUB_LONG_MULT = 4;

export interface SubNoise {
  skyE: number;         // sky signal electrons in the sub
  darkE: number;        // dark-current electrons in the sub
  readE: number;        // read-noise electrons (constant per sub)
  totalNoiseE: number;  // sqrt(skyE + darkE + readE^2)
  readFraction: number; // readE^2 / totalNoiseE^2  (0..1) — read-noise dominance
}

/** Total per-sub noise (e-) from shot (sky+dark) and read terms in quadrature. */
export function subNoise(skyE: number, readNoiseE: number, darkE = 0): SubNoise {
  const sky = Math.max(0, skyE);
  const dark = Math.max(0, darkE);
  const read = Math.max(0, readNoiseE);
  const varTotal = sky + dark + read * read;
  return {
    skyE: sky, darkE: dark, readE: read,
    totalNoiseE: Math.sqrt(varTotal),
    readFraction: varTotal > 0 ? (read * read) / varTotal : 1,
  };
}

/** Sky signal electrons in one sub from a light-frame background median (ADU),
 *  the bias pedestal (ADU) and the sensor gain (e-/ADU). Clamped >= 0. */
export function skyElectronsPerSub(medianAdu: number, biasAdu: number, egain: number): number {
  return Math.max(0, (medianAdu - biasAdu) * egain);
}

/** Sky rate (e-/s) from the sub's sky electrons and its exposure. 0 when the
 *  exposure is non-positive (guards a downstream divide-by-zero). */
export function skyRateEPerSec(skyE: number, exposureS: number): number {
  return exposureS > 0 ? Math.max(0, skyE) / exposureS : 0;
}

/** Sky-limited sub length (s): t = FACTOR * readNoise^2 / skyRate. null when the
 *  sky rate is non-positive (unmeasurable — e.g. no linear light frame yet). */
export function skyLimitedSubSeconds(
  readNoiseE: number, skyRateEPerSec: number, factor = SKY_LIMIT_FACTOR,
): number | null {
  if (!(skyRateEPerSec > 0)) return null;
  const read = Math.max(0, readNoiseE);
  return (factor * read * read) / skyRateEPerSec;
}

export type SubVerdict = "too_short" | "good" | "long" | "unknown";

/** Compare an actual sub length to the sky-limited length. */
export function subLengthVerdict(actualS: number, skyLimitedS: number | null): SubVerdict {
  if (skyLimitedS == null || !(actualS > 0)) return "unknown";
  if (actualS < skyLimitedS) return "too_short";
  if (actualS > skyLimitedS * SUB_LONG_MULT) return "long";
  return "good";
}

/** Per-sub SNR of a signal (electrons in the sub) against the total sub noise. */
export function subSnr(signalE: number, noise: SubNoise): number {
  return noise.totalNoiseE > 0 ? Math.max(0, signalE) / noise.totalNoiseE : 0;
}

/** Stacked SNR after n subs (shot/read-noise limited): perSubSnr * sqrt(n). */
export function stackedSnr(perSubSnr: number, n: number): number {
  return n > 0 ? perSubSnr * Math.sqrt(n) : 0;
}

/** Subs needed for a target STACKED snr given the per-sub snr. Ceil; null when the
 *  per-sub snr is non-positive (no signal / unreachable). */
export function subsForStackedSnr(perSubSnr: number, targetSnr: number): number | null {
  if (!(perSubSnr > 0) || !(targetSnr > 0)) return null;
  return Math.ceil((targetSnr / perSubSnr) ** 2);
}

/** Extra subs to raise a stack of `have` subs to `multiple`x its current SNR.
 *  SNR ∝ sqrt(N) => N_target = have * multiple^2. Exact; needs only counts. */
export function moreSubsForSnrMultiple(have: number, multiple: number): number {
  if (have <= 0 || multiple <= 1) return 0;
  return Math.ceil(have * multiple * multiple) - have;
}

/** Projected total integration (s) for a set of steps (count x exposure). */
export function projectedIntegrationSeconds(steps: { exposure_s: number; count: number }[]): number {
  return steps.reduce((a, s) => a + Math.max(0, s.exposure_s) * Math.max(0, s.count), 0);
}

/** Per-filter integration rollup (s), grouping by filter label (null => "—").
 *  Insertion order preserved so the UI list is deterministic. */
export function integrationByFilter(
  steps: { filter: string | null; exposure_s: number; count: number }[],
): { filter: string; seconds: number }[] {
  const order: string[] = [];
  const acc = new Map<string, number>();
  for (const s of steps) {
    const key = s.filter ?? "—";
    if (!acc.has(key)) order.push(key);
    acc.set(key, (acc.get(key) ?? 0) + Math.max(0, s.exposure_s) * Math.max(0, s.count));
  }
  return order.map((f) => ({ filter: f, seconds: acc.get(f) ?? 0 }));
}

// ---------------------------------------------------------------- per-sub SNR
// Polish grab-bag (b): an ABSOLUTE per-star SNR for THIS sub, from the server's
// background-subtracted star flux (ADU) and the user's photometry profile. It is
// deliberately a per-SUB number — never the stacked result (that is
// `stackedSnr(snr, n)`), and the copy at every call site must say "this sub".

export interface PerSubSnrInput {
  fluxAdu: number;      // background-subtracted star flux in THIS sub
  egain: number;        // e-/ADU
  biasAdu: number;      // bias pedestal (ADU)
  medianAdu: number;    // frame background median (ADU) — the sky term
  readNoiseE: number;   // read noise (e-)
}
export interface PerSubSnr {
  ok: boolean;
  snr: number;          // 0 when !ok
  signalE: number;      // star electrons in this sub
  noise: SubNoise | null;
  reason?: string;      // plain-language why-not, when !ok
}

/** Per-sub SNR of one star: signal = flux x egain (flux is already background-
 *  subtracted, so no bias term on the signal), noise = sqrt(sky + read²) from the
 *  existing tested primitives. `ok:false` with an honest prompt when the
 *  photometry profile is unset — identical posture to `suggestSubLength`. */
export function perSubSnrFromFlux(inp: PerSubSnrInput): PerSubSnr {
  const { fluxAdu, egain, biasAdu, medianAdu, readNoiseE } = inp;
  if (!(egain > 0) || !(readNoiseE > 0)) {
    return { ok: false, snr: 0, signalE: 0, noise: null,
      reason: "Add your camera's gain (e-/ADU) and read noise (e-) to see SNR." };
  }
  if (!(fluxAdu > 0)) {
    return { ok: false, snr: 0, signalE: 0, noise: null,
      reason: "No measured star flux in this sub yet." };
  }
  const signalE = fluxAdu * egain;
  const skyE = skyElectronsPerSub(medianAdu, biasAdu, egain);
  const noise = subNoise(skyE, readNoiseE);
  return { ok: true, snr: subSnr(signalE, noise), signalE, noise };
}

export interface SuggestInput {
  medianAdu: number; biasAdu: number; egain: number; readNoiseE: number;
  exposureS: number; minSubS?: number; maxSubS?: number; factor?: number;
}
export interface Suggestion {
  ok: boolean; skyLimitedS: number | null; suggestedS: number | null;
  readFraction: number | null; reason: string;
}

/** NOV-4 assembler: sky-limited sub length from a measured background, clamped to
 *  [minSubS, maxSubS] and rounded to a whole second. `ok:false` (with a reason)
 *  when egain/read-noise/exposure are unusable — the caller shows the honest prompt. */
export function suggestSubLength(inp: SuggestInput): Suggestion {
  const { medianAdu, biasAdu, egain, readNoiseE, exposureS } = inp;
  const minSubS = inp.minSubS ?? 5;
  const maxSubS = inp.maxSubS ?? 600;
  if (!(egain > 0) || !(readNoiseE > 0)) {
    return { ok: false, skyLimitedS: null, suggestedS: null, readFraction: null,
      reason: "Add your camera's gain (e-/ADU) and read noise (e-) to enable Suggest." };
  }
  if (!(exposureS > 0)) {
    return { ok: false, skyLimitedS: null, suggestedS: null, readFraction: null,
      reason: "No linear preview frame yet — take one light frame first." };
  }
  const skyE = skyElectronsPerSub(medianAdu, biasAdu, egain);
  const rate = skyRateEPerSec(skyE, exposureS);
  const skyLimitedS = skyLimitedSubSeconds(readNoiseE, rate, inp.factor);
  if (skyLimitedS == null) {
    return { ok: false, skyLimitedS: null, suggestedS: null, readFraction: null,
      reason: "Sky background reads at the bias floor — can't estimate a sky-limited sub." };
  }
  const suggestedS = Math.round(Math.min(maxSubS, Math.max(minSubS, skyLimitedS)));
  const readFraction = subNoise(skyE * (suggestedS / exposureS), readNoiseE).readFraction;
  return { ok: true, skyLimitedS, suggestedS, readFraction,
    reason: `Sky-limited near ${Math.round(skyLimitedS)}s at this sky brightness.` };
}
