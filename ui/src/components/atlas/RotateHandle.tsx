// RotateHandle — the Atlas camera/FOV box's grabbable rotation stalk, rendered
// in its OWN top-level <svg> layer (design spec §6, wave-2 G3 fix).
//
// ROOT CAUSE this replaces: the handle used to live inside FovOverlay's shared
// geometry <svg>, which SkyCanvas mounts BEFORE (i.e. underneath, in normal
// DOM/paint order — neither element sets a z-index) the HTML label layer that
// carries the "Your camera · WxH" text. That text sits a FIXED 18 CSS-px above
// the frame's top edge, while the handle's reach above the same edge is a
// viewBox-unit offset that SCALES with the canvas's CSS width (`boxPx`). At
// the canvas's max width (720px) the two only just graze; below roughly
// 400-410px of canvas width the handle's visible ring is ENTIRELY inside the
// label's vertical band, and because the label paints later/on top, the
// handle vanished — invisible and (functionally, if not literally) ungrabbable.
//
// Fix: promote the handle to a sibling <svg> mounted AFTER the label layer in
// SkyCanvas's JSX, so it always paints on top regardless of any geometric
// overlap — grabbable and visible at every canvas width. It shares the exact
// `translate(cx,cy) rotate(rotationDeg)` transform FovOverlay uses so it stays
// pixel-aligned with the frame rectangle at any rotation/zoom, and the same
// `gridHalfHeightPx` helper (lib/framing.ts) so a multi-row mosaic's stalk
// still clears the whole grid, not just one panel.
import { type JSX } from "react";
import { gridHalfHeightPx } from "../../lib/framing";

export interface RotateHandleProps {
  /** viewBox edge length (square). SkyCanvas uses 1000. */
  view: number;
  /** Projected center of the survey crop in viewBox px — same as FovOverlay. */
  cx: number;
  cy: number;
  /** Survey-plane px per degree (1000 / fovZoomDeg). */
  pxPerDeg: number;
  /** Single-frame footprint height in degrees (bin-1). */
  fovYDeg: number;
  /** Position angle of the whole mosaic, degrees (clockwise on the N-up image). */
  rotationDeg: number;
  rows: number;
  overlap: number; // 0..0.5
}

export function RotateHandle(props: RotateHandleProps): JSX.Element {
  const { view, cx, cy, pxPerDeg, fovYDeg, rotationDeg, rows, overlap } = props;
  const gridHalfH = gridHalfHeightPx(fovYDeg, pxPerDeg, rows, overlap);

  return (
    <svg
      viewBox={`0 0 ${view} ${view}`}
      className="absolute inset-0 w-full h-full pointer-events-none"
      aria-hidden
    >
      <g transform={`translate(${cx} ${cy}) rotate(${rotationDeg})`}>
        {/* rotation stalk — PowerPoint-style grip on the grid's top edge. It
            rotates WITH the box; SkyCanvas hit-tests data-role, so this is a
            real element hit, valid at any angle/zoom (wave-2 §1). The parent
            SVG is pointer-events-none; this group re-enables itself. */}
        <g data-role="rotate-handle" style={{ pointerEvents: "all", cursor: "grab" }}>
          {/* invisible touch pad: r=64 viewBox units ≈ 46px dia at a 360px
              canvas — keeps the target ≥44px CSS on the smallest layout */}
          <circle cx={0} cy={-gridHalfH - 34} r={64} fill="transparent" stroke="none" />
          <line
            x1={0} y1={-gridHalfH} x2={0} y2={-gridHalfH - 34}
            className="svg-halo" stroke="var(--accent)" strokeWidth={2}
          />
          <circle
            cx={0} cy={-gridHalfH - 34} r={10}
            className="svg-halo" fill="var(--bg)" stroke="var(--accent)" strokeWidth={2}
          />
        </g>
      </g>
    </svg>
  );
}
