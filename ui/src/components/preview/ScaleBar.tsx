// ScaleBar.tsx — a labeled scale bar overlaid on the stage (field-workflow
// finding #14, spec §1, §5). Drawn in SCREEN space (a fixed-px bar, not inside
// the zoom transform) so it always reads a round angular value at the current
// zoom. Hidden when pixel_scale_arcsec is unknown (honest absence, §12).
//
// Math: arcsec-per-display-pixel = pixel_scale_arcsec * (data_width / display_width)
// because the display image is a downscale of frame.data. The on-screen bar is
// then `arcsecPerScreenPx = arcsecPerDisplayPx / scale` (zoom magnifies pixels).
// We pick the largest "nice" arcsec/arcmin value whose bar is <= ~120px.

const NICE_ARCSEC = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200];

function fmtAngle(arcsec: number): string {
  if (arcsec >= 3600) return `${(arcsec / 3600).toFixed(arcsec % 3600 ? 1 : 0)}°`;
  if (arcsec >= 60) return `${Math.round(arcsec / 60)}'`;
  return `${arcsec}″`;
}

export function ScaleBar({
  pixelScaleArcsec,
  dataWidth,
  displayWidth,
  scale,
}: {
  pixelScaleArcsec: number | undefined;
  dataWidth: number;
  displayWidth: number;
  scale: number;
}) {
  // Honest absence (§12.4): when plate scale is unknown we show nothing rather
  // than a fabricated bar. The toolbar/FrameStats still convey HFR in px.
  if (!pixelScaleArcsec || !displayWidth || !dataWidth) return null;
  const arcsecPerDisplayPx = pixelScaleArcsec * (dataWidth / displayWidth);
  const arcsecPerScreenPx = arcsecPerDisplayPx / Math.max(scale, 1e-4);
  const maxBarPx = 120;
  // largest nice value whose bar fits
  let chosen = NICE_ARCSEC[0];
  for (const v of NICE_ARCSEC) {
    if (v / arcsecPerScreenPx <= maxBarPx) chosen = v;
    else break;
  }
  const barPx = Math.round(chosen / arcsecPerScreenPx);

  return (
    <div className="preview-chip flex items-center gap-2" aria-label={`Scale bar ${fmtAngle(chosen)}`}>
      <svg width={barPx} height={8} className="preview-scalebar block" aria-hidden>
        <path
          d={`M0.5,1 L0.5,7 M0.5,4 L${barPx - 0.5},4 M${barPx - 0.5},1 L${barPx - 0.5},7`}
          fill="none"
          strokeWidth={1.5}
        />
      </svg>
      <span className="mono">{fmtAngle(chosen)}</span>
    </div>
  );
}
