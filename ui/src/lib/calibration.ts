// calibration.ts — pure logic for the one-tap calibration capture feature
// (calibration-capture spec §1.3). No React, no DOM: npx-tsx testable
// (exposure.ts/eta.ts precedent).
//
// Tracks what the last batch of Light frames was shot at (accumulateLight),
// decides whether an end-of-session "take matching darks?" nudge is honest to
// offer (shouldOfferDarks), and derives the calibration-capture prefill /
// human-readable summary from that snapshot.

export type FrameType = "Light" | "Dark" | "Flat" | "Bias";

export const FRAME_TYPES: readonly FrameType[] = ["Light", "Dark", "Flat", "Bias"];

// Coaching copy shown under the frame-type selector for non-Light types (empty
// for Light — normal imaging needs no coaching).
export const FRAME_COACH: Record<FrameType, string> = {
  Light: "",
  Dark: "Cap the scope so no light reaches the sensor. A dark matches your light's exposure, gain and temperature with the shutter closed.",
  Bias: "Cap the scope. A bias is the shortest possible dark — it maps read-noise and offset only.",
  Flat: "Uncover the scope and point it at an even light source (flat panel or dawn sky). Flats keep the shutter open.",
};

// Snapshot of what a light frame was shot at (recorded when a light frame LANDS).
export interface LightSnapshot {
  exposureS: number;
  gain: number;
  offset: number;
  binning: number;
  tempC: number | null; // sensor temp at capture, if any cooler reports one
  count: number; // frames banked at THESE settings this session
}

// Prefill copied into the capture form when adopting a calibration batch.
export interface CalibrationPrefill {
  frameType: FrameType;
  exposure: string;
  gain: string;
  offset: string;
  binning: string;
  coolerTarget: string | null; // null => leave the cooler field untouched
}

const sameSettings = (a: Omit<LightSnapshot, "count">, b: LightSnapshot): boolean =>
  a.exposureS === b.exposureS && a.gain === b.gain &&
  a.offset === b.offset && a.binning === b.binning;

// Reducer for the completion signal: if `next`'s exposure/gain/offset/binning
// MATCH `prev`, return `prev` with count+1 and refreshed tempC; otherwise start
// a fresh batch at count:1 (batch reset).
export function accumulateLight(
  prev: LightSnapshot | null,
  next: Omit<LightSnapshot, "count">,
): LightSnapshot {
  if (prev && sameSettings(next, prev)) {
    return { ...prev, tempC: next.tempC, count: prev.count + 1 };
  }
  return { ...next, count: 1 };
}

export function shouldOfferDarks(l: LightSnapshot | null): l is LightSnapshot {
  return !!l && l.count >= 1 && Number.isFinite(l.exposureS) && l.exposureS > 0;
}

const fmtExp = (s: number): string => (s >= 1 ? `${Math.round(s)}s` : `${s}s`);

export function formatLightSummary(l: LightSnapshot): string {
  const parts = [
    l.count >= 1 ? `${l.count} × ${fmtExp(l.exposureS)}` : `${fmtExp(l.exposureS)}`,
    `gain ${l.gain}`,
  ];
  if (l.tempC != null) parts.push(`${l.tempC} °C`);
  return parts.join(" · ");
}

export function darkPrefillFrom(l: LightSnapshot): CalibrationPrefill {
  return {
    frameType: "Dark",
    exposure: String(l.exposureS),
    gain: String(l.gain),
    offset: String(l.offset),
    binning: String(l.binning),
    coolerTarget: l.tempC != null ? String(l.tempC) : null,
  };
}
