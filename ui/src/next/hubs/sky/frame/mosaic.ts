// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// mosaic.ts - FRAME mode's arithmetic, its one server call, and what its door
// hands Send to Flow Wizard (hub-sky plan C; #196, spec 2026-09-23 flows
// mosaic, section 8 S6).
//
// THE SERVER IS CANONICAL. `POST /api/framing/mosaic` is the same layout the
// compile gives a TARGET block, so the panels DONE keeps on the framing come
// from there and the byte-identical client mirror (`lib/framing.ts`'s
// `mosaicGrid`) is only the offline fallback. A phone that computed its own
// panel centres would be a second truth about where the telescope points.
//
// THE FRAMING GOES FORWARD THROUGH THE WIZARD (#196). SEND TO FLOW WIZARD, on
// the framing card and on the quick sheet of a kept mosaic, opens the shared
// wizard pre-filled from the framing (`framingPrefill`), which writes ONE
// TARGET block with the loop wire into a flow. The framing used to reach the
// night as classic Plan targets sharing a `mosaic_group`, shot panel-first,
// beside a flow saved for the centre alone (#154); nothing writes that side
// channel now.
//
// ONE OVERLAP (spec 2.4). This file used to re-set every FRAME session to 0.15
// so that the card's sentence ("Panels overlap 15%") and the panel pitch
// agreed, while the store seeded 0.25 and the server laid a wizard's grid out
// at 0.25. `lib/framing.ts`'s `DEFAULT_OVERLAP` is the one constant now, and
// `OVERLAP` below is that same binding under the name this module always
// exported.

import { api } from "../../../../api";
import { DEFAULT_OVERLAP, mosaicGrid, mosaicTotalFov } from "../../../../lib/framing";
import type { MosaicPanel, MosaicResult } from "../../../../types";
import { decDms, raHms } from "../../session/flows/create/quickPayload";
// TYPE-ONLY, off the door's own opener: the prefill type lives in the shared
// wizard's model (`components/flows/wizard/wizardModel`), and naming it
// through `openFlowWizard` keeps this file from importing the legacy tree
// (r7Parity) while staying the exact type the opener takes.
import type { openFlowWizard } from "../../session/flows/wizard";

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

/** The overlap a FRAME session starts from: `DEFAULT_OVERLAP` itself,
 *  re-exported under this module's old name (a binding, not a copy), so
 *  there is still exactly one number. */
export { DEFAULT_OVERLAP as OVERLAP };

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
 * carried 23.4, the node vocabulary's default until #150 (an older server still
 * ships it, which is why the -1 is still written explicitly).
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

// ------------------------------------------------ Send to Flow Wizard (#196)
// The door's label is `sheets/quickCopy.ts`'s `SEND_TO_WIZARD`, with the Sky's
// other words (that module imports nothing, so a copy test reads it bare).

/** What the wizard is handed: its `WizardPrefill`, named through the opener. */
export type SkyWizardPrefill = Parameters<typeof openFlowWizard>[0];

/** A framing's overlap (a fraction) as the percent a TARGET holds, with the
 *  float noise of `0.15 * 100` taken off (15.000000000000002 is not a number
 *  anybody framed). */
export function overlapPercent(fraction: number): number {
  return Math.round(fraction * 100 * 1e6) / 1e6;
}

/**
 * The Sky framing as the wizard's prefill: everything the framing decided and
 * nothing it did not.
 *
 * - The NAME is the object's display name, else its catalogue id, and "" for
 *   a free-roam patch, which the wizard then asks for (the frames are filed
 *   under it; a patch's coordinate label is not a name anybody chose).
 * - The CENTRE is the framing's, not the catalogue's: a framing dragged off
 *   the object is framed where it was dragged to.
 * - NO ANGLE MODE. The framing holds a rotation and no mode (the prefill's
 *   own doc), so the wizard asks ROTATE TO or CAMERA FIXED AT with the PA
 *   already typed. The PA is the commanded one (`commandedPa`): a dial never
 *   turned off 0 commands nothing and arrives as no PA, which the wizard asks
 *   for rather than planning a grid at an angle nobody chose.
 * - No skipped panels: the Sky has no way to skip one.
 * - The FIELD is the one the reticle and the pitch were drawn from, null
 *   while the optics are unknown.
 */
export function framingPrefill(
  f: {
    target?: { id?: string; name?: string } | undefined;
    center: { ra_hours: number; dec_deg: number };
    rotation_deg: number;
    mosaic: { rows: number; cols: number; overlap: number };
  },
  fov: { fov_x_deg: number; fov_y_deg: number },
): SkyWizardPrefill {
  return {
    name: (f.target?.name || f.target?.id || "").trim(),
    ra: raHms(f.center.ra_hours),
    dec: decDms(f.center.dec_deg),
    angleMode: null,
    paDeg: commandedPa(f.rotation_deg),
    rows: f.mosaic.rows,
    cols: f.mosaic.cols,
    overlapPct: overlapPercent(f.mosaic.overlap),
    skip: "",
    fov: fov.fov_x_deg > 0 && fov.fov_y_deg > 0 ? { xDeg: fov.fov_x_deg, yDeg: fov.fov_y_deg } : null,
  };
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
  overlap = DEFAULT_OVERLAP,
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
  overlap = DEFAULT_OVERLAP,
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
