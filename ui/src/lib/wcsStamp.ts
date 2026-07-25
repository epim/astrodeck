// wcsStamp.ts — the only non-render logic behind the per-frame-WCS Settings
// panel (spec §4.3). Kept pure and out of the .tsx so it is testable without a
// DOM: the panel itself is a thin shell.
import type { WcsStampConfig } from "../types";

/** Every field at its server default (config.py WcsStampConfig). Used as the
 *  fallback when an old WS `hello` bootstrap predates the block. */
export const DEFAULT_WCS_STAMP: WcsStampConfig = {
  solver: "auto",
  downsample: 0,
  min_stars: 0,
  queue_max: 4,
};

/** Downsample factors the server accepts (`WCS_DOWNSAMPLE_CHOICES`). 0 = let
 *  the solver choose, which is what the feature has always done. */
export const DOWNSAMPLE_CHOICES = [0, 1, 2, 4] as const;

export const downsampleLabel = (n: number): string =>
  n === 0 ? "Auto" : `${n}x`;

/** Fill in the defaults for a config that may predate the block. */
export function wcsStampOrDefault(
  cfg: WcsStampConfig | null | undefined,
): WcsStampConfig {
  return { ...DEFAULT_WCS_STAMP, ...(cfg ?? {}) };
}

/** The honest advisory to show under the toggle, or null when there's nothing
 *  to warn about.
 *
 *  Reads the ALREADY-SERVED per-capability solve resolution (poll_status →
 *  providers.solve) rather than inventing a new endpoint. `unavailable` is
 *  precisely "no ASTAP and a real mount is connected" — the one case where
 *  turning the toggle on will quietly tag nothing, so say so. `sim` is fine
 *  (the built-in solver answers on a sim rig) and needs no warning. */
export function wcsStampAdvisory(
  solve: { kind?: string } | null | undefined,
): string | null {
  if (solve?.kind === "unavailable") {
    return (
      "ASTAP not detected — a real rig needs ASTAP installed to solve, so " +
      "frames will save without sky coordinates until you install it."
    );
  }
  return null;
}

/** One-line state summary for the panel header / collapsed disclosure. */
export function wcsStampSummary(
  enabled: boolean,
  cfg?: WcsStampConfig | null,
): string {
  if (!enabled) return "Off — frames save without sky coordinates.";
  const c = wcsStampOrDefault(cfg);
  const parts = [c.solver === "astap" ? "ASTAP" : "Auto solver"];
  if (c.downsample > 0) parts.push(`downsample ${downsampleLabel(c.downsample)}`);
  if (c.min_stars > 0) parts.push(`min ${c.min_stars} stars`);
  return `On — ${parts.join(", ")}.`;
}
