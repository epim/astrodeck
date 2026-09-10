// mosaic.ts - FRAME mode's arithmetic and its one server call (hub-sky plan C).
//
// THE SERVER IS CANONICAL. `POST /api/framing/mosaic` is what the sequence
// engine will slew to, so the panels that reach the plan come from there and the
// byte-identical client mirror (`lib/framing.ts`'s `mosaicGrid`) is only the
// offline fallback - exactly the split `AtlasView.computePanels` already makes.
// A phone that computed its own panel centres and sent them would be a second
// truth about where the telescope points.
//
// THE 0.15 OVERLAP IS NOT A DEFAULT, IT IS A CORRECTION. `store.openFraming`
// seeds `mosaic.overlap = 0.25` (the Atlas's own default), while the README's
// mosaic formula, the prototype and the sentence the framing card PRINTS
// ("Panels overlap 15%") all use 0.15. Entering FRAME mode therefore re-sets it
// immediately; without that the card's copy and the panel pitch disagree, and
// the one that reaches the sky is the pitch.

import { api } from "../../../../api";
import { mosaicGrid, mosaicTotalFov } from "../../../../lib/framing";
import { uid } from "../../../../lib/ids";
import type { MosaicPanel, MosaicResult, Target } from "../../../../types";

/** The design's four mosaic tiles, cols x rows (README section 1). */
export const MOSAIC_CHOICES: readonly { cols: number; rows: number }[] = [
  { cols: 1, rows: 1 },
  { cols: 2, rows: 1 },
  { cols: 2, rows: 2 },
  { cols: 3, rows: 2 },
];

/** README section 1: "camera-rotation dial 0-165 deg in 15 deg steps". A sensor
 *  at 180 frames identically to one at 0, so twelve stops cover every distinct
 *  framing (plan H.4). */
export const ROTS: readonly number[] = [0, 15, 30, 45, 60, 75, 90, 105, 120, 135, 150, 165];

/** The overlap the copy promises and the engine is asked for. */
export const OVERLAP = 0.15;

/** `AtlasView.tsx:794`, verbatim and load-bearing: `rotation_deg` starts at 0
 *  for every framing session, so treating 0 as a commanded angle bolts a
 *  rotate-to-PA loop onto every "just show me this" tap. */
export const PA_DEADBAND_DEG = 0.5;

/**
 * The angle this framing actually COMMANDS, or null for "leave the camera
 * alone".
 *
 * One expression, read by the framing card's promise AND by the graph edit that
 * writes the TARGET node's `rotation` - so the sentence on screen and the number
 * on the wire cannot drift apart. They had: the card promised a PA and the flow
 * carried the node vocabulary's shipped 23.4.
 */
export function commandedPa(rotationDeg: number): number | null {
  return rotationDeg > PA_DEADBAND_DEG ? rotationDeg : null;
}

export interface MosaicSpec {
  ra_hours: number;
  dec_deg: number;
  rows: number;
  cols: number;
  overlap: number;
  rotation_deg: number;
  fov_x_deg: number;
  fov_y_deg: number;
}

/** Panels from the engine, falling back to the client mirror when the call
 *  fails. The caller cannot tell which ran, and must not need to: the mirror is
 *  the same algorithm (`lib/framing.ts` mirrors `framing.py`). */
export async function fetchPanels(spec: MosaicSpec): Promise<MosaicPanel[]> {
  try {
    const res = await api.post<MosaicResult>("/api/framing/mosaic", spec);
    return res.panels;
  } catch {
    return mosaicGrid(spec);
  }
}

const DEFAULT_STEP = {
  filter: null,
  exposure_s: 60,
  gain: 100,
  offset: 30,
  binning: 1,
  count: 20,
  frame_type: "light",
};

/**
 * Panels -> plan targets, named and flagged exactly as `AtlasView.sendToPlan`
 * does, so a mosaic sent from the Sky hub and one sent from the Atlas are
 * indistinguishable in the plan (plan H.6: the engine's mosaic mechanism IS
 * N targets sharing a `mosaic_group`, not a flow stage).
 */
export function panelsToTargets(
  panels: MosaicPanel[],
  baseName: string,
  groupId: string | undefined,
  rotationDeg: number,
): Target[] {
  // `rotation_deg` on every panel is the whole rotator hand-off: the sequence
  // engine calls `goto_and_center(..., rotation_deg=target.rotation_deg)` on
  // each slew (`sequence/engine.py:5185, 5880`), and it is the ONLY trigger for
  // rotation. A panel list without it is a mosaic that images at whatever angle
  // the camera happened to be left at, under a card promising an angle.
  const many = panels.length > 1;
  return panels.map((p) => ({
    id: uid(),
    name: many ? `${baseName} ${p.row + 1}-${p.col + 1}` : baseName,
    ra_hours: p.ra_hours,
    dec_deg: p.dec_deg,
    center: true,
    autofocus_first: p.row === 0 && p.col === 0,
    calibration: false,
    rotation_deg: rotationDeg,
    mosaic_group: many ? groupId : undefined,
    steps: [{ ...DEFAULT_STEP, id: uid() }],
  })) as Target[];
}

