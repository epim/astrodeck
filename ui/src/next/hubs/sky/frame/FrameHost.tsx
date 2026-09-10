// FrameHost.tsx - the real survey imagery under the mosaic panels (plan C).
//
// FRAME mode replaces the schematic finder with `components/atlas/SkyCanvas`,
// which is the app's ONE survey renderer: WebGL HiPS tiles, the offline pack,
// the dashed catalogue-extent ellipse, the panel rectangles at true scale, the
// live pointing footprint. Rebuilding any of that here would be a second sky.
//
// TWO CONSTRAINTS WORTH STATING RATHER THAN CODING AROUND:
//
//   1. SkyCanvas carries its own `maxWidth: min(720px, 85svh)` cap inline
//      (SkyCanvas.tsx:880-890). That is exactly ARCHITECTURE section 4's cap for
//      the finder, so nothing here resizes it and `SkyCanvas.tsx` is not edited.
//   2. Every prop below is fed from `store.framing`, which is the SAME slice the
//      Atlas writes. The Sky hub's FRAME mode and the Atlas are two doors onto
//      one framing session on purpose: a mosaic framed here is the mosaic the
//      Atlas shows, because there is only one.
//
// The schematic fallback stays the user's explicit choice (persisted under
// `astrodeck-next-sky-frame-mode`, and now with a control that writes it - see
// `SurveyPopover`); it is not a silent downgrade when a tile fetch fails, which
// is what `surveyDegraded` plus `degradedText` are for.
//
// THE DEGRADED BANNER NAMES THE REAL CAUSE. It used to say "No survey source -
// download the offline sky pack" for every failure, including a transient
// upstream hiccup on a rig with online fetch ON (review #30). `degraded.ts` now
// answers per state, and the caller polls the pack while - and only while - the
// copy depends on it.
//
// THE CATALOGUE'S OWN ABSENCE IS A SEPARATE SENTENCE. `region.degraded` means
// the server answered from the 64 curated objects because the bulk deep-sky file
// did not load, and a sky with no labels looks exactly like a sky whose labels
// failed to load (review #32).

import type { JSX } from "react";
import { SkyCanvas } from "../../../../components/atlas/SkyCanvas";
import type { SkyRow } from "../../../../lib/skyRegion";
import type { OpticsLike } from "../../../../lib/framing";
import type { CatalogEntry, FramingSession, MountStatus, RotatorStatus } from "../../../../types";
import { regionNotes, type RegionNoteInput } from "./degraded";

export { DEGRADED_NO_SOURCE } from "./degraded";

export interface FrameHostProps {
  framing: FramingSession;
  optics: OpticsLike | null;
  night: boolean;
  mode: "survey" | "schematic";
  imageBrightness: number;
  surveyDegraded: boolean;
  /** The sentence for the state the rig is actually in - never undefined, so
   *  SkyCanvas's own offline-pack default cannot come back on the online case. */
  degradedText: string;
  onlineFetch: boolean;
  mount: MountStatus | null;
  rotator: RotatorStatus | null;
  pointingWhere: string | null;
  skyRows: SkyRow[];
  /** What the catalogue could and could not answer for this patch. */
  region: RegionNoteInput;
  selectedObjectId: string | null;
  catalogTarget?: CatalogEntry;
  onPickObject: (row: SkyRow | null) => void;
  onCenterChange: (ra: number, dec: number) => void;
  onRotate: (deg: number) => void;
  onZoom: (fov: number) => void;
  onSurveyError: () => void;
  onSurveyLoad: () => void;
}

export function FrameHost(p: FrameHostProps): JSX.Element {
  const notes = regionNotes(p.region);
  return (
    <div data-testid="sky-frame-host">
      <SkyCanvas
        center={p.framing.center}
        rotationDeg={p.framing.rotation_deg}
        survey={p.framing.survey}
        stretch={p.framing.stretch}
        fovZoomDeg={p.framing.fovZoomDeg}
        optics={p.optics}
        mosaic={p.framing.mosaic}
        catalogTarget={p.catalogTarget}
        night={p.night}
        mode={p.mode}
        imageBrightness={p.imageBrightness}
        surveyDegraded={p.surveyDegraded}
        degradedText={p.degradedText}
        onlineFetch={p.onlineFetch}
        pointing={p.mount}
        rotator={p.rotator}
        pointingWhere={p.pointingWhere}
        skyRows={p.skyRows}
        selectedObjectId={p.selectedObjectId}
        onPickObject={p.onPickObject}
        onCenterChange={p.onCenterChange}
        onRotate={p.onRotate}
        onZoom={p.onZoom}
        onSurveyError={p.onSurveyError}
        onSurveyLoad={p.onSurveyLoad}
      />
      {notes.length > 0 && (
        <div
          data-testid="sky-region-note"
          style={{ display: "flex", flexDirection: "column", gap: 4, padding: "6px 2px 0" }}
        >
          {notes.map((n) => (
            <span key={n} style={{ fontSize: 11, lineHeight: 1.45, color: "var(--text-faint)" }}>
              {n}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
