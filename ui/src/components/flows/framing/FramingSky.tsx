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
// a narrower sentence from, so the canvas's own default is the one, and the
// canvas picks it by the `onlineFetch` passed here (#426): UX-07's sentence
// with online fetch off, "not arriving, check the connection or install the
// pack" with it on, where UX-07's told the operator to turn on what was on.
//
// THE VERDICT OUTLIVES THE SKY (#493). The degraded state lived in this
// component's own state, so a sky that remounted (the modal's sky did on a
// phone turn, 390 x 844 to 844 x 390) began again at "not degraded" with a
// fresh tile engine, and pulsed LOADING over a survey it had already found has
// no source until the new engine had failed its way back. It is kept per
// survey in module memory instead (`degradedSurveys` below), which survives a
// remount of this component or of any host above it, where state lifted into
// the sheet would not survive the sheet's own. Keyed by survey, because the
// verdict is one survey's: another survey opens LOADING as before. A verdict
// that has gone stale (a pack installed since) costs one tile's round trip:
// the engine keeps fetching while degraded and its first drawn frame clears
// it through `onSurveyLoad`, exactly as it clears a live one.
//
// THE SKY'S HEIGHT IS FIXED AND THE CANVAS'S LINES ARE INSIDE IT. SkyCanvas
// draws its degraded banner and verdict under its square, and `.tfs-sky` clips
// whatever does not fit; framing.css sizes the square from the height those
// lines leave (#440, #465), through the `.tfs-sky-fit` box below and the
// canvas's own `.sky-canvas-square` slot, so this file passes nothing for it.

import { useCallback, useSyncExternalStore, type JSX } from "react";
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

// The surveys found degraded, for as long as the page lives (see the header).
// A tiny external store, so every mounted sky re-renders when a verdict moves.
const degradedSurveys = new Set<string>();
const degradedListeners = new Set<() => void>();
function setSurveyDegraded(survey: string, on: boolean): void {
  if (degradedSurveys.has(survey) === on) return;
  if (on) degradedSurveys.add(survey);
  else degradedSurveys.delete(survey);
  for (const l of degradedListeners) l();
}
function subscribeDegraded(l: () => void): () => void {
  degradedListeners.add(l);
  return () => { degradedListeners.delete(l); };
}

export function FramingSky(p: FramingSkyProps): JSX.Element {
  // Above the early return: hooks cannot be conditional. The callbacks change
  // identity only with the survey, because SkyCanvas's loaders depend on them
  // and a new one each render would restart its fetch on every render; and
  // they close over the survey they were made for, so a late answer from the
  // survey just left marks that one, not the one now shown.
  const survey = p.survey;
  const degraded = useSyncExternalStore(subscribeDegraded, () => degradedSurveys.has(survey));
  const onSurveyError = useCallback(() => setSurveyDegraded(survey, true), [survey]);
  const onSurveyLoad = useCallback(() => setSurveyDegraded(survey, false), [survey]);
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
