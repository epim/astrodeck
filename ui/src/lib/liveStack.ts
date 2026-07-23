// Pure formatting for the Live View readout (NOV-1). No React, tsx-tested.
import type { LiveStackInfo } from "../types";

export function integratedLabel(seconds: number): string {
  const s = Math.max(0, seconds);
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  return `${(s / 3600).toFixed(1).replace(/\.0$/, "")} h`;
}

export function formatLiveStack(ls: LiveStackInfo): {
  frames: string; integrated: string; rejected: string; headline: string;
} {
  const n = Math.max(0, Math.floor(ls.frames));
  const frames = `${n} ${n === 1 ? "frame" : "frames"}`;
  const integrated = integratedLabel(ls.integrated_s);
  const r = Math.max(0, Math.floor(ls.rejected));
  const rejected = r > 0 ? `${r} skipped` : "";
  return { frames, integrated, rejected, headline: `${frames} · ${integrated} integrated` };
}
