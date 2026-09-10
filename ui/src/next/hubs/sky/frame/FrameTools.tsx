// FrameTools.tsx - the row of framing tools under the finder while FRAME is on
// (review #27, #31, #35).
//
// WHAT WAS LOST AND WHY IT MATTERS ON A PHONE. `SkyCanvas` writes `fovZoomDeg`
// from a wheel listener and the `+`/`-` keys. A phone has neither, so the survey
// field of view was fixed for the whole session: you could drag the sky and turn
// the camera, and never once change how much of it you were looking at. The
// legacy Atlas had a FOV stepper, Fit object, Match camera and a recentre, all
// on one row (`SurveyControls.tsx`), and this is that row in the new language.
//
// FIVE 44 px CONTROLS AND NO MORE. The nudge cluster and the numeric rotation
// stepper from `SurveyControls` are deliberately NOT here: dragging the sky is
// the nudge (and it is better on a touch screen than four arrows), and the
// rotation dial one card down is the angle. A second control for a thing that
// already has one is how two numbers end up disagreeing.
//
// EVERY LOCK IS A REASON. "Fit object" over a free-roam patch and "Match camera"
// with no optics both have nothing to compute, and they say which rather than
// rendering dead.

import type { JSX, RefObject } from "react";
import { Card, honestPress, lockedAttrs, lockedClass } from "../../../ui";
import { zoomLabel, zoomStep, ZOOM_MAX, ZOOM_MIN } from "./zoom";

const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

export const NO_OPTICS_REASON =
  "Set the telescope and camera in Settings > Optics - without them there is no frame to match.";
export const NO_OBJECT_REASON =
  "Fit needs a catalogued object with a published size; this framing is a patch of sky.";
export const RECENTRE_TARGET = "RECENTRE";
export const RECENTRE_HINT_TARGET = "back to the object";
export const RECENTRE_HINT_MOUNT = "back to where the mount points";

export interface FrameToolsProps {
  /** `framing.fovZoomDeg` - degrees of sky across the survey crop. */
  fovZoomDeg: number;
  /** Null when the framing session has no catalogued object (free-roam). */
  fitReason: string | null;
  /** Null when the rig's optics are known. */
  opticsReason: string | null;
  /** True when the session has a catalogued target, so RECENTRE says which
   *  centre it goes back to instead of promising the wrong one. */
  hasTarget: boolean;
  surveyAnchorRef: RefObject<HTMLElement | null>;
  onZoom: (fov: number) => void;
  onFit: () => void;
  onMatchCamera: () => void;
  onRecentre: () => void;
  onSurvey: () => void;
  onExplain: (reason: string) => void;
}

function ToolButton({ label, sub, reason, onPress, onExplain, testid, anchorRef }: {
  label: string;
  sub: string;
  reason: string | null;
  onPress: () => void;
  onExplain: (reason: string) => void;
  testid: string;
  anchorRef?: RefObject<HTMLElement | null>;
}): JSX.Element {
  return (
    <button
      type="button"
      ref={anchorRef as RefObject<HTMLButtonElement> | undefined}
      data-testid={testid}
      className={lockedClass(reason, "nx-sky-frametool")}
      onClick={honestPress(reason, onExplain, onPress)}
      {...lockedAttrs(reason)}
      style={{
        flex: "1 1 0", minWidth: 78, minHeight: 44, borderRadius: 10,
        border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
        color: "var(--text)", fontFamily: DISPLAY, fontWeight: 600,
        fontSize: 10, letterSpacing: ".1em", cursor: "pointer",
        display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", gap: 1, padding: "4px 6px",
      }}
    >
      <span>{label}</span>
      <span
        style={{
          fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
          fontWeight: 400, fontSize: 9, letterSpacing: ".02em",
          color: "var(--text-faint)", whiteSpace: "nowrap",
          overflow: "hidden", textOverflow: "ellipsis", maxWidth: "100%",
        }}
      >
        {sub}
      </span>
    </button>
  );
}

/**
 * The `-` field `+` pair, in the house stepper's own clothes.
 *
 * NOT `Stepper2`: that primitive adds and subtracts a FIXED step, and this
 * range spans 0.1 to 10 degrees. A step big enough to be useful at 8 degrees
 * skips the entire mosaic-framing range at the bottom, and one small enough at
 * 0.3 degrees takes forty presses to cross the top. The step is multiplicative
 * instead, and a press at either end still lands and still says why nothing
 * moved - the same contract `Stepper2` has at its limits.
 */
function ZoomStepper({ value, onZoom, onExplain }: {
  value: number;
  onZoom: (fov: number) => void;
  onExplain: (reason: string) => void;
}): JSX.Element {
  const atMin = value <= ZOOM_MIN;
  const atMax = value >= ZOOM_MAX;
  const wideReason = atMax ? "The field of view is as wide as the survey goes." : null;
  const tightReason = atMin ? "The field of view is as tight as the survey goes." : null;
  return (
    <div className="nx-stepper" role="group" aria-label="Field of view" data-testid="sky-zoom">
      <button
        type="button"
        className={lockedClass(wideReason, "nx-stepper-btn")}
        aria-label="Field of view wider"
        onClick={honestPress(wideReason, onExplain, () => onZoom(zoomStep(value, -1)))}
        {...lockedAttrs(wideReason)}
      >-</button>
      <span className="nx-stepper-value" data-testid="sky-zoom-value">{zoomLabel(value)}</span>
      <button
        type="button"
        className={lockedClass(tightReason, "nx-stepper-btn")}
        aria-label="Field of view tighter"
        onClick={honestPress(tightReason, onExplain, () => onZoom(zoomStep(value, 1)))}
        {...lockedAttrs(tightReason)}
      >+</button>
    </div>
  );
}

export function FrameTools({
  fovZoomDeg, fitReason, opticsReason, hasTarget, surveyAnchorRef,
  onZoom, onFit, onMatchCamera, onRecentre, onSurvey, onExplain,
}: FrameToolsProps): JSX.Element {
  return (
    <Card tone="default" data-testid="sky-frame-tools">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span
            style={{
              fontFamily: DISPLAY, fontWeight: 600, fontSize: 10,
              letterSpacing: ".2em", color: "var(--text-faint)", flexShrink: 0,
            }}
          >
            FIELD
          </span>
          <span style={{ flex: 1 }} />
          <ZoomStepper value={fovZoomDeg} onZoom={onZoom} onExplain={onExplain} />
        </div>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          <ToolButton
            testid="sky-fit-object"
            label="FIT OBJECT"
            sub="to its catalogued size"
            reason={fitReason}
            onPress={onFit}
            onExplain={onExplain}
          />
          <ToolButton
            testid="sky-match-camera"
            label="MATCH CAMERA"
            sub="what one frame sees"
            reason={opticsReason}
            onPress={onMatchCamera}
            onExplain={onExplain}
          />
          <ToolButton
            testid="sky-recentre"
            label={RECENTRE_TARGET}
            sub={hasTarget ? RECENTRE_HINT_TARGET : RECENTRE_HINT_MOUNT}
            reason={null}
            onPress={onRecentre}
            onExplain={onExplain}
          />
          <ToolButton
            testid="sky-survey-btn"
            label="SURVEY"
            sub="image and brightness"
            reason={null}
            onPress={onSurvey}
            onExplain={onExplain}
            anchorRef={surveyAnchorRef}
          />
        </div>
      </div>
    </Card>
  );
}
