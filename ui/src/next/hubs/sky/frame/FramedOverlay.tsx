// FramedOverlay.tsx - the panels that stay drawn on the schematic finder after
// DONE (proto `a_setPanels`, plan C).
//
// WHY THIS EXISTS AT ALL. DONE leaves FRAME mode, so the survey canvas goes away
// and the schematic finder comes back - and the framing the user just spent a
// minute on would vanish with it. The faint cyan rectangles are the only thing
// on the screen that says the run will shoot six panels rather than one, and the
// label repeats the shape and the angle so it can be read without opening the
// lock card.
//
// It is an overlay rather than part of `SkyView` because `finder/` belongs to
// another task; it is positioned against the same wrapper the finder fills, so
// the rectangles land where the reticle was.
//
// The geometry is SCREEN geometry (`panelRects` in px off the reticle size), not
// a re-projection of the server's panel centres. That is deliberate: the panels
// the ENGINE will slew to come from `POST /api/framing/mosaic` and live in the
// plan; this is a picture of the same grid drawn from the same reticle the user
// framed with, so the picture cannot disagree with the rectangle they aimed.

import type { JSX } from "react";
import { panelRects } from "./mosaic";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";

export interface FramedOverlayProps {
  boxW: number;
  boxH: number;
  /** Reticle rectangle in px - zero when the optics are unknown, in which case
   *  nothing is drawn: a panel grid with no frame size would be a made-up one. */
  frameW: number;
  frameH: number;
  cols: number;
  rows: number;
  overlap: number;
  rotationDeg: number;
  /** Screen position of the framed target, box px. */
  cx: number;
  cy: number;
  label: string;
}

export function FramedOverlay({
  boxW, boxH, frameW, frameH, cols, rows, overlap, rotationDeg, cx, cy, label,
}: FramedOverlayProps): JSX.Element | null {
  if (!(frameW > 0) || !(frameH > 0)) return null;
  const rects = panelRects(cx, cy, cols, rows, frameW, frameH, overlap);
  const labelY = cy - (frameH * rows) / 2 - 14;

  return (
    <div
      data-testid="sky-framed-overlay"
      style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden", borderRadius: 16 }}
    >
      <svg viewBox={`0 0 ${boxW} ${boxH}`} preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
        <g transform={`rotate(${rotationDeg} ${cx} ${cy})`}>
          {rects.map((r) => (
            <rect
              key={`${r.row}-${r.col}`}
              x={r.x}
              y={r.y}
              width={r.w}
              height={r.h}
              fill="rgba(0,210,255,.1)"
              stroke="var(--accent)"
              strokeWidth={1.2}
            />
          ))}
        </g>
      </svg>
      <div
        style={{
          position: "absolute",
          left: cx,
          top: Math.max(4, labelY),
          transform: "translateX(-50%)",
          fontFamily: MONO,
          fontSize: 10,
          color: "var(--accent)",
          whiteSpace: "nowrap",
          textShadow: "0 1px 4px rgba(6,7,11,.9)",
        }}
      >
        {label}
      </div>
    </div>
  );
}
