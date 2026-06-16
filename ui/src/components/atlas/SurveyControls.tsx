// SurveyControls — the Atlas framing control cluster (design spec §6). Survey
// picker, stretch toggle, FOV zoom + "fit object", per-image brightness dimmer,
// "use camera FOV" lock, recenter, center-nudge cluster, rotation numeric. Every
// control is >=44px (.btn-touch / .stepper / 44px Toggle). The pixel-scale
// plausibility hint is shown so a focal-length typo is visible (C1-D1).
//
// This component is presentational + callback-driven: AtlasView passes the
// current FramingSession-derived values and the setters that route into
// setFraming. It edits NO magnet file and reads no store directly.

import type { JSX } from "react";
import type { CatalogEntry } from "../../types";
import { Stepper, Toggle, IconButton } from "../ui";

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
  stretch: "linear" | "asinh";
  fovZoomDeg: number;
  rotationDeg: number;
  /** per-image brightness 0.08..1 (night-adaptation dimmer). */
  imageBrightness: number;
  /** true => zoom is locked to the camera FOV (×1.6 padding). */
  cameraFovLock: boolean;
  /** single-frame FOV (bin-1, degrees) for "use camera FOV" + fit math. */
  frameFovDeg: number; // max(fov_x, fov_y)
  /** pixel scale + optional plausibility hint for the readout. */
  pixelScaleArcsec: number;
  plausibility: string | null;
  catalogTarget?: CatalogEntry; // for "fit object"
  haveOptics: boolean;

  onSurveyChange: (survey: string) => void;
  onStretchChange: (stretch: "linear" | "asinh") => void;
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
    survey, stretch, fovZoomDeg, rotationDeg, imageBrightness, cameraFovLock,
    frameFovDeg, pixelScaleArcsec, plausibility, catalogTarget, haveOptics,
    onSurveyChange, onStretchChange, onZoom, onRotate, onImageBrightness,
    onCameraFovLock, onNudge, onRecenter,
  } = props;

  // "Fit object": 1.6 × size; falls back to camera FOV (or 0.5°) for point sources.
  const fitObject = () => {
    const sizeDeg = (catalogTarget?.size_arcmin ?? 0) / 60;
    if (sizeDeg > 0) onZoom(clampZoom(1.6 * sizeDeg));
    else onZoom(clampZoom(Math.max(frameFovDeg, 0.5)));
  };

  // "Use camera FOV": lock the survey crop to the camera field (×1.6 pad).
  const useCameraFov = () => {
    if (frameFovDeg > 0) onZoom(clampZoom(frameFovDeg * 1.6));
  };

  const rotById = (delta: number) => onRotate(((rotationDeg + delta) % 360 + 360) % 360);

  return (
    <div className="flex flex-col gap-4">
      {/* survey + stretch */}
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 min-w-0">
          <span className="label">Survey</span>
          <select
            className="field btn-touch px-2"
            value={survey}
            onChange={(e) => onSurveyChange(e.target.value)}
          >
            {SURVEYS.map((s) => (
              <option key={s.id} value={s.id}>{s.label}</option>
            ))}
          </select>
        </label>
        <div className="flex flex-col gap-1">
          <span className="label">Stretch</span>
          <div className="inline-flex items-stretch" role="group" aria-label="Survey stretch">
            <button
              type="button"
              className={`btn btn-touch ${stretch === "linear" ? "border-accent text-accent" : ""}`}
              aria-pressed={stretch === "linear"}
              onClick={() => onStretchChange("linear")}
            >Linear</button>
            <button
              type="button"
              className={`btn btn-touch ${stretch === "asinh" ? "border-accent text-accent" : ""}`}
              aria-pressed={stretch === "asinh"}
              onClick={() => onStretchChange("asinh")}
            >Asinh</button>
          </div>
        </div>
      </div>

      {/* zoom + fit + use-camera-FOV lock */}
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
        <button
          type="button"
          className="btn btn-touch"
          onClick={useCameraFov}
          disabled={!haveOptics}
          title={haveOptics ? "Zoom to the camera field" : "Set focal length first"}
        >
          Use camera FOV
        </button>
        <div className="flex items-center gap-2">
          <span className="label">Lock</span>
          <Toggle
            checked={cameraFovLock}
            onChange={onCameraFovLock}
            disabled={!haveOptics}
            label="Lock zoom to camera FOV"
            showState
          />
        </div>
      </div>

      {/* recenter + nudge cluster */}
      <div className="flex flex-wrap items-center gap-3">
        <IconButton icon="align" label="Recenter on target" onClick={onRecenter} />
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
