// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// PanelLayer - a mosaic's panels on the Atlas sky, drawn from the SERVER's
// panel coordinates (#189 S4 item 2; spec 2026-09-23 flows mosaic, 2.3).
//
// WHERE A PANEL IS COMES FROM ONE PLACE. Each panel arrives as the sky
// position `POST /api/framing/mosaic` answered for it (or, while the operator
// drags, the client mirror `lib/framing.ts` mosaicGrid, which is byte-for-byte
// the same arithmetic). Its centre goes through `skyToView` and its outline
// through `fovCornersSky`, the projection every other claim on this canvas
// uses, so a panel is glued to the survey pixels under it through every pan and
// zoom. Two layouts that already exist are deliberately NOT used:
//   * FovOverlay's screen-space grid is a point reflection of the server's
//     layout: its row 0 is drawn at the bottom and its col 0 on the left;
//   * `panelRects` (next/hubs/sky/frame/mosaic.ts) mirrors the columns.
// A label taken from either names the wrong patch of sky. The server's panel
// (0, 0) is the NORTH-WEST corner at angle 0 (framing.py compute_mosaic), so on
// this north-up, east-left chart its label `1-1` sits TOP RIGHT, which is the
// same `<name> r-c` the Plan uses.
//
// STATES BY SHAPE, NEVER BY HUE. The canvas is read at night under a red filter
// where --accent and --good are the same red (index.css palette note), so each
// state has a drawing a mono screen and a colour-blind eye can still read
// (spec 2.3's table): pending a thin stroke, skipped dashed with an X, shooting
// a thick stroke with corner ticks, done a diagonal hatch, set aside dotted
// with "!". Every stroke is one ink.
//
// Two pieces, because they live in two layers of SkyCanvas: `PanelShapes` is
// SVG geometry for the scaled viewBox (NO text there, C3-A2), `PanelLabels` is
// real CSS px on the HTML layer. The corner ticks point OUTWARD, crop-mark
// style, so they cannot be read as PointingFrame's inward viewfinder brackets
// (that shape means "where the tube IS").

import { memo, type JSX } from "react";
import {
  fovCornersSky, skyToView,
  type AtlasViewGeom, type ViewPoint,
} from "../../lib/atlasFov";

export type PanelState = "pending" | "skipped" | "shooting" | "done" | "set_aside";

/** The five, in the spec's table order. */
export const PANEL_STATES: readonly PanelState[] =
  ["pending", "skipped", "shooting", "done", "set_aside"];

/** One panel of a mosaic, as the canvas draws it. */
export interface SkyPanel {
  /** 0-based, in the SERVER's convention: row 0 is the north edge and col 0
   *  the west edge at angle 0. The label adds one to each. */
  row: number;
  col: number;
  /** J2000, straight from the server's answer (or the client mirror). */
  ra_hours: number;
  dec_deg: number;
  /** 1-based run order, when the caller knows it. */
  order?: number;
  state: PanelState;
  /** What a screen reader is told of the panel in place of its state's own
   *  words, when the caller knows more than the shape can say: the Target
   *  modal's current panel of a paused, held or stopping run is drawn as
   *  `shooting` and is not being shot (#451). */
  words?: string;
  /** The angle the server laid this panel out at (its answer carries one per
   *  panel). Absent, the canvas's own `rotationDeg` is used. */
  rotation_deg?: number;
}

/** The single-frame field a grid was TILED for, degrees at bin 1. */
export interface PanelFov {
  fov_x_deg: number;
  fov_y_deg: number;
}

export interface PlacedPanel {
  panel: SkyPanel;
  /** The panel centre in viewBox px. */
  center: ViewPoint;
  /** Four corners in viewBox px, cyclic. null when the panel size is unknown
   *  (nothing to outline) or a corner is past the projection horizon (no
   *  honest polygon: see atlasFov TAN_HORIZON_DEG). */
  outline: ViewPoint[] | null;
}

/** `1-1` for server panel (0, 0). */
export function panelLabel(p: { row: number; col: number }): string {
  return `${p.row + 1}-${p.col + 1}`;
}

