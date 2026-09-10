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
// `astrodeck-next-sky-frame-mode`); it is not a silent downgrade when a tile
// fetch fails, which is what `surveyDegraded` plus `degradedText` are for.

import type { JSX } from "react";
import { SkyCanvas } from "../../../../components/atlas/SkyCanvas";
import type { SkyRow } from "../../../../lib/skyRegion";
import type { OpticsLike } from "../../../../lib/framing";
import type { CatalogEntry, FramingSession, MountStatus, RotatorStatus } from "../../../../types";

export const DEGRADED_NO_SOURCE =
  "No survey source - download the offline sky pack in Settings, or enable online fetch.";

export interface FrameHostProps {
  framing: FramingSession;
  optics: OpticsLike | null;
  night: boolean;
  mode: "survey" | "schematic";
  imageBrightness: number;
  surveyDegraded: boolean;
  onlineFetch: boolean;
  mount: MountStatus | null;
  rotator: RotatorStatus | null;
  pointingWhere: string | null;
  skyRows: SkyRow[];
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
        degradedText={DEGRADED_NO_SOURCE}
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
    </div>
  );
}
