// AtlasHost.tsx - ATLAS mode: the classic pannable sky, inside the Sky hub.
//
// WHY THIS EXISTS. On a deployed rig the Sky tab opens on the schematic MAP
// finder; the survey imagery lives only in FRAME, which is locked until the
// reticle is above the horizon or a target is locked; and the classic full-sky
// Atlas (`views/AtlasView.tsx`) had no door at all in the new UI - the legacy
// bridge sent the old `atlas` view onto the finder, where it read as "no atlas
// loaded". This is that door: a fourth mode beside MAP / FRAME / GYRO that
// mounts the SAME renderer the Atlas always used, pannable over the whole sky.
//
// ONE RENDERER, THREE MOUNT SITES. `components/atlas/SkyCanvas` is the app's
// only survey renderer (WebGL HiPS tiles, the offline pack, the catalogue
// markers, the live pointing footprint) and is on ARCHITECTURE section 11's
// keep-as-is list precisely so nothing re-implements it. `views/AtlasView`
// mounts it, `frame/FrameHost` mounts it, and this file is the third. It is
// NOT edited here: its own `maxWidth: min(720px, 85svh)` (SkyCanvas.tsx:890)
// is already ARCHITECTURE section 4's cap for a full-frame canvas, so this
// host adds no width rule of its own at any breakpoint.
//
// WHAT THIS HOST ADDS that FrameHost does not, and why each is here:
//
//   * THE PACK POLL LIVES HERE, not in the hub. `AtlasView.tsx:236-245` polls
//     `GET /api/survey/pack` every 2 s while - and only while - the survey is
//     degraded with no online source, because that is the one state whose
//     banner copy moves (a download's progress). Scoping the effect to this
//     component means leaving ATLAS stops the poll without anyone remembering
//     to, which is the failure the Sky hub's own FRAME poll had to be written
//     around.
//   * THE FIX, NOT A THIRD COPY OF THE COMPLAINT. `SkyCanvas` already prints
//     the degraded sentence TWICE on its own - centred over the empty sky, and
//     again in its own banner under the canvas (`SkyCanvas.tsx:961,1236`) - and
//     the sentence it prints is the one `frame/degraded.ts` computes here, so
//     FRAME and ATLAS cannot disagree about why the sky is dark. What neither
//     copy carries is the way OUT of the state, so this host's degraded row is
//     the route to Settings > SKY PACK, with the download's live progress on it
//     when there is a download - which is the whole reason the pack is polled.
//     The sentence rides on the row's title and accessible name rather than
//     being painted a third time.
//   * THE ROUND TRIP BACK TO THE FINDER. The classic Atlas could search the
//     catalogue but had no way to hand what it found to the finder; the finder
//     has `#/sky?lock=<id>` and nothing on the Atlas ever used it. LOCK IN
//     FINDER closes that loop, and FRAME hands the current centre to FRAME
//     mode, which is where the mosaic and rotation tools live.
//
// NO CAPABILITY GATE. All three requests behind this screen are `view.status`:
// `/api/survey/tile/{slug}/{order}/{npix}.jpg` (`catalog/tiles.py:102`),
// `/api/survey/cutout.jpg` (`catalog/survey.py:286`) and `/api/survey/pack`
// (`api/app.py:2866`). It needs no site, no target and no connected device, so
// the ATLAS button is never locked - a viewer on a rig with nothing plugged in
// gets the same sky an admin does.

import { useCallback, useEffect, useRef, useState, type JSX, type PointerEvent as ReactPointerEvent } from "react";
import { CatalogSearch } from "../../../../components/atlas/CatalogSearch";
import { SkyCanvas } from "../../../../components/atlas/SkyCanvas";
import type { SkyRow } from "../../../../lib/skyRegion";
import type { OpticsLike } from "../../../../lib/framing";
import type {
  CatalogEntry, FramingSession, MountStatus, PackStatus, RotatorStatus,
} from "../../../../types";
import { getPackStatus } from "../../../../api/backends";
import { ActionButton } from "../../../ui";
import { NxIcon, type NxIconName } from "../../../icons";
import {
  PACK_POLL_MS, regionNotes, shouldPollPack, surveyDegradedText,
  type RegionNoteInput,
} from "../frame/degraded";
import type { PatchModel, SkyKind, SkyTarget } from "../finder";
import { AtlasMarkers } from "./AtlasMarkers";
import { boxToSky } from "./aim";
import "./atlas.css";