/**
 * Every panel that has a place on this map, in viewBox px. A panel past the
 * projection horizon is left out rather than drawn mirrored (skyToView's
 * null). `rotationDeg` is the fallback angle for a panel that carries none.
 */
export function placePanels(
  panels: readonly SkyPanel[],
  fov: PanelFov | null | undefined,
  rotationDeg: number,
  g: AtlasViewGeom,
): PlacedPanel[] {
  const sized = !!fov && fov.fov_x_deg > 0 && fov.fov_y_deg > 0;
  const out: PlacedPanel[] = [];
  for (const panel of panels) {
    const center = skyToView(panel.ra_hours, panel.dec_deg, g);
    if (center === null) continue;
    let outline: ViewPoint[] | null = null;
    if (sized) {
      const pa = Number.isFinite(panel.rotation_deg) ? (panel.rotation_deg as number) : rotationDeg;
      const corners = fovCornersSky(
        { ra_hours: panel.ra_hours, dec_deg: panel.dec_deg },
        fov!.fov_x_deg, fov!.fov_y_deg, pa,
      ).map((c) => skyToView(c.ra_hours, c.dec_deg, g));
      outline = corners.includes(null) ? null : (corners as ViewPoint[]);
    }
    out.push({ panel, center, outline });
  }
  return out;
}

/** Inside a convex quad (the gnomonic image of a rectangle is one), either
 *  winding: every edge's cross product has the same sign. */
function insideQuad(q: readonly ViewPoint[], x: number, y: number): boolean {
  let sign = 0;
  for (let i = 0; i < q.length; i++) {
    const a = q[i];
    const b = q[(i + 1) % q.length];
    const cross = (b.x - a.x) * (y - a.y) - (b.y - a.y) * (x - a.x);
    if (cross === 0) continue;
    const s = cross > 0 ? 1 : -1;
    if (sign === 0) sign = s;
    else if (s !== sign) return false;
  }
  return true;
}

/**
 * The panel a point (viewBox px) is in: the one whose outline holds it, and in
 * an overlap the one whose centre is nearest, since the 25% overlap is shared
 * sky and the nearer panel is the one the eye reads there. A panel with no
 * outline (size unknown) is hit within `radius` of its centre.
 */
export function panelAt(
  placed: readonly PlacedPanel[],
  x: number,
  y: number,
  radius: number,
): PlacedPanel | null {
  let best: PlacedPanel | null = null;
  let bestD = Infinity;
  for (const pp of placed) {
    const d = Math.hypot(x - pp.center.x, y - pp.center.y);
    const hit = pp.outline ? insideQuad(pp.outline, x, y) : d <= radius;
    if (hit && d < bestD) {
      best = pp;
      bestD = d;
    }
  }
  return best;
}

// ------------------------------------------------------------------ drawing
/** What each state is drawn with, colour deliberately absent. Stroke widths
 *  and dashes are viewBox units (the canvas is 1000 across). */
interface PanelShape {
  strokeWidth: number;
  dash: string | null;
  hatch: boolean;
  ticks: boolean;
  cross: boolean;
  flag: "!" | null;
  /** Round caps turn the short dashes of a dotted stroke into dots. */
  cap?: "round";
}

const PANEL_SHAPE: Record<PanelState, PanelShape> = {
  pending: { strokeWidth: 1.5, dash: null, hatch: false, ticks: false, cross: false, flag: null },
  skipped: { strokeWidth: 1.5, dash: "10 7", hatch: false, ticks: false, cross: true, flag: null },
  shooting: { strokeWidth: 4, dash: null, hatch: false, ticks: true, cross: false, flag: null },
  done: { strokeWidth: 1.5, dash: null, hatch: true, ticks: false, cross: false, flag: null },
  set_aside: { strokeWidth: 1.5, dash: "1.5 6", hatch: false, ticks: false, cross: false, flag: "!", cap: "round" },
};

