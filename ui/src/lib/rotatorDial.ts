// rotatorDial.ts — pure SVG geometry for the RotatorCard arc dial (CAA spec
// §5.1). Split out of RotatorCard.tsx so the assert-file tests can import these
// under plain `tsx`/node: the component module eagerly touches `window` through
// its `../../api` import (lib/base computes BASE at load), which a DOM-less test
// run cannot satisfy — same reason healthStrip.test imports lib/health directly
// rather than the component. No React, no I/O, no `window`.
import type { RotatorConfig } from "../types";

export function polarXY(cx: number, cy: number, r: number, deg: number) {
  // 0° at 12 o'clock, clockwise (matches how a rotator angle reads on-sky)
  const rad = ((deg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

export function arcPath(cx: number, cy: number, r: number,
                        startDeg: number, sweepDeg: number): string {
  const s = polarXY(cx, cy, r, startDeg);
  const e = polarXY(cx, cy, r, startDeg + sweepDeg);
  const large = sweepDeg > 180 ? 1 : 0;
  return `M ${s.x.toFixed(2)} ${s.y.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${e.x.toFixed(2)} ${e.y.toFixed(2)}`;
}

export function allowedSweepDeg(rangeType: RotatorConfig["range_type"]): number {
  return rangeType === "half" ? 180 : rangeType === "quarter" ? 90 : 360;
}