/** Why LOCK IN FINDER is refused with nothing framed. It names the two ways to
 *  give it something, because "nothing selected" would describe the state and
 *  not the move out of it. */
export const ATLAS_NO_OBJECT =
  "Search for an object, or tap one on the sky, before the finder can lock it - "
  + "a lock follows a catalogued target, not a bare position.";

/** Centre RA/Dec in the form a user reads out over the phone. */
export function centreLabel(raHours: number, decDeg: number): string {
  const h = Math.floor(((raHours % 24) + 24) % 24);
  const m = Math.floor(((((raHours % 24) + 24) % 24) - h) * 60);
  const sign = decDeg < 0 ? "-" : "+";
  const ad = Math.abs(decDeg);
  const d = Math.floor(ad);
  const am = Math.round((ad - d) * 60);
  return `${String(h).padStart(2, "0")}h ${String(m).padStart(2, "0")}m `
    + `${sign}${String(d).padStart(2, "0")}° ${String(am).padStart(2, "0")}'`;
}

/** "CDS/P/DSS2/color" -> "DSS2 color". The registry id is a path, and the tail
 *  segment alone ("color") names three different surveys, so the last TWO
 *  segments are what identifies the picture. `schematic` has no imagery and
 *  says so. */
export function surveyLabel(survey: string): string {
  if (!survey || survey === "schematic") return "schematic";
  const parts = survey.split("/").filter(Boolean);
  return parts.slice(-2).join(" ") || survey;
}

/**
 * What the degraded row OFFERS, which is never the same as what is wrong.
 *
 * A download in flight puts its own numbers on the row - they move every two
 * seconds and they are the only thing on this screen that changes while a pack
 * is fetching. Every other degraded state ends in the same place, because the
 * SKY PACK sheet is where both fixes live (download the pack, or turn on online
 * fetch), so the row names that destination rather than guessing which of the
 * two the user wants.
 */
export function packFixLabel(pack: PackStatus | null): string {
  const f = pack?.fetching;
  if (f) return `SKY PACK - ${f.done}/${f.total} ›`;
  return "SKY PACK ›";
}

/** What a deliberately blank sky says about itself. Without it, SURVEY off and
 *  a survey that cannot be reached look identical - a dark square - and the
 *  second one has a fix while the first one is a choice. */
export const SURVEY_LAYER_OFF_NOTE =
  "Survey imagery is off for this device, so no tiles are fetched - the "
  + "markers, the reticle and the pointing footprint are still drawn. Turn it "
  + "back on under the layers button.";

export interface AtlasHostProps {
  framing: FramingSession;
  optics: OpticsLike | null;
  night: boolean;
  /** The user's persisted survey/schematic choice, shared with FRAME mode. */
  mode: "survey" | "schematic";
  imageBrightness: number;
  surveyDegraded: boolean;
  onlineFetch: boolean;
  mount: MountStatus | null;
  rotator: RotatorStatus | null;
  pointingWhere: string | null;
  skyRows: SkyRow[];
  region: RegionNoteInput;
  selectedObjectId: string | null;
  /** A search result, or a marker tapped on the sky: both recentre the atlas. */
  onPick: (entry: CatalogEntry) => void;
  onPickRow: (row: SkyRow | null) => void;

  // ---- tonight's targets, on the atlas --------------------------------------
  /** `useSkyModel.targets` - the same ranked, lens-filtered list the schematic
   *  MAP draws. Drawn here as the design's label pills; see `AtlasMarkers`. */
  targets: SkyTarget[];
  /** The finder's current lock, which wears the ring. */
  lockId: string | null;
  kindIcon: Record<SkyKind, NxIconName>;
  /** A tap on one of those pills: the hub aims the finder at it, exactly as a
   *  tap on a MAP marker does, and frames it here. */
  onPickTarget: (t: SkyTarget) => void;
  /** The model's own sentences about the ranking - why it is empty, or why most
   *  of the sky cannot be placed for this role. Shown verbatim, because they
   *  are the server's and this hub's reasons and not new ones. */
  rankingNotes: string[];