/** The words a screen reader gets for a state (the shapes are aria-hidden). */
const STATE_WORDS: Record<PanelState, string> = {
  pending: "pending",
  skipped: "skipped",
  shooting: "shooting now",
  done: "done",
  set_aside: "set aside tonight",
};

const INK = "var(--accent)";

function pathOf(pts: readonly ViewPoint[]): string {
  return `M ${pts.map((p) => `${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" L ")} Z`;
}

function seg(a: ViewPoint, b: ViewPoint): string {
  return `M ${a.x.toFixed(2)} ${a.y.toFixed(2)} L ${b.x.toFixed(2)} ${b.y.toFixed(2)}`;
}

/** Crop marks: each edge carried on past its corner by `frac` of its length,
 *  two per corner, eight in all. Outward, so never PointingFrame's brackets. */
function tickPath(q: readonly ViewPoint[], frac: number): string {
  const parts: string[] = [];
  for (let i = 0; i < q.length; i++) {
    const p = q[i];
    for (const n of [q[(i + 1) % q.length], q[(i + q.length - 1) % q.length]]) {
      parts.push(seg(p, { x: p.x + (p.x - n.x) * frac, y: p.y + (p.y - n.y) * frac }));
    }
  }
  return parts.join(" ");
}

export interface PanelShapesProps {
  placed: readonly PlacedPanel[];
  /** viewBox edge (SkyCanvas uses 1000), for the off-canvas cull. */
  view: number;
  /** A document-unique id for the done-hatch pattern. */
  hatchId: string;
  /** The grid's centre in viewBox px (frameCenter, or the view centre), where
   *  the target cross goes. null draws none. */
  frameCenter: ViewPoint | null;
  /** The framed object's size ellipse radius, viewBox px, drawn at the grid
   *  centre as FovOverlay draws it (this layer replaces FovOverlay). */
  objectRPx?: number | null;
}

export const PanelShapes = memo(function PanelShapes(props: PanelShapesProps): JSX.Element {
  const { placed, view, hatchId, frameCenter, objectRPx } = props;
  const crossLen = view * 0.018;
  return (
    <g aria-hidden data-role="panel-layer">
      <defs>
        {/* 45-degree hatch in viewBox units: 14 units is about 5 CSS px at a
            360 px canvas, dense enough to read as filled, open enough to let
            the survey through. */}
        <pattern
          id={hatchId}
          patternUnits="userSpaceOnUse"
          width={14}
          height={14}
          patternTransform="rotate(45)"
        >
          <line x1={0} y1={0} x2={0} y2={14} stroke={INK} strokeWidth={3} opacity={0.8} />
        </pattern>
      </defs>
      {objectRPx != null && objectRPx > 0 && frameCenter && (
        <ellipse
          cx={frameCenter.x}
          cy={frameCenter.y}
          rx={objectRPx}
          ry={objectRPx}
          className="svg-halo"
          fill="none"
          stroke={INK}
          strokeWidth={1.5}
          strokeDasharray="6 5"
          opacity={0.85}
        />
      )}
      {placed.map((pp) => {
        const { panel, center, outline } = pp;
        // Off-canvas is normal (the grid is glued to the sky); a path millions
        // of units wide is pointless work, not a picture. Same cull as
        // PointingFrame.
        const stray = Math.max(Math.abs(center.x - view / 2), Math.abs(center.y - view / 2));
        if (!Number.isFinite(stray) || stray > view * 1.5) return null;
        const shape = PANEL_SHAPE[panel.state] ?? PANEL_SHAPE.pending;
        return (
          <g
            key={`${panel.row}-${panel.col}`}
            data-role="panel"
            data-row={panel.row}
            data-col={panel.col}
            data-state={panel.state}
          >
            {outline && (
              <path
                data-mark="outline"
                d={pathOf(outline)}
                className="svg-halo"
                fill={shape.hatch ? `url(#${hatchId})` : "none"}
                stroke={INK}
                strokeWidth={shape.strokeWidth}
                strokeDasharray={shape.dash ?? undefined}
                strokeLinecap={shape.cap}
                opacity={0.95}
              />
            )}
            {outline && shape.cross && (
              <path
                data-mark="cross"
                d={`${seg(outline[0], outline[2])} ${seg(outline[1], outline[3])}`}
                className="svg-halo"
                fill="none"
                stroke={INK}
                strokeWidth={1.5}
                opacity={0.9}
              />
            )}
            {outline && shape.ticks && (
              <path
                data-mark="ticks"
                d={tickPath(outline, 0.12)}
                className="svg-halo"
                fill="none"
                stroke={INK}
                strokeWidth={3}
                strokeLinecap="butt"
              />
            )}
          </g>
        );
      })}
      {frameCenter && (
        <path
          data-role="panel-frame-center"
          data-x={frameCenter.x}
          data-y={frameCenter.y}
          d={`M ${frameCenter.x - crossLen} ${frameCenter.y} L ${frameCenter.x + crossLen} ${frameCenter.y} M ${frameCenter.x} ${frameCenter.y - crossLen} L ${frameCenter.x} ${frameCenter.y + crossLen}`}
          className="svg-halo"
          stroke={INK}
          strokeWidth={1.5}
          opacity={0.9}
        />
      )}
    </g>
  );
});

