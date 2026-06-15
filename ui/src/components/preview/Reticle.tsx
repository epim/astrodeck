// Reticle.tsx — center mark + optional full reticle, drawn INSIDE the shared
// transform SVG so it stays pixel-aligned with the image (kills the old
// misaligned-crosshair bug by construction — spec §1, §7, §10).
//
// Perceptual rules (§11): every stroke is drawn twice — a dark --halo under-stroke
// then a neutral high-value stroke — so it reads on bright nebula and dark sky.
// Uses vector-effect non-scaling-stroke so line weight is constant under zoom.
// Neutral stroke (not --accent red-on-red at night) per §8.

export function Reticle({
  w,
  h,
  centerMark,
  reticle,
}: {
  w: number; // display image width (transform-space units)
  h: number;
  centerMark: boolean;
  reticle: boolean;
}) {
  if (!centerMark && !reticle) return null;
  const cx = w / 2;
  const cy = h / 2;
  const arm = Math.min(w, h) * 0.04; // center-mark arm length
  const gap = arm * 0.4; // small gap at the exact center so a star is visible

  // a stroke drawn twice: halo (dark, wide) then the visible neutral stroke
  const dbl = (d: string, key: string) => (
    <g key={key}>
      <path d={d} stroke="var(--halo)" strokeWidth={3} fill="none" vectorEffect="non-scaling-stroke" />
      <path d={d} stroke="#e8eefc" strokeWidth={1} fill="none" vectorEffect="non-scaling-stroke" opacity={0.85} />
    </g>
  );

  return (
    <g aria-hidden>
      {centerMark &&
        dbl(
          `M${cx - arm},${cy} L${cx - gap},${cy} M${cx + gap},${cy} L${cx + arm},${cy} ` +
            `M${cx},${cy - arm} L${cx},${cy - gap} M${cx},${cy + gap} L${cx},${cy + arm}`,
          "center",
        )}
      {reticle && (
        <>
          {dbl(`M0,${cy} L${w},${cy} M${cx},0 L${cx},${h}`, "cross")}
          {[0.18, 0.34].map((f, i) =>
            dbl(
              `M${cx} ${cy} m ${-Math.min(w, h) * f},0 a ${Math.min(w, h) * f},${Math.min(w, h) * f} 0 1,0 ${Math.min(w, h) * f * 2},0 a ${Math.min(w, h) * f},${Math.min(w, h) * f} 0 1,0 ${-Math.min(w, h) * f * 2},0`,
              `ring${i}`,
            ),
          )}
        </>
      )}
    </g>
  );
}