  // ---- aim anywhere --------------------------------------------------------
  /** Where the finder is aimed, in RA/Dec, or null with no site / below the
   *  horizon. Drawn as the reticle and printed in the readout line. */
  aim: PatchModel | null;
  /** A tap that landed on empty sky: the RA/Dec under the finger. */
  onAimSky: (raHours: number, decDeg: number) => void;

  // ---- the survey layer ----------------------------------------------------
  /** False draws the markers, the reticle and the footprint on the plain dark
   *  surface and fetches no tiles at all. Persisted per device by the hub. */
  survey: boolean;
  /** Open the layers popover - the same one the schematic finder's stack icon
   *  opens, so the four overlays live under one control in both modes. */
  onLayers: () => void;
  onCenterChange: (ra: number, dec: number) => void;
  onRotate: (deg: number) => void;
  onZoom: (fov: number) => void;
  onSurveyError: () => void;
  onSurveyLoad: () => void;
  /** Open Settings > SKY PACK, where both fixes for a dark sky live. */
  onSurveySource: () => void;
  /** Hand the framed object to the finder's reticle and go back to the map. */
  onLockInFinder: () => void;
  /** Hand the current centre to FRAME mode (mosaic, rotation, panels). */
  onFrame: () => void;
  /** Null while the centre is above the horizon; the hub's FRAME_NEEDS_AIM
   *  sentence when it is not - the survey is drawable anywhere, but a patch of
   *  ground has no target to frame. */
  frameReason: string | null;
  onExplain: (reason: string) => void;
}

