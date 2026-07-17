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
// `rotation_deg`. The active-panel emphasis (thicker stroke + corner ticks) was
// removed as a never-fed contract — no caller ever drove which panel was
// emphasized (wave-2 §4).
//
// The rotate-handle grip used to live here (wave-2 §1) but moved out to its own
// component, RotateHandle.tsx, mounted by SkyCanvas AFTER the HTML label layer
// so it always paints on top — this SVG's DOM position (BEFORE the label layer)
// meant the handle could render behind the "Your camera" label at some canvas
// widths, invisible and ungrabbable (wave-2 G3). See RotateHandle.tsx for the
// full root-cause note.

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
  /** Optional catalog-size ellipse ("Object size"), semi-axes in degrees. */
  objectSemiMajorDeg?: number | null;
  objectSemiMinorDeg?: number | null;
  /** Whether optics are usable; false => dashed placeholder frame. */
  haveOptics: boolean;
}

// A single rotated rectangle path centered at (0,0) before the group transform.
function rectPath(halfW: number, halfH: number): string {
  return `M ${-halfW} ${-halfH} L ${halfW} ${-halfH} L ${halfW} ${halfH} L ${-halfW} ${halfH} Z`;
}

export const FovOverlay = memo(function FovOverlay(props: FovOverlayProps): JSX.Element {
  const {
    view, cx, cy, pxPerDeg, fovXDeg, fovYDeg, rotationDeg,
    rows, cols, overlap,
    objectSemiMajorDeg, objectSemiMinorDeg, haveOptics,
  } = props;

  const frameW = fovXDeg * pxPerDeg;
  const frameH = fovYDeg * pxPerDeg;
  const halfW = frameW / 2;
  const halfH = frameH / 2;
  const stepX = frameW * (1 - overlap);
  const stepY = frameH * (1 - overlap);

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
  const panels: JSX.Element[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const gx = (c - (cols - 1) / 2) * stepX;
      const gy = ((rows - 1) / 2 - r) * stepY;
      panels.push(
        <g key={`p-${r}-${c}`} transform={`translate(${gx} ${gy})`}>
          <path
            d={rectPath(halfW, halfH)}
            className="svg-halo"
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.5}
            strokeDasharray={haveOptics ? undefined : "8 6"}
            opacity={haveOptics ? 0.95 : 0.7}
          />
        </g>,
      );
    }
  }

  return (
    <g aria-hidden>
      {ellipse}
      <g transform={`translate(${cx} ${cy}) rotate(${rotationDeg})`}>
        {panels}
      </g>
      {targetCross}
    </g>
  );
});