/**
 * The plan's dedupe key for this framing session.
 *
 * `store.addTargetsToPlan` REPLACES every existing target carrying the group
 * before appending, so re-framing the same object updates its panels instead of
 * silently doubling them. A catalogued object groups by its id; a free-roam
 * session groups by the stable `freeroamId` the store seeded - which is the
 * whole reason that field exists (`store.ts:1272-1275`).
 */
export function mosaicGroupId(f: {
  target?: { id: string } | undefined;
  freeroamId?: string;
}): string | undefined {
  return f.target?.id ?? f.freeroamId;
}

/** What every panel target is named after. `AtlasView.tsx:727` prefers the
 *  catalogue ID over the display name, because "M31 1-2" is what an operator
 *  finds in the plan and in the frame filenames; "Sky" is the free-roam case. */
export function mosaicBaseName(f: {
  target?: { id?: string; name?: string } | undefined;
}): string {
  return f.target?.id ?? f.target?.name ?? "Sky";
}

/**
 * Does this framing session describe the target the sheet is about?
 *
 * The framing slice is GLOBAL - one session, shared with the Atlas - so a
 * framing kept for M31 was being drawn over a flow generated for M42 and fed
 * into its payload (review #3). Every consumer of `framing.panels` has to ask
 * this first, and it is one function so no consumer can ask it a different way.
 *
 * A free-roam session matches only the typed/patch target it was framed at,
 * which the quick sheet identifies by the same `freeroamId` string.
 */
export function framingMatches(
  f: { target?: { id?: string; name?: string } | undefined; freeroamId?: string } | null,
  targetId: string | null,
): boolean {
  if (!f || !targetId) return false;
  return f.target?.id === targetId
    || f.target?.name === targetId
    || f.freeroamId === targetId;
}

// ------------------------------------------------------------------- copy

/** "2×1 mosaic · 2 panels · rot 30°" / "single frame · rot 0°". */
export function frameText(cols: number, rows: number, rot: number): string {
  const n = cols * rows;
  const head = n > 1 ? `${cols}×${rows} mosaic · ${n} panels` : "single frame";
  return `${head} · rot ${Math.round(rot)}°`;
}

/** The lock card's kept-framing strip. Shorter than `frameText` on purpose: the
 *  strip sits beside an ADJUST button, so the panel count is one tap away and
 *  the two things worth reading at a glance are the shape and the angle. */
export function framedStrip(cols: number, rows: number, rot: number): string {
  const shape = cols * rows > 1 ? `${cols}×${rows} mosaic` : "single frame";
  return `Framed · ${shape} · rot ${Math.round(rot)}°`;
}

/** The framing card's right-hand meta: "2 panels · 4.7° × 1.7° · rot 30°", or
 *  the single-frame field of view when there is only one panel. */
export function framingMeta(
  cols: number,
  rows: number,
  rot: number,
  fovX: number,
  fovY: number,
  overlap = OVERLAP,
): string {
  const n = cols * rows;
  const rotTail = `rot ${Math.round(rot)}°`;
  if (!(fovX > 0) || !(fovY > 0)) {
    return n > 1 ? `${n} panels · ${rotTail}` : rotTail;
  }
  const total = mosaicTotalFov(cols, rows, overlap, fovX, fovY);
  const w = total.total_fov_x_deg.toFixed(1);
  const h = total.total_fov_y_deg.toFixed(1);
  return n > 1
    ? `${n} panels · ${w}° × ${h}° · ${rotTail}`
    : `${fovX.toFixed(1)}° × ${fovY.toFixed(1)}° · ${rotTail}`;
}

// --------------------------------------------------------------- overlay

export interface PanelRect {
  row: number;
  col: number;
  /** Top-left in box px, BEFORE the whole grid is rotated about (cx, cy). */
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * The kept framing, drawn on the schematic finder after DONE (proto's
 * `a_setPanels`). Screen geometry only: the grid is laid out on the tangent
 * plane in px and then the whole group is rotated about the lock, which is what
 * the prototype does and what `FovOverlay` does on the Atlas.
 *
 * `frameW`/`frameH` are the reticle rectangle already in px, so this never has
 * to know the optics - and cannot disagree with the reticle the user framed by.
 */
export function panelRects(
  cx: number,
  cy: number,
  cols: number,
  rows: number,
  frameW: number,
  frameH: number,
  overlap = OVERLAP,
): PanelRect[] {
  const stepX = frameW * (1 - overlap);
  const stepY = frameH * (1 - overlap);
  const out: PanelRect[] = [];
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      const px = cx + (c - (cols - 1) / 2) * stepX;
      // Screen y grows downward while row 0 is the TOP row, so rows advance
      // downward here - the opposite sign from `mosaicGrid`'s sky-plane gy.
      const py = cy + (r - (rows - 1) / 2) * stepY;
      out.push({ row: r, col: c, x: px - frameW / 2, y: py - frameH / 2, w: frameW, h: frameH });
    }
  }
  return out;
}