export function AtlasHost(p: AtlasHostProps): JSX.Element {
  const { onlineFetch, survey: surveyLayer } = p;
  // WITH THE LAYER OFF NOTHING IS DEGRADED, because nothing was asked for. The
  // banner, the poll and the fix row all hang off this one value rather than
  // off the raw flag, so turning the imagery off cannot leave a complaint about
  // imagery on screen - or a request for pack status running every two seconds
  // for a sky nobody is fetching.
  const surveyDegraded = surveyLayer && p.surveyDegraded;
  const [pack, setPack] = useState<PackStatus | null>(null);

  // Polled ONLY while the copy depends on it, and only while this component is
  // on screen - `AtlasView.tsx:236-245`'s rule, scoped to a mount.
  useEffect(() => {
    if (!shouldPollPack(surveyDegraded, onlineFetch)) return;
    let live = true;
    const tick = (): void => {
      getPackStatus().then((s) => { if (live) setPack(s); }).catch(() => { /* the copy falls back */ });
    };
    tick();
    const id = setInterval(tick, PACK_POLL_MS);
    return () => { live = false; clearInterval(id); };
  }, [surveyDegraded, onlineFetch]);

  const degradedText = surveyDegradedText(onlineFetch, pack);
  const notes = regionNotes(p.region);
  const target = p.framing.target ?? null;

  // ---- the canvas square, MEASURED ----------------------------------------
  //
  // The marker overlay has to sit exactly on the square `SkyCanvas` draws, and
  // that square sizes itself (`min(720px, 85svh)`, centred). Re-stating that
  // rule here would be a second answer to a question that already has one and
  // would drift the first time either number moved, so the element is measured
  // instead: its offset inside this host (which is the overlay's positioned
  // ancestor) and its edge. Zero is a real answer - a layout that has not
  // happened yet, or jsdom, which has no layout at all - and the overlay
  // renders anyway, so the markers are in the tree either way.
  const canvasWrapRef = useRef<HTMLDivElement | null>(null);
  const [box, setBox] = useState({ left: 0, top: 0, size: 0 });

  useEffect(() => {
    const wrap = canvasWrapRef.current;
    if (!wrap || typeof window === "undefined") return;
    const read = (): void => {
      const el = wrap.querySelector('[role="application"]') as HTMLElement | null;
      if (!el) return;
      const size = el.clientWidth || 0;
      setBox((cur) =>
        cur.left === el.offsetLeft && cur.top === el.offsetTop && cur.size === size
          ? cur
          : { left: el.offsetLeft, top: el.offsetTop, size });
    };
    read();
    const RO = (window as unknown as {
      ResizeObserver?: new (cb: () => void) => { observe(t: Element): void; disconnect(): void };
    }).ResizeObserver;
    if (!RO) {
      window.addEventListener("resize", read);
      return () => window.removeEventListener("resize", read);
    }
    const ro = new RO(read);
    ro.observe(wrap);
    return () => ro.disconnect();
  }, []);

  // ---- aim anywhere --------------------------------------------------------
  //
  // WHICH GESTURE, and why it is this one. `SkyCanvas` exposes exactly one
  // empty-sky hook: `onPickObject(null)`, fired on a TAP (pointer down and up
  // inside `TAP_SLOP_PX` within `TAP_MS`) that hit no marker. It exposes no
  // long-press at all, and it is on ARCHITECTURE section 11's keep-as-is list,
  // so a long-press would have meant either editing that file or hand-rolling a
  // second gesture recogniser beside its own - two recognisers on one surface
  // being the reliable way to make a drag sometimes aim.
  //
  // What the hook does NOT carry is WHERE the tap was, so the position is read
  // off the very same pointer event: the canvas's own `onPointerUp` runs first
  // (it is the inner handler) and sets the flag, then this one bubbles with the
  // coordinates still on it. A tap on one of our own target pills never reaches
  // the canvas at all, so it can never be mistaken for empty sky.
  const emptyTapRef = useRef(false);
  const { onPickRow, onAimSky } = p;
  const centreRa = p.framing.center.ra_hours;
  const centreDec = p.framing.center.dec_deg;
  const fovDeg = p.framing.fovZoomDeg;

  const pickRow = useCallback((row: SkyRow | null) => {
    emptyTapRef.current = row == null;
    onPickRow(row);
  }, [onPickRow]);

  const onCanvasPointerUp = useCallback((e: ReactPointerEvent<HTMLDivElement>) => {
    if (!emptyTapRef.current) return;
    emptyTapRef.current = false;
    const el = (e.target as Element | null)?.closest?.('[role="application"]') as HTMLElement | null;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (!(rect.width > 0)) return;
    const at = { x: e.clientX - rect.left, y: e.clientY - rect.top };
    const sky = boxToSky(at, { ra_hours: centreRa, dec_deg: centreDec }, fovDeg, rect.width);
    onAimSky(sky.ra_hours, sky.dec_deg);
  }, [centreRa, centreDec, fovDeg, onAimSky]);

  return (
    <div className="nx-atlas" data-testid="sky-atlas">
      <div className="nx-atlas-search" data-testid="atlas-search">
        {/* No `placeholder` override: `CatalogSearch`'s own default already
            carries the "M 31" example the server's squashed-designation match
            needs a user to see. */}
        <CatalogSearch className="w-full" onPick={p.onPick} />
      </div>

      <div
        className="nx-atlas-canvas"
        data-testid="atlas-canvas"
        ref={canvasWrapRef}
        onPointerUp={onCanvasPointerUp}
      >
        <SkyCanvas
          center={p.framing.center}
          rotationDeg={p.framing.rotation_deg}
          survey={p.framing.survey}
          stretch={p.framing.stretch}
          fovZoomDeg={p.framing.fovZoomDeg}
          optics={p.optics}
          mosaic={p.framing.mosaic}
          catalogTarget={target ?? undefined}
          night={p.night}
          // SURVEY OFF IS `schematic`, which is not a near-miss for "no tiles" -
          // it is exactly the state `SkyCanvas` already has for it: the tile
          // engine is gated on `mode === "survey"` and the cutout scheduler
          // returns early, so not one request leaves the phone, while the SVG
          // layer (the planned box, the catalogue markers, the live footprint)
          // draws on over the dark backdrop.
          mode={surveyLayer ? p.mode : "schematic"}
          imageBrightness={p.imageBrightness}
          surveyDegraded={surveyDegraded}
          degradedText={degradedText}
          onlineFetch={onlineFetch}
          pointing={p.mount}
          rotator={p.rotator}
          pointingWhere={p.pointingWhere}
          skyRows={p.skyRows}
          selectedObjectId={p.selectedObjectId}
          onPickObject={pickRow}
          onCenterChange={p.onCenterChange}
          onRotate={p.onRotate}
          onZoom={p.onZoom}
          onSurveyError={p.onSurveyError}
          onSurveyLoad={p.onSurveyLoad}
        />

        <AtlasMarkers
          box={box}
          centre={p.framing.center}
          fovDeg={fovDeg}
          targets={p.targets}
          lockId={p.lockId}
          kindIcon={p.kindIcon}
          aim={p.aim}
          onPick={p.onPickTarget}
        />

        <button
          type="button"
          className="nx-atlas-layers"
          data-sky-layers
          data-survey={surveyLayer ? "on" : "off"}
          data-testid="atlas-layers"
          aria-label={
            surveyLayer
              ? "overlays: survey imagery, cloud, horizon, wind"
              : "overlays: survey imagery off, cloud, horizon, wind"
          }
          onPointerDown={(e) => e.stopPropagation()}
          onClick={p.onLayers}
        >
          <NxIcon name="layers" size={20} />
        </button>
      </div>

      {surveyDegraded && (
        <button
          type="button"
          className="nx-atlas-degraded"
          data-testid="atlas-degraded"
          // The sentence is already on screen twice (SkyCanvas draws it over the
          // empty sky and again in its own banner right above this row), so it
          // rides here as the reason for the destination instead of as a third
          // paragraph saying the same thing.
          title={degradedText}
          aria-label={`${degradedText} Open Settings, sky pack.`}
          onClick={p.onSurveySource}
        >
          {packFixLabel(pack)}
        </button>
      )}

      {!surveyLayer && (
        <span className="nx-atlas-note" data-testid="atlas-survey-off">
          {SURVEY_LAYER_OFF_NOTE}
        </span>
      )}

      {/* The catalogue's own absences, then the RANKING's - two different
          silences with two different fixes, and the ranking's are the model's
          own sentences rather than second ones written here. */}
      {[...notes, ...p.rankingNotes].map((n) => (
        <span className="nx-atlas-note" key={n}>{n}</span>
      ))}

      <div className="nx-atlas-actions">
        <ActionButton
          kind="primary"
          onPress={p.onLockInFinder}
          lockedReason={target ? null : ATLAS_NO_OBJECT}
          onExplain={p.onExplain}
          ariaLabel={target ? `Lock ${target.name} in the finder` : "Lock in finder"}
          data-testid="atlas-lock-in-finder"
        >
          LOCK IN FINDER
        </ActionButton>
        <ActionButton
          kind="secondary"
          onPress={p.onFrame}
          lockedReason={p.frameReason}
          onExplain={p.onExplain}
          ariaLabel="Frame this centre - mosaic and rotation"
          data-testid="atlas-frame"
        >
          FRAME
        </ActionButton>
      </div>

      {/* THE THREE FACTS THE CANVAS ITSELF NEVER PRINTS. It draws the field
          width and the pixel scale in its own corners, but never the centre it
          is drawn at and never which survey the pixels came from - and DSS2
          colour, DSS2 red and 2MASS are three different pictures of the same
          sky. The survey picker lives in FRAME's popover, which is not mounted
          here, so without this line the imagery is unattributed. */}
      <p className="nx-atlas-where">
        {target ? `${target.name} · ` : "free roam · "}
        {centreLabel(p.framing.center.ra_hours, p.framing.center.dec_deg)}
        {` · ${surveyLayer ? surveyLabel(p.framing.survey) : "no imagery"}`}
      </p>

      {/* WHERE THE RETICLE IS, which is not always where the picture is
          centred: pan the sky and the aim stays on the patch it was put on.
          These are the coordinates the patch card will send and the FITS header
          will be filed under, plus the one thing the marker's colour carries
          for anyone who can see it - clear, clouded, or behind the tree line. */}
      {p.aim && (
        <p className="nx-atlas-where" data-testid="atlas-aim">
          {`reticle ${p.aim.raStr} ${p.aim.decStr} · ${p.aim.statusTxt}`}
        </p>
      )}
    </div>
  );
}

export default AtlasHost;
