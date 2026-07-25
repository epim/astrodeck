// lib/bundleView.ts — pure helpers for the ReportView "Stacking bundle" panel
// (PRO-10 §1.5 / Task 5). No React, no I/O — unit-tested via `npx tsx`
// (bundleView.test.ts), mirroring lib/eta.ts.
import type { BundleMaterializeResult, BundlePreview } from "../types";

/** The folder layouts the server knows how to emit (mirror of bundle.py
 *  `LAYOUTS`). `grouped` is the default and the novice path. */
export type BundleLayout = "grouped" | "siril" | "app";

/** Layout picker options, in a fixed order, with the plain-language "what does
 *  this do to my folders" hint the Advanced disclosure shows. */
export function layoutOptions(): { value: BundleLayout; label: string; hint: string }[] {
  return [
    {
      value: "grouped",
      label: "Grouped (default)",
      hint: "lights under each target/filter/exposure group; one shared masters/ folder",
    },
    {
      value: "siril",
      label: "Siril",
      hint: "lights/ darks/ flats/ biases/ side by side inside each group",
    },
    {
      value: "app",
      label: "APP (AstroPixelProcessor)",
      hint: "Light/ Dark/ Flat/ Bias/ inside each group",
    },
  ];
}

/** Where light subs land for a layout — the client-side mirror of bundle.py's
 *  `_lights_dir`, so the picker updates the preview instantly with no refetch. */
export function lightsDir(groupDir: string, layout: BundleLayout): string {
  return layout === "app" ? `${groupDir}/Light` : `${groupDir}/lights`;
}

/** Per-group lights directories for the whole preview under a chosen layout. */
export function relayoutDirs(preview: BundlePreview | null, layout: BundleLayout): string[] {
  return (preview?.groups ?? []).map((g) => lightsDir(g.dir, layout));
}

/** Query string (leading `?`, or `""`) shared by the .zip href, the preview
 *  fetch, and the materialize POST — so all three always describe the same
 *  bundle. Defaults are omitted, which keeps the novice one-click URL exactly
 *  what it was before this feature existed. */
export function bundleQuery(opts: {
  layout?: BundleLayout;
  weightAlt?: boolean;
  keepThreshold?: number | null;
}): string {
  const parts: string[] = [];
  if (opts.weightAlt) parts.push("weight_altitude=1");
  if (opts.layout && opts.layout !== "grouped") parts.push(`layout=${opts.layout}`);
  if (opts.keepThreshold != null && Number.isFinite(opts.keepThreshold))
    parts.push(`keep_threshold=${opts.keepThreshold}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

/** "38 of 42 rated good" across the whole preview — `null` when no threshold is
 *  active (nothing to say) or no preview has loaded. Says the OUTCOME ("all 42
 *  are still in the download") rather than only the flag count, which read as a
 *  warning that photos had been thrown away. */
export function keptSummary(preview: BundlePreview | null): string | null {
  if (!preview || preview.keep_threshold == null) return null;
  let total = 0;
  let kept = 0;
  for (const g of preview.groups) {
    total += g.light_count;
    kept += g.kept_count ?? g.light_count;
  }
  if (total === 0) return null;
  return `${kept} of ${total} photos rated good — the weakest ${total - kept} are `
    + `marked so your stacker can skip them, but nothing is deleted and all `
    + `${total} are in the download.`;
}

/** One-line OUTCOME of a materialize run — what the user got, not the link/copy
 *  telemetry. Failures are still named rather than hidden behind a total. */
export function materializeSummary(r: BundleMaterializeResult): string {
  const n = r.linked + r.copied;
  const disk = r.copied === 0
    ? "no extra disk used"
    : r.linked === 0
      ? "copied, so they use extra disk"
      : `${r.copied} had to be copied, so those use extra disk`;
  const bits = [`Ready: ${n} photo${n === 1 ? "" : "s"} (${disk})`];
  if (r.failed.length) {
    bits.push(`${r.failed.length} couldn't be written`);
  }
  return bits.join(" · ");
}

/** Honest-disabled reason (§11.8) for the Materialize action. Materializing
 *  only means anything when the FITS are on THIS machine — and `build_bundle`
 *  already filters to subs under CAPTURE_DIR, so a non-empty group list IS the
 *  signal that we're on the capture box (no extra probe needed). */
export function materializeDisabledReason(
  framesCaptured: number,
  preview: BundlePreview | null,
): string | null {
  if (framesCaptured <= 0) return "No frames were captured in this session.";
  if (!preview) return "Still loading this session's bundle preview.";
  if (preview.groups.length === 0)
    return "No local subs on this machine — the FITS live on your imaging host. Use Download bundle.zip and run build.sh there.";
  return null;
}

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