export interface PanelLabelsProps {
  placed: readonly PlacedPanel[];
  /** CSS px per viewBox unit (boxPx / 1000). */
  scale: number;
  /** The canvas edge in CSS px: a label whose panel centre is off the canvas
   *  is not drawn. */
  boxPx: number;
  /** Given, each label is a button (the keyboard channel for what a tap on
   *  the panel does); absent, plain text. */
  onPanelTap?: (row: number, col: number) => void;
}

/**
 * The HTML labels: run-order number and `r-c`, centred on each panel. The
 * layer clips (overflow hidden), so a label near the edge cannot widen the
 * page, which is what the "Object size" legend once did at 390 px. Pointer
 * events are OFF, as on the object labels: a pannable canvas cannot afford
 * buttons that swallow the start of a drag, so taps are served by the canvas's
 * own hit test and these stay the keyboard and screen-reader channel.
 */
export function PanelLabels(props: PanelLabelsProps): JSX.Element | null {
  const { placed, scale, boxPx, onPanelTap } = props;
  const shown = placed.filter((pp) => {
    const x = pp.center.x * scale;
    const y = pp.center.y * scale;
    return x >= 0 && x <= boxPx && y >= 0 && y <= boxPx;
  });
  if (shown.length === 0) return null;
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none" data-role="panel-labels">
      {shown.map((pp) => {
        const { panel } = pp;
        const shape = PANEL_SHAPE[panel.state] ?? PANEL_SHAPE.pending;
        const name = panelLabel(panel);
        const body = (
          <>
            {shape.flag && <span data-role="panel-flag" aria-hidden className="font-bold">{shape.flag}</span>}
            {panel.order != null && (
              <span data-role="panel-order" className="px-0.5 border border-current leading-[12px]">
                {panel.order}
              </span>
            )}
            <span data-role="panel-rc">{name}</span>
            <span className="sr-only">, {panel.words ?? STATE_WORDS[panel.state] ?? panel.state}</span>
          </>
        );
        const common = {
          "data-role": "panel-label",
          "data-row": panel.row,
          "data-col": panel.col,
          "data-state": panel.state,
          className:
            "absolute inline-flex items-center gap-1 text-[12px] mono px-1 bg-black/60 " +
            "whitespace-nowrap leading-[14px] text-ink border border-transparent " +
            "focus-visible:outline-none focus-visible:border-accent",
          style: {
            left: pp.center.x * scale,
            top: pp.center.y * scale,
            transform: "translate(-50%, -50%)",
          },
        };
        const key = `${panel.row}-${panel.col}`;
        return onPanelTap ? (
          <button
            key={key}
            type="button"
            {...common}
            onClick={() => onPanelTap(panel.row, panel.col)}
          >
            {body}
          </button>
        ) : (
          <span key={key} {...common}>{body}</span>
        );
      })}
    </div>
  );
}
