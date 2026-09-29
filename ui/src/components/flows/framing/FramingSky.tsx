// FramingSky.tsx - the Target modal's sky (#189 S4 item 1; spec 2026-09-23
// flows mosaic, 2.3).
//
// ONE SkyCanvas, extended only through its props and never forked. It is fed
// the sheet's LOCAL session (centre, zoom, survey) and the draft's grid, and
// it never reads or writes the global `store.framing`: that singleton is how
// one target's mosaic appeared on another target's flow in review #3, and
// the CompassSurvey sky (ClassicAtlasSky.tsx) set the precedent of a canvas
// that browses without touching it.
//
// THE PANELS ARE THE SERVER'S. `panels` arrives already placed: the route's
// answer for the current spec, or while a drag is live the client mirror
// (lib/framing `mosaicGrid`, the same arithmetic). This file draws them and
// reports gestures; it decides no position and no label.
//
// TWO DRAG MODES (spec 2.3). MOVE SKY: one finger drags the sky under the
// pinned grid, which moves the mosaic centre (SkyCanvas `panTo`, reported as
// `onCenterChange`). MOVE GRID: the sky holds still and the drag carries the
// grid (SkyCanvas `frameCenter` with `onFrameCenterChange`). Both pass the
// grid centre as `frameCenter`; in MOVE SKY it is the view centre too, so the
// grid stays pinned where the operator put it. A tap on a panel toggles its
// skip. Pinch and the +/- keys zoom 0.1 to 10 deg; the rotate handle and the
// [ ] keys turn the angle.
//
// THE DEGRADED SURVEY IS THIS FILE'S TO KEEP (#404, UX-07). SkyCanvas swaps its
// LOADING skeleton for the honest empty state only when its host says the
// survey is degraded, and the host learns that from the canvas's own
// `onSurveyError` / `onSurveyLoad`. Every host has to wire all three, as
// AtlasView, SkyHub and CompassSurvey do; this one wired none, so on a rig
// with no sky pack and online fetch off the modal's sky said LOADING, over the
// panel labels that are the skip toggles, for as long as it was open. No
// `degradedText` is given: without the pack's status there is nothing to pick
// a narrower sentence from, so the canvas's own UX-07 sentence is the one.

import { useCallback, useState, type JSX } from "react";
import { SkyCanvas } from "../../atlas/SkyCanvas";
import type { PanelFov, SkyPanel } from "../../atlas/PanelLayer";
import type { OpticsLike } from "../../../lib/framing";
import type { CatalogEntry } from "../../../types";

export const NO_CENTRE =
  "No coordinates yet: search the catalogue or type RA and Dec to place the grid.";

export interface SkyPoint { ra_hours: number; dec_deg: number }

export interface FramingSkyProps {
  /** The grid centre (the draft's coordinates), or null when it has none. */
  frameCentre: SkyPoint | null;
  /** The sky's centre while MOVE GRID holds it still. */
  viewCentre: SkyPoint | null;
  moveGrid: boolean;
  panels: SkyPanel[];
  panelFov: PanelFov | null;
  rotationDeg: number;
  zoomDeg: number;
  survey: string;
  optics: OpticsLike | null;
  mosaic: { rows: number; cols: number; overlap: number };
  catalogTarget?: CatalogEntry;
  night: boolean;
  onlineFetch: boolean;
  /** View mode: gestures that would edit the framing are not wired. */
  readOnly: boolean;
  onMoveGrid: (on: boolean) => void;
  /** The grid centre moved (a MOVE SKY drag, or a MOVE GRID drag). */
  onFrameCentre: (ra_hours: number, dec_deg: number) => void;
  /** The sky moved under a still grid (the arrow keys in MOVE GRID). */
  onViewCentre: (ra_hours: number, dec_deg: number) => void;
  onRotate: (deg: number) => void;
  onZoom: (deg: number) => void;
  /** 0-based server row and col. */
  onPanelTap: (row: number, col: number) => void;
}

const noop = () => {};

export function FramingSky(p: FramingSkyProps): JSX.Element {
  // Above the early return: hooks cannot be conditional. Stable identities,
  // because SkyCanvas's loaders depend on these callbacks and a new one each
  // render would restart its fetch on every render.
  const [degraded, setDegraded] = useState(false);
  const onSurveyError = useCallback(() => setDegraded(true), []);
  const onSurveyLoad = useCallback(() => setDegraded(false), []);
  if (p.frameCentre === null) {
    return (
      <div className="tfs-sky-empty" data-testid="framing-sky-empty">
        <p>{NO_CENTRE}</p>
      </div>
    );
  }
  const moveGrid = p.moveGrid && !p.readOnly;
  const centre = moveGrid && p.viewCentre ? p.viewCentre : p.frameCentre;
  const toggle = p.readOnly ? null : (
    <div className="tfs-move" role="group" aria-label="What a drag moves">
      <button type="button" className={`tfs-btn ${!moveGrid ? "tfs-on" : ""}`}
        aria-pressed={!moveGrid} onClick={() => p.onMoveGrid(false)}>MOVE SKY</button>
      <button type="button" className={`tfs-btn ${moveGrid ? "tfs-on" : ""}`}
        aria-pressed={moveGrid} onClick={() => p.onMoveGrid(true)}>MOVE GRID</button>
    </div>
  );
  return (
    <div className="tfs-sky-fit">
      <SkyCanvas
        overlayControls={toggle}
        showFraming
        center={centre}
        rotationDeg={p.rotationDeg}
        survey={p.survey}
        stretch="linear"
        fovZoomDeg={p.zoomDeg}
        optics={p.optics}
        mosaic={p.mosaic}
        catalogTarget={p.catalogTarget}
        night={p.night}
        mode="survey"
        onlineFetch={p.onlineFetch}
        surveyDegraded={degraded}
        onSurveyError={onSurveyError}
        onSurveyLoad={onSurveyLoad}
        panels={p.panels}
        panelFov={p.panelFov}
        frameCenter={p.frameCentre}
        onFrameCenterChange={moveGrid ? p.onFrameCentre : undefined}
        onPanelTap={p.readOnly ? undefined : p.onPanelTap}
        // MOVE SKY drags the sky under the pinned grid, which IS moving the
        // mosaic centre; MOVE GRID's sky only moves by keyboard, and moves
        // the view alone.
        onCenterChange={p.readOnly ? noop : moveGrid ? p.onViewCentre : p.onFrameCentre}
        onRotate={p.readOnly ? noop : p.onRotate}
        onZoom={p.onZoom}
      />
    </div>
  );
}

export default FramingSky;
