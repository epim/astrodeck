// ClipMaskLayer.tsx — saturation/clip mask (stream O).
// (spec §5 "Clip mask", §12 honesty rule #4, decisions #3/#7)
//
// HONESTY: only renders when data_is_linear && full_well != null && overlays.clip.
// On NINA / unknown-well it renders NOTHING (the caller shows the inline reason).
// We do not have the linear pixels client-side, so Pass-1 cannot paint exact
// saturated pixels; instead we overlay a STATIC desaturated-amber hatch FRAME +
// a "▲ CLIPPED" text tag, shown only when the frame actually clips
// (stats.max >= full_well). Static (never flashing/magenta — §11.4). This is an
// honest "this frame contains clipped pixels" indicator, not a per-pixel lie.
//
// Per-pixel clip painting lands with the linear /crop path in Pass 2.

export function ClipMaskLayer({
  w,
  h,
  active,
}: {
  w: number; // display image dims (transform space)
  h: number;
  active: boolean; // already gated by caller: linear+full_well+clip+stats.max>=full_well
}) {
  if (!active) return null;
  const inset = Math.max(2, Math.min(w, h) * 0.006);
  return (
    <g aria-hidden>
      <defs>
        <pattern id="clip-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line x1="0" y1="0" x2="0" y2="6" stroke="#d9a441" strokeWidth="1.4" />
        </pattern>
      </defs>
      {/* hatched frame border (static) */}
      <rect
        x={inset}
        y={inset}
        width={w - inset * 2}
        height={h - inset * 2}
        fill="none"
        stroke="url(#clip-hatch)"
        strokeWidth={Math.max(6, Math.min(w, h) * 0.02)}
        vectorEffect="non-scaling-stroke"
      />
      <rect
        x={inset}
        y={inset}
        width={w - inset * 2}
        height={h - inset * 2}
        fill="none"
        stroke="#d9a441"
        strokeWidth={1}
        strokeDasharray="4 3"
        vectorEffect="non-scaling-stroke"
      />
    </g>
  );
}
