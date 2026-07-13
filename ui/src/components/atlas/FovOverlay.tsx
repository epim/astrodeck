// FovOverlay — pure SVG geometry for the Atlas sensor rectangle / mosaic grid
// (design spec §6). NO survey image, NO HTML text: SkyCanvas owns the <img> and
// the HTML label layer; this component only emits <svg> shapes into the parent's
// 0..1000 viewBox. Keeping it geometry-only is what lets every text label live in
// real CSS px on the HTML layer (spec §6 C3-A2) and every stroke carry the black
// `.svg-halo` underlay so the rectangle never collapses into a same-hue red survey
// in night mode (C3-A1).
//
// Coordinate model: the parent passes a `project(ra,dec) -> {x,y}` mapping into
// the 1000×1000 viewBox (px_per_deg = 1000 / fovZoomDeg, centered). We draw the
// frame(s) at the projected center, sized `fov·px_per_deg`, rotated by
// `rotation_deg`. The ACTIVE panel is emphasized by a thicker stroke + corner
// ticks (NOT a translucent fill — invisible red-on-red at night, C3-A12).

import { memo, type JSX } from "react";

export interface FovOverlayProps {
  /** viewBox edge length (square). SkyCanvas uses 1000. */
  view: number;
  /** Projected center of the survey crop in viewBox px (the target/center). */
  cx: number;
  cy: number;
  /** Survey-plane px per degree (1000 / fovZoomDeg). Uniform — TAN cutout. */
  pxPerDeg: number;
  /** Single-frame footprint in degrees (bin-1). */
  fovXDeg: number;
  fovYDeg: number;
  /** Position angle of the whole mosaic, degrees (clockwise on the N-up image). */
  rotationDeg: number;
  /** Mosaic shape. 1×1 draws a single rectangle. */
  rows: number;
  cols: number;
  overlap: number; // 0..0.5
  /** Index of the emphasized panel in row-major order, or null for none. */
  activeIndex?: number | null;
  /** Optional catalog-size ellipse ("Object size"), semi-axes in degrees. */
  objectSemiMajorDeg?: number | null;
  objectSemiMinorDeg?: number | null;
  /** Whether optics are usable; false => dashed placeholder frame. */
  haveOptics: boolean;
  /** Draw the grabbable rotation stalk on the box's top edge (wave-2 §1). */
  rotateHandle?: boolean;
}

// A single rotated rectangle path centered at (0,0) before the group transform.
function rectPath(halfW: number, halfH: number): string {
  return `M ${-halfW} ${-halfH} L ${halfW} ${-halfH} L ${halfW} ${halfH} L ${-halfW} ${halfH} Z`;
}

// Corner ticks for the active panel (the panel-bracket motif). Returns 8 short
// segments at the four corners, inset slightly so they read as brackets.
function cornerTicks(halfW: number, halfH: number, len: number): string {
  const x = halfW;
  const y = halfH;
  return [
    // top-left
    `M ${-x} ${-y + len} L ${-x} ${-y} L ${-x + len} ${-y}`,
    // top-right
    `M ${x - len} ${-y} L ${x} ${-y} L ${x} ${-y + len}`,
    // bottom-right
    `M ${x} ${y - len} L ${x} ${y} L ${x - len} ${y}`,
    // bottom-left
    `M ${-x + len} ${y} L ${-x} ${y} L ${-x} ${y - len}`,
  ].join(" ");
}

export const FovOverlay = memo(function FovOverlay(props: FovOverlayProps): JSX.Element {
  const {
    view, cx, cy, pxPerDeg, fovXDeg, fovYDeg, rotationDeg,
    rows, cols, overlap, activeIndex = null,
    objectSemiMajorDeg, objectSemiMinorDeg, haveOptics, rotateHandle = false,
  } = props;

  const frameW = fovXDeg * pxPerDeg;
  const frameH = fovYDeg * pxPerDeg;
  const halfW = frameW / 2;
  const halfH = frameH / 2;
  const stepX = frameW * (1 - overlap);
  const stepY = frameH * (1 - overlap);
  // Overall mosaic-grid half-height — the stalk hangs off the TOP of the whole
  // grid (not one panel) so it never overlaps a frame. Grid is symmetric about
  // the origin: half-height = one panel's half + half the row span.
  const gridHalfH = halfH + ((rows - 1) * stepY) / 2;
  const tickLen = Math.max(6, Math.min(halfW, halfH) * 0.28);

  // Object-size ellipse (suppressed when size unknown — stars/doubles, C3-A11).
  const ellipse =
    objectSemiMajorDeg && objectSemiMajorDeg > 0 ? (
      <ellipse
        cx={cx}
        cy={cy}
        rx={objectSemiMajorDeg * pxPerDeg}
        ry={(objectSemiMinorDeg ?? objectSemiMajorDeg) * pxPerDeg}
        className="svg-halo"
        fill="none"
        stroke="var(--accent)"
        strokeWidth={1.5}
        strokeDasharray="6 5"
        opacity={0.85}
      />
    ) : null;

  // Target cross at center — orients the eye even before the frame is read.
  const crossLen = view * 0.018;
  const targetCross = (
    <path
      d={`M ${cx - crossLen} ${cy} L ${cx + crossLen} ${cy} M ${cx} ${cy - crossLen} L ${cx} ${cy + crossLen}`}
      className="svg-halo"
      stroke="var(--accent)"
      strokeWidth={1.5}
      opacity={0.9}
    />
  );

  // The frame group: translate to center, rotate by PA, then draw each panel.
  // Walk in the SAME boustrophedon (snake) order as lib/framing.mosaicGrid (odd
  // rows reversed) so the flat `idx` matches the canonical panel list — otherwise
  // activeIndex would mis-highlight on odd rows of a multi-row mosaic.
  const panels: JSX.Element[] = [];
  let idx = 0;
  for (let r = 0; r < rows; r++) {
    const colOrder: number[] = [];
    for (let c = 0; c < cols; c++) colOrder.push(c);
    if (r % 2 === 1) colOrder.reverse();
    for (const c of colOrder) {
      const gx = (c - (cols - 1) / 2) * stepX;
      const gy = ((rows - 1) / 2 - r) * stepY;
      const active = activeIndex != null && idx === activeIndex;
      panels.push(
        <g key={`p-${r}-${c}`} transform={`translate(${gx} ${gy})`}>
          <path
            d={rectPath(halfW, halfH)}
            className="svg-halo"
            fill="none"
            stroke="var(--accent)"
            strokeWidth={active ? 3 : 1.5}
            strokeDasharray={haveOptics ? undefined : "8 6"}
            opacity={active ? 1 : haveOptics ? 0.95 : 0.7}
          />
          {active && (
            <path
              d={cornerTicks(halfW, halfH, tickLen)}
              className="svg-halo"
              fill="none"
              stroke="var(--accent)"
              strokeWidth={4}
            />
          )}
        </g>,
      );
      idx++;
    }
  }

  return (
    <g aria-hidden>
      {ellipse}
      <g transform={`translate(${cx} ${cy}) rotate(${rotationDeg})`}>
        {panels}
        {/* rotation stalk — PowerPoint-style grip on the box's top edge. It
            rotates WITH the box; SkyCanvas hit-tests data-role, so this is a
            real element hit, valid at any angle/zoom (wave-2 §1). The parent
            SVG is pointer-events-none; this group re-enables itself. */}
        {rotateHandle && (
          <g
            data-role="rotate-handle"
            style={{ pointerEvents: "all", cursor: "grab" }}
          >
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
        )}
      </g>
      {targetCross}
    </g>
  );
});
