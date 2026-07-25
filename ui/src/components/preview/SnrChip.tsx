// SnrChip.tsx — absolute per-SUB SNR readout over the preview (polish grab-bag
// (b)). Thin shell: every number comes from lib/photometry's tested core.
//
// Progressive disclosure (Decision E):
//   - novice default: ONE plain line — "Typical star SNR ~38 · this sub".
//   - on tap: the tapped star's own measured value rides in the same chip, so
//     one glance answers both "how is this frame doing" and "what did I tap".
//   - advanced: the decomposition (signal e-, sky e-, read e-, read-noise share
//     of the noise variance) lives in the title tooltip — auditable, off the
//     novice's path.
//   - when no photometry profile is set the chip renders NOTHING at all (not an
//     error, not a disabled shell): with no e-/ADU there is no honest number,
//     and a novice who never asked for SNR sees zero clutter.
//
// "this sub" is load-bearing copy — a per-sub SNR must never be read as the
// final stacked-image SNR. (Stacking N subs multiplies it by sqrt(N):
// lib/photometry.stackedSnr.)
//
// HONESTY NOTE: the wire carries ONE flux number per frame — the median over the
// trusted mid-bright stars (`star_flux_median`). `StarMark` deliberately stays
// compact (x/y/hfr[, ecc, theta]), so a *per-star* SNR for the tapped star is not
// computable client-side; we show its measured HFR rather than inventing an SNR
// for it.
import type { PreviewInfo, StarMark } from "../../types";
import { usePhotometry } from "../../store";
import { perSubSnrFromFlux } from "../../lib/photometry";

function fmtSnr(v: number): string {
  return v >= 10 ? v.toFixed(0) : v.toFixed(1);
}

export function SnrChip({
  preview, star, className = "",
}: {
  preview: PreviewInfo;
  star: StarMark | null;
  className?: string;
}) {
  const photometry = usePhotometry();
  const typicalFlux = preview.star_flux_median;
  // No profile / no measured flux => no honest number => no chip at all.
  if (!(photometry.egain > 0) || !(photometry.readNoiseE > 0)) return null;
  if (typicalFlux == null) return null;

  const res = perSubSnrFromFlux({
    fluxAdu: typicalFlux,
    egain: photometry.egain,
    biasAdu: photometry.biasAdu,
    medianAdu: preview.stats.median,
    readNoiseE: photometry.readNoiseE,
  });
  const n = res.noise;
  if (!res.ok || !n) return null;

  const detail =
    `Per-SUB estimate for a typical (median-flux) star in THIS frame — not the ` +
    `stacked result. Signal ${Math.round(res.signalE)} e-, sky ${Math.round(n.skyE)} e-, ` +
    `read ${n.readE.toFixed(1)} e- (${Math.round(n.readFraction * 100)}% of the noise ` +
    `variance). Star flux is background-subtracted ADU from the detector, so treat ` +
    `it as an estimate, not photometry.`;

  return (
    <div className={`preview-chip mono ${className}`} aria-live="polite" title={detail}>
      Typical star SNR ~{fmtSnr(res.snr)} · this sub
      {star && (
        <span className="text-dim"> · tapped star HFR {star.hfr.toFixed(2)} px</span>
      )}
    </div>
  );
}
