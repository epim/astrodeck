// lib/bundleView.ts — pure helpers for the ReportView "Stacking bundle" panel
// (PRO-10 §1.5 / Task 5). No React, no I/O — unit-tested via `npx tsx`
// (bundleView.test.ts), mirroring lib/eta.ts.
import type { BundlePreview } from "../types";

/** The three calibration kinds in a fixed display order, each with its matched
 *  flag from the preview's per-group `masters` map. Fixed order so the chips
 *  never reshuffle between groups. */
export function masterChips(m: Record<string, boolean>): { kind: string; ok: boolean }[] {
  return (["dark", "flat", "bias"] as const).map((kind) => ({ kind, ok: !!m[kind] }));
}

/** Honest-disabled reason (§11.8): a non-null string means the download is not
 *  meaningful and the control must render as a dimmed, `aria-disabled` span with
 *  this text as its `title`. `null` means the bundle is downloadable. */
export function bundleDisabledReason(
  framesCaptured: number,
  preview: BundlePreview | null,
): string | null {
  if (framesCaptured <= 0) return "No frames were captured in this session.";
  if (preview && preview.groups.length === 0)
    return "No local light subs available to bundle.";
  return null;
}
