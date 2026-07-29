// SurveyControls — the Atlas framing control cluster (design spec §6). Survey
// picker, FOV zoom + "fit object", per-image brightness dimmer, FOV lock
// (absorbs the old "use camera FOV" button — see AtlasView's onCameraFovLock),
// recenter, center-nudge cluster, rotation numeric. Every control is >=44px
// (.btn-touch / .stepper / 44px Toggle). The pixel-scale plausibility hint is
// shown so a focal-length typo is visible (C1-D1).
//
// This component is presentational + callback-driven: AtlasView passes the
// current FramingSession-derived values and the setters that route into
// setFraming. It edits NO magnet file and reads no store directly.

import type { JSX } from "react";
import type { CatalogEntry } from "../../types";
import { Stepper, Toggle, IconButton, InfoDot } from "../ui";

const SURVEYS: { id: string; label: string }[] = [
  { id: "CDS/P/DSS2/color", label: "DSS2 color" },
  { id: "CDS/P/DSS2/red", label: "DSS2 red (mono)" },
  { id: "CDS/P/2MASS/color", label: "2MASS color" },
  { id: "schematic", label: "Schematic (offline)" },
];

const ZOOM_MIN = 0.1;
const ZOOM_MAX = 10;

export interface SurveyControlsProps {
  survey: string;
  fovZoomDeg: number;
  rotationDeg: number;
  /** per-image brightness 0.08..1 (night-adaptation dimmer). */
  imageBrightness: number;
  /** true => zoom is locked to the camera FOV (×1.6 padding). */
  cameraFovLock: boolean;
  /** single-frame FOV (bin-1, degrees) for the FOV lock + fit math. */
  frameFovDeg: number; // max(fov_x, fov_y)
  /** pixel scale + optional plausibility hint for the readout. */
  pixelScaleArcsec: number;
  plausibility: string | null;
  catalogTarget?: CatalogEntry; // for "fit object"
  haveOptics: boolean;
  /** True when the session has a catalog target (labels recenter truthfully). */
  hasTarget: boolean;
  /** config.survey.online_fetch — gates online-only surveys. */
  onlineFetch: boolean;

  onSurveyChange: (survey: string) => void;
  onZoom: (fovDeg: number) => void;
  onRotate: (deg: number) => void;
  onImageBrightness: (v: number) => void;
  onCameraFovLock: (locked: boolean) => void;
  /** nudge center by ±1 frame-width (or ±0.05° when no optics). dx/dy in frames. */
  onNudge: (dxFrames: number, dyFrames: number) => void;
  onRecenter: () => void;
}

function clampZoom(v: number): number {
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, v));
}

export function SurveyControls(props: SurveyControlsProps): JSX.Element {
  const {
    survey, fovZoomDeg, rotationDeg, imageBrightness, cameraFovLock,
    frameFovDeg, pixelScaleArcsec, plausibility, catalogTarget, haveOptics, hasTarget,
    onlineFetch,
    onSurveyChange, onZoom, onRotate, onImageBrightness,
    onCameraFovLock, onNudge, onRecenter,
  } = props;

  // "Fit object": 1.6 × size; falls back to camera FOV (or 0.5°) for point sources.
  const fitObject = () => {
    const sizeDeg = (catalogTarget?.size_arcmin ?? 0) / 60;
    if (sizeDeg > 0) onZoom(clampZoom(1.6 * sizeDeg));
    else onZoom(clampZoom(Math.max(frameFovDeg, 0.5)));
  };

  const rotById = (delta: number) => onRotate(((rotationDeg + delta) % 360 + 360) % 360);

  return (
    <div className="flex flex-col gap-4">
      {/* survey */}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 min-w-0">
          <span className="label">Survey</span>
          <select
            className="field btn-touch px-2"
            value={survey}
            onChange={(e) => onSurveyChange(e.target.value)}
          >
            {SURVEYS.map((s) => {
              const needsOnline = s.id === "CDS/P/DSS2/red" || s.id === "CDS/P/2MASS/color";
              const off = needsOnline && !onlineFetch;
              return (
                <option key={s.id} value={s.id} disabled={off}>
                  {s.label}{off ? " (online only)" : ""}
                </option>
              );
            })}
          </select>
        </label>
      </div>

      {/* zoom + fit + FOV lock */}
      <div className="flex flex-wrap items-end gap-3">
        <Stepper
          label="FOV (zoom)"
          value={fovZoomDeg}
          onChange={(v) => onZoom(clampZoom(v))}
          min={ZOOM_MIN}
          max={ZOOM_MAX}
          step={0.1}
          unit="°"
          format={(v) => v.toFixed(2)}
        />
        <button type="button" className="btn btn-touch" onClick={fitObject}>
          Fit object
        </button>
        {/* No `title=` on this row: it carried the same sentence as the InfoDot
            beside it, and a native tooltip never fires on the phone or tablet
            this page is driven from — so it was a duplicate that only desktop
            could see. The InfoDot has a tap path, a keyboard path and Escape. */}
        <div className="flex items-center gap-2">
          <span className="label inline-flex items-center gap-1">
            Match camera
            <InfoDot
              label="About Match camera"
              content="Zooms the sky view to what your camera will capture (with a little margin). Turn off to go back to your previous zoom."
            />
          </span>
          <Toggle
            checked={cameraFovLock}
            onChange={onCameraFovLock}
            disabled={!haveOptics}
            label="Match the zoom to your camera's field of view"
            showState
          />
        </div>
      </div>

      {/* recenter + nudge cluster */}
      <div className="flex flex-wrap items-center gap-3">
        <IconButton
          icon="align"
          label={hasTarget ? "Recenter on target" : "Recenter on mount"}
          onClick={onRecenter}
        />
        <div className="inline-grid grid-cols-3 gap-1" role="group" aria-label="Nudge center">
          <span />
          <IconButton icon="arrow-up" label="Nudge up" onClick={() => onNudge(0, 1)} />
          <span />
          <IconButton icon="arrow-left" label="Nudge left" onClick={() => onNudge(-1, 0)} />
          <span />
          <IconButton icon="arrow-right" label="Nudge right" onClick={() => onNudge(1, 0)} />
          <span />
          <IconButton icon="arrow-down" label="Nudge down" onClick={() => onNudge(0, -1)} />
          <span />
        </div>
      </div>

      {/* rotation numeric (camera angle, manual) */}
      <div className="flex flex-wrap items-end gap-3">
        <Stepper
          label="Camera angle (manual)"
          value={Math.round(rotationDeg)}
          onChange={(v) => onRotate(((v % 360) + 360) % 360)}
          min={0}
          max={360}
          step={5}
          unit="°"
        />
        <button type="button" className="btn btn-touch" onClick={() => rotById(-5)}>−5°</button>
        <button type="button" className="btn btn-touch" onClick={() => rotById(5)}>+5°</button>
      </div>

      {/* per-image brightness dimmer (night-adaptation memory) */}
      <label className="flex flex-col gap-1">
        <span className="label">Survey brightness</span>
        <input
          type="range"
          min={0.08}
          max={1}
          step={0.02}
          value={imageBrightness}
          onChange={(e) => onImageBrightness(Number(e.target.value))}
          className="w-full h-11 accent-[var(--accent)]"
          aria-label="Survey image brightness"
        />
      </label>

      {/* pixel-scale plausibility hint */}
      {haveOptics && (
        <div className="text-[12px] mono text-ink">
          {pixelScaleArcsec.toFixed(2)}″/px
          {plausibility && <span className="text-warn ml-2">⚠ {plausibility}</span>}
        </div>
      )}
    </div>
  );
}
