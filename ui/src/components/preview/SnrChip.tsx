// SnrChip.tsx — absolute per-SUB SNR readout over the preview (polish grab-bag
// (b)). Thin shell: every number comes from lib/photometry's tested core.
//
// Progressive disclosure (Decision E):
//   - novice default: ONE plain line — a verdict WORD, the number, and what
//     stacking buys ("Star signal strong — SNR ~38 in this photo").
//   - NOT aria-live, and it must stay that way: this is ambient per-frame
//     telemetry, and announcing it on every new sub all night is screen-reader
//     spam that talks over everything else for the whole session.
//   - advanced: the decomposition (signal e-, sky e-, read e-, read-noise share
//     of the noise variance) lives in a `Tooltip` — reachable on demand by tap,
//     hover AND keyboard focus, off the novice's path. It was a raw `title=`,
//     which never fires on touch and is invisible to AT.
//   - when no photometry profile is set the chip renders NOTHING at all (not an
//     error, not a disabled shell): with no e-/ADU there is no honest number,
//     and a novice who never asked for SNR sees zero clutter.
//
// The tapped star's HFR used to ride along at the end of this line. It was a
// straight duplicate of the selected-star readout PreviewStage already pins to
// the bottom-left (which additionally gives it in arcsec), and it was the single
// biggest contributor to this chip running the full width of a phone stage and
// under the loupe. Dropped — one fact, one place.
//
// "this sub" is load-bearing copy — a per-sub SNR must never be read as the
// final stacked-image SNR. (Stacking N subs multiplies it by sqrt(N):
// lib/photometry.stackedSnr.)
//
// HONESTY NOTE: the wire carries ONE flux number per frame — the median over the
// trusted mid-bright stars (`star_flux_median`). `StarMark` deliberately stays
// compact (x/y/hfr[, ecc, theta]), so a *per-star* SNR for the tapped star is not
// computable client-side. That is why this chip is frame-wide only and says
// nothing at all about whichever star you tapped.
import type { PreviewInfo } from "../../types";
import { usePhotometry } from "../../store";
import { Tooltip } from "../ui";
import { perSubSnrFromFlux, starSnrWord } from "../../lib/photometry";

function fmtSnr(v: number): string {
  return v >= 10 ? v.toFixed(0) : v.toFixed(1);
}

export function SnrChip({
  preview, className = "",
}: {
  preview: PreviewInfo;
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

  // A bare "~38" reads as a score out of 100. Lead with the WORD, keep the
  // number, and say what stacking buys — the whole point of a per-sub figure is
  // that it is not the final one (SNR grows as sqrt(N), so 4x the subs = 2x).
  return (
    <div className={className}>
      <Tooltip content={detail}>
        <span className="preview-chip mono">
          Star signal {starSnrWord(res.snr)} — SNR ~{fmtSnr(res.snr)} in this photo
          <span className="text-dim"> · 4× as many photos ≈ 2× better</span>
        </span>
      </Tooltip>
    </div>
  );
}
