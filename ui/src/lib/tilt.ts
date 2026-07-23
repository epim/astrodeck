// tilt.ts — pure client helpers for the sensor-tilt / corner-vs-center
// optical-aberration inspector (PRO-13). No render here; see
// components/preview/TiltOverlay.tsx for the SVG heatmap that consumes these.
import type { TiltInfo } from "../types";

export interface TiltSummary {
  label: string;
  tone: "good" | "warn" | "bad";
  advice: string;
}

const TABLE: Record<TiltInfo["pattern"], TiltSummary> = {
  uniform: {
    label: "Uniform",
    tone: "good",
    advice: "Stars are round and even across the frame.",
  },
  tilt: {
    label: "Sensor tilt",
    tone: "bad",
    advice: "HFR is worse on one side — check camera/sensor squareness and spacer tilt.",
  },
  coma: {
    label: "Coma / spacing",
    tone: "warn",
    advice: "Corners degrade radially vs a sharp center — adjust back-focus / corrector spacing.",
  },
  tracking: {
    label: "Tracking drift",
    tone: "warn",
    advice: "Elongation is uniform in one direction — check guiding / polar alignment.",
  },
};

export function tiltSummary(t: TiltInfo): TiltSummary {
  return TABLE[t.pattern] ?? TABLE.uniform;
}

export function zoneHfrRange(t: TiltInfo): { min: number; max: number } | null {
  const hs = t.zones.map((z) => z.hfr).filter((h): h is number => h != null);
  if (hs.length < 2) return null;
  return { min: Math.min(...hs), max: Math.max(...hs) };
}

export function heatFrac(hfr: number, min: number, max: number): number {
  if (max <= min) return 0;
  return Math.min(1, Math.max(0, (hfr - min) / (max - min)));
}
