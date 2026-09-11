// VideoControls.tsx - the four numbers a recording is made of, RECORD, and STOP.
//
// RECORD IS ARMED AND STOP IS NOT, and the asymmetry is the point. A recording
// claims the camera for its whole duration (`hub.py:307-311` labels the lane
// "recording" precisely because a restart mid-file leaves a truncated .ser), so
// it gets the same two-tap the flows RUN uses. STOP is the escape hatch, and
// `components/ui.tsx`'s own rule is that an escape hatch is one tap - a STOP
// that had to be armed would be a STOP that failed the one time it mattered.
//
// THE SIZE FORECAST IS SHOWN BEFORE THE PRESS because the alternative is the
// 507: the server refuses a recording it cannot fit, with two byte counts and
// no sentence, AFTER the operator has decided. `estimateBytes` runs the same
// sum the server does, so the number beside DURATION is the number the disk
// check will use.
//
// Props only. Every reason arrives resolved; nothing here decides who may press
// what.

import type { JSX } from "react";
import { ActionButton, Bar, Label, Mono, NumberField } from "../../../../ui";
import { fmtBytes } from "../../../../../lib/gallery";
import { MAX_DURATION_S, clampNote, progressLine } from "./videoModel";
import type { VideoProgress } from "./videoModel";
import type { VideoState } from "../../../../../types";

export interface VideoSettings {
  fps: number;
  exposureMs: number;
  gain: number;
  offset: number;
  durationS: number;
}

export interface VideoControlsProps {
  settings: VideoSettings;
  onChange: (patch: Partial<VideoSettings>) => void;
  /** Frames this duration and rate plan to write, and the bytes they need. */
  plannedFrames: number;
  estBytes: number;
  /** `camera.max_fps`, when the driver says. Null is "it did not say", which is
   *  NOT the same as slow, so it caps nothing and only removes a hint. */
  maxFps: number | null;
  /** The merged recorder state (bus + cold GET), or null when nothing has run. */
  state: VideoState | null;
  progress: VideoProgress | null;
  recordReason: string | null;
  stopReason: string | null;
  editReason: string | null;
  recording: boolean;
  starting: boolean;
  onRecord: () => void;
  onStop: () => void;
  onExplain: (reason: string) => void;
}

export function VideoControls(props: VideoControlsProps): JSX.Element {
  const s = props.settings;
  const clamp = clampNote(props.state);
  const line = progressLine(props.progress, props.state);
  const p = props.progress;

  return (
    <div className="nx-vid-controls" data-testid="video-controls">
      <div className="nx-vid-fields">
        <NumberField
          label="FPS" value={s.fps} onCommit={(v) => props.onChange({ fps: v })}
          min={0.1} max={10000} step={5}
          hint={props.maxFps != null
            ? `this camera declares ${Number(props.maxFps.toFixed(1))} fps at full frame`
            : "the driver does not declare a ceiling; the recorder clamps and says so"}
          ariaLabel="Frames per second requested"
          lockedReason={props.editReason} onExplain={props.onExplain}
          data-testid="video-fps"
        />
        <NumberField
          label="EXPOSURE" value={s.exposureMs} onCommit={(v) => props.onChange({ exposureMs: v })}
          min={0.1} max={60000} step={1} unit="ms"
          hint="per frame. Longer than 1/fps and the rate is what gets clamped, not the exposure"
          ariaLabel="Per-frame exposure, milliseconds"
          lockedReason={props.editReason} onExplain={props.onExplain}
          data-testid="video-exposure"
        />
        <NumberField
          label="GAIN" value={s.gain} onCommit={(v) => props.onChange({ gain: v })}
          min={0} integer
          ariaLabel="Sensor gain for this recording"
          lockedReason={props.editReason} onExplain={props.onExplain}
          data-testid="video-gain"
        />
        <NumberField
          label="DURATION" value={s.durationS} onCommit={(v) => props.onChange({ durationS: v })}
          min={0.1} max={MAX_DURATION_S} step={5} unit="s"
          hint={`the rig caps a recording at ${MAX_DURATION_S / 60} minutes`}
          ariaLabel="Recording length, seconds"
          lockedReason={props.editReason} onExplain={props.onExplain}
          data-testid="video-duration"
        />
      </div>

      <Mono size={10.5} tone="dim" data-testid="video-estimate">
        {`${props.plannedFrames.toLocaleString("en")} frames, about ${fmtBytes(props.estBytes)} `
          + "on disk. The rig refuses a recording it cannot fit."}
      </Mono>

      {clamp && (
        <Mono size={10.5} tone="warn" data-testid="video-clamp">{clamp}</Mono>
      )}

      {p && (
        <div className="nx-vid-progress" data-testid="video-progress">
          <Bar
            value={p.fraction ?? 0}
            tone={p.fraction == null ? "dim" : "accent"}
            height={6}
            label={p.fraction == null
              ? "Arming the camera"
              : `${p.frames} of ${p.targetFrames} frames recorded`}
            data-testid="video-progress-bar"
          />
          {line && <Mono size={11} tone="accent">{line}</Mono>}
          {props.state?.error && (
            <Mono size={10.5} tone="bad" data-testid="video-error">{props.state.error}</Mono>
          )}
        </div>
      )}

      {props.recording ? (
        <ActionButton
          kind="danger"
          size="xl"
          full
          onPress={props.onStop}
          lockedReason={props.stopReason}
          onExplain={props.onExplain}
          data-testid="video-stop"
        >
          STOP RECORDING
        </ActionButton>
      ) : (
        <ActionButton
          kind="primary"
          size="xl"
          full
          arm={{ label: "CONFIRM RECORD" }}
          busy={props.starting}
          onPress={props.onRecord}
          lockedReason={props.recordReason}
          onExplain={props.onExplain}
          data-testid="video-record"
        >
          {props.starting
            ? "STARTING"
            : `RECORD ${Math.round(s.durationS)} s AT ${Number(s.fps.toFixed(1))} FPS`}
        </ActionButton>
      )}

      <Label size={10}>WHAT THE TWO RATES MEAN</Label>
      <Mono size={10} tone="dim">
        The rate above is what is asked for. The recording reports two more: the
        rate it planned after clamping, and the rate it is measuring frame by
        frame. A file that plans 60 and measures 41 is a file that is running
        short of its frame count, not one that is failing.
      </Mono>
    </div>
  );
}
