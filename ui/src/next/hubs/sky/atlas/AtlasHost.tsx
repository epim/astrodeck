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

import { useEffect, useState, type JSX } from "react";
import { CatalogSearch } from "../../../../components/atlas/CatalogSearch";
import { SkyCanvas } from "../../../../components/atlas/SkyCanvas";
import type { SkyRow } from "../../../../lib/skyRegion";
import type { OpticsLike } from "../../../../lib/framing";
import type {
  CatalogEntry, FramingSession, MountStatus, PackStatus, RotatorStatus,
} from "../../../../types";
import { getPackStatus } from "../../../../api/backends";
import { ActionButton } from "../../../ui";
import {
  PACK_POLL_MS, regionNotes, shouldPollPack, surveyDegradedText,
  type RegionNoteInput,
} from "../frame/degraded";
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
  const { surveyDegraded, onlineFetch } = p;
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

  return (
    <div className="nx-atlas" data-testid="sky-atlas">
      <div className="nx-atlas-search" data-testid="atlas-search">
        {/* No `placeholder` override: `CatalogSearch`'s own default already
            carries the "M 31" example the server's squashed-designation match
            needs a user to see. */}
        <CatalogSearch className="w-full" onPick={p.onPick} />
      </div>

      <div className="nx-atlas-canvas" data-testid="atlas-canvas">
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
          mode={p.mode}
          imageBrightness={p.imageBrightness}
          surveyDegraded={surveyDegraded}
          degradedText={degradedText}
          onlineFetch={onlineFetch}
          pointing={p.mount}
          rotator={p.rotator}
          pointingWhere={p.pointingWhere}
          skyRows={p.skyRows}
          selectedObjectId={p.selectedObjectId}
          onPickObject={p.onPickRow}
          onCenterChange={p.onCenterChange}
          onRotate={p.onRotate}
          onZoom={p.onZoom}
          onSurveyError={p.onSurveyError}
          onSurveyLoad={p.onSurveyLoad}
        />
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

      {notes.map((n) => (
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
        {` · ${surveyLabel(p.framing.survey)}`}
      </p>
    </div>
  );
}

export default AtlasHost;
