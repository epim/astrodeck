// FieldOverlay.tsx — the catalogued objects in THIS frame, on THIS frame (#182).
//
// Draws only from `preview.field.objects`, which the server populates ONLY when
// the preview it rides on IS the frame that was plate-solved. That gate lives on
// the server (`hub._field_block`) for a reason: a WCS from the previous frame
// drawn over this one produces markers that look right and are not, and this
// codebase has paid for that class of defect more than once. Nothing here
// re-derives a position, and nothing here falls back to the mount's reported
// pointing — the AM5 has no brake and has been found 50° from where it claimed.
//
// TWO SCALE FACTORS, NOT ONE. The display JPEG's height is `int(h*scale)`,
// floored, so `dispH/dataH` and `dispW/dataW` differ by up to a pixel;
// `CropOverlay`/`cropRoi.ts` already use separate sx/sy for exactly this reason
// while `StarOverlay` uses a single x-derived scale. A star ring being a pixel
// out is invisible; a galaxy outline being a pixel out reads as a bad solve.
//
// Text inside a CSS-transformed SVG scales with the zoom, so every font size and
// stroke width here is divided by the live zoom — the same counter-scaling
// `StarOverlay` does for its decimation cells — which keeps a label readable at
// 25% and stops it swallowing the frame at 800%.

import { useMemo } from "react";
import type { FieldObject } from "../../types";

export interface FrameMark {
  obj: FieldObject;
  /** display-image px */
  cx: number;
  cy: number;
  /** semi-axes in display px. Two of them, because sx !== sy — see the header. */
  r: number;
  ry: number;
  extended: boolean;
  labelled: boolean;
}

/** Smallest drawn radius, display px. Below this an outline is a smudge and the
 *  object may as well be a point. */
const MIN_R = 7;
/** An outline wider than this fraction of the frame has stopped outlining
 *  anything — at that size the object IS the picture and the survey underneath
 *  says so better than a dashed ellipse. */
const MAX_R_FRAC = 0.42;

/** How many objects get TEXT. Markers are not budgeted: losing a label is a
 *  readability decision, losing the mark is losing the information. */
export function frameLabelBudget(stageW: number): number {
  return Math.max(4, Math.min(12, Math.round(stageW / 80)));
}

/** Place every object, then decide which ones can carry text.
 *
 *  Rows arrive in the server's score order (most worth naming first), so the
 *  greedy pass below gives the labels to the objects a human would look for.
 *  Collision is tested in DISPLAY px against the boxes already placed; an object
 *  that cannot fit its text keeps its marker and its identity, it just goes
 *  quiet — which is the correct trade in a frame full of NGC numbers.
 */
export function frameMarks(
  objects: FieldObject[],
  opts: { sx: number; sy: number; dispW: number; dispH: number; budget: number },
): FrameMark[] {
  const { sx, sy, dispW, dispH, budget } = opts;
  const maxR = Math.min(dispW, dispH) * MAX_R_FRAC;
  const placed: Array<{ x: number; y: number; w: number; h: number }> = [];
  const out: FrameMark[] = [];
  let labels = 0;
  for (const obj of objects) {
    const cx = obj.x * sx;
    const cy = obj.y * sy;
    if (!Number.isFinite(cx) || !Number.isFinite(cy)) continue;
    const trueR = (obj.size_px * sx) / 2;
    const extended = trueR >= MIN_R;
    const r = Math.min(Math.max(trueR, MIN_R), maxR);
    const ry = Math.min(Math.max((obj.size_px * sy) / 2, MIN_R), maxR);
    let labelled = false;
    if (labels < budget) {
      // One anchor, to the east of the mark, and a plain overlap test. Four
      // anchors would place a few more labels; on a frame this is not worth the
      // second geometry, because unlike the Atlas the picture does not pan under
      // the labels — they are computed once per frame, not per animation frame.
      const w = Math.max(24, obj.label.length * 6.2);
      const box = { x: cx + r + 4, y: cy - 7, w, h: 14 };
      const clash = placed.some(
        (b) => Math.abs(b.x - box.x) < (b.w + box.w) / 2
          && Math.abs(b.y - box.y) < (b.h + box.h) / 2,
      );
      if (!clash) {
        placed.push({ x: box.x + w / 2, y: box.y + 7, w, h: box.h });
        labelled = true;
        labels += 1;
      }
    }
    out.push({ obj, cx, cy, r, ry, extended, labelled });
  }
  return out;
}

export function FieldOverlay({
  objects,
  dispW,
  dispH,
  dataW,
  dataH,
  scale,
}: {
  objects: FieldObject[];
  dispW: number;
  dispH: number;
  dataW: number;
  dataH: number;
  /** live zoom, so text and strokes stay a constant SCREEN size */
  scale: number;
}) {
  const marks = useMemo(() => {
    if (!dataW || !dataH || !dispW || !dispH) return [];
    return frameMarks(objects, {
      sx: dispW / dataW,
      sy: dispH / dataH,
      dispW,
      dispH,
      budget: frameLabelBudget(dispW),
    });
  }, [objects, dispW, dispH, dataW, dataH]);

  if (marks.length === 0) return null;
  const z = Math.max(scale, 1e-4);
  const font = 11 / z;

  return (
    <g data-field-overlay="" aria-hidden="true">
      {marks.map((m) => {
        const o = m.obj;
        // SHAPE carries the kind, never colour alone: this app runs in the dark
        // under a red filter, where every accent collapses toward one hue.
        const shape = m.extended ? (
          <ellipse
            cx={m.cx}
            cy={m.cy}
            rx={m.r}
            ry={m.ry}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.4}
            strokeDasharray="5 3"
            vectorEffect="non-scaling-stroke"
          />
        ) : (
          <circle
            cx={m.cx}
            cy={m.cy}
            r={MIN_R}
            fill="none"
            stroke="var(--accent)"
            strokeWidth={1.4}
            vectorEffect="non-scaling-stroke"
          />
        );
        return (
          <g key={`${o.id}:${o.x},${o.y}`} data-field-object={o.id}>
            {shape}
            {m.labelled && (
              <text
                x={m.cx + m.r + 4 / z}
                y={m.cy + font * 0.35}
                fontSize={font}
                fill="var(--accent)"
                stroke="var(--halo)"
                strokeWidth={2.5 / z}
                paintOrder="stroke"
                style={{ pointerEvents: "none" }}
              >
                {o.label}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}

export default FieldOverlay;
