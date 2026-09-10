// CaptureControls.tsx - CAPTURE / LOOP / LIVE VIEW, and the STOP that replaces
// the primary while anything is in flight.
//
// LOOP is the GAP-ANALYSIS §3 addition: the design has CAPTURE and RECORD, the
// rig has Single, Loop and Live View, and Loop is the main framing and focusing
// tool. RECORD is not built - see `VIDEO_LOCK_REASON`.
//
// Every label carries the numbers the press will use, so the primary reads
// "CAPTURE 3 x 60 s · L" rather than "CAPTURE". A button that does not say what
// it will do is a button somebody presses twice.
//
// LIVE VIEW's extras all survive: the satellite/aircraft trail rejection sent as
// `clip_sigma` at START only, RESET STACK, and `LiveStackReadout` - which is
// mounted as-is and stays SILENT on a clean match, so it is not a badge that
// always says "aligned".

import type { JSX } from "react";
import { LiveStackReadout } from "../../../../components/preview/LiveStackReadout";
import { fmtExposure } from "../../../../components/ui/CameraPickers";
import { ActionButton, Checkbox22, Label, Mono, Stepper2 } from "../../../ui";
import type { PreviewInfo } from "../../../../types";

/** What the sigma actually does, and the one setting of it that is not a
 *  mistake. Verbatim from `views/CaptureView.tsx:1437-1443`, with the em-dash
 *  rule applied. Dropping it left a stepper whose 0 looked like "off by
 *  accident" rather than the deliberate answer for a moving subject. */
export const CLIP_SIGMA_NOTE =
  "A pixel this far above the running average is treated as a satellite or "
  + "aircraft trail and kept out of the stack. Lower rejects more; 0 turns it "
  + "off, which is what you want if the thing you are imaging is itself moving.";

export interface CaptureControlsProps {
  count: number;
  exposureS: number;
  filter: string;
  looping: boolean;
  liveStackOn: boolean;
  inFlight: boolean;
  pending: "single" | "loop" | "live" | null;
  /** Frame n of a COUNT batch, for the progress sentence. */
  batch: { index: number; total: number } | null;
  remainingS: number;
  singleReason: string | null;
  loopReason: string | null;
  liveReason: string | null;
  stopReason: string | null;
  resetReason: string | null;
  onCapture: () => void;
  onLoop: () => void;
  onLiveView: () => void;
  onStop: () => void;
  onResetStack: () => void;
  clipEnabled: boolean;
  setClipEnabled: (v: boolean) => void;
  clipSigma: number;
  setClipSigma: (v: number) => void;
  preview: PreviewInfo | null;
  /** The notices under the row: polar, sequence, filter motion (F.5). */
  notices: string[];
  onExplain: (reason: string) => void;
}

/** The line under the primary while a frame is running. Never a percentage
 *  during readout: the client cannot measure transfer time and saying so would
 *  be a lie about a number. */
export function progressLine(opts: {
  inFlight: boolean; remainingS: number; batch: { index: number; total: number } | null;
  filter: string; exposureS: number;
}): string | null {
  if (!opts.inFlight) return null;
  const frame = opts.batch ? `frame ${opts.batch.index} of ${opts.batch.total} · ` : "";
  return `${frame}${Math.max(0, Math.ceil(opts.remainingS))} / ${opts.exposureS} s · ${opts.filter}`;
}

export function CaptureControls(props: CaptureControlsProps): JSX.Element {
  const busy = props.inFlight || props.looping || props.pending != null;
  const primaryLabel = props.pending === "single"
    ? "STARTING"
    : `CAPTURE ${props.count} × ${fmtExposure(props.exposureS)} · ${props.filter}`;
  const line = progressLine({
    inFlight: props.inFlight, remainingS: props.remainingS, batch: props.batch,
    filter: props.filter, exposureS: props.exposureS,
  });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} data-testid="capture-controls">
      {line && <Mono size={11} tone="accent">{line}</Mono>}

      {busy ? (
        <ActionButton
          kind="danger"
          size="xl"
          full
          onPress={props.onStop}
          lockedReason={props.stopReason}
          onExplain={props.onExplain}
          data-testid="capture-stop"
        >
          STOP
        </ActionButton>
      ) : (
        <ActionButton
          kind="primary"
          size="xl"
          full
          onPress={props.onCapture}
          lockedReason={props.singleReason}
          onExplain={props.onExplain}
          data-testid="capture-go"
        >
          {primaryLabel}
        </ActionButton>
      )}

      <div style={{ display: "flex", gap: 6 }}>
        <ActionButton
          kind="secondary"
          full
          onPress={props.onLoop}
          busy={props.pending === "loop"}
          lockedReason={props.loopReason}
          onExplain={props.onExplain}
          data-testid="capture-loop"
        >
          {props.pending === "loop" ? "STARTING" : props.looping ? "LOOPING" : "LOOP"}
        </ActionButton>
        <ActionButton
          kind="secondary"
          full
          onPress={props.onLiveView}
          busy={props.pending === "live"}
          lockedReason={props.liveReason}
          onExplain={props.onExplain}
          data-testid="capture-live"
        >
          {props.pending === "live" ? "STARTING"
            : props.liveStackOn ? "LIVE VIEW · ON" : "LIVE VIEW"}
        </ActionButton>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Label>LIVE VIEW</Label>
        <Checkbox22
          checked={props.clipEnabled}
          onChange={props.setClipEnabled}
          label={`Reject trails above ${props.clipSigma} sigma`}
        />
        {props.clipEnabled && (
          <Stepper2
            value={props.clipSigma}
            onChange={props.setClipSigma}
            step={1}
            min={0}
            max={20}
            format={(v) => `${v} sigma`}
            label="Trail rejection sigma"
            data-testid="live-sigma"
          />
        )}
        {props.clipEnabled && (
          <Mono size={10} tone="dim">{CLIP_SIGMA_NOTE}</Mono>
        )}
        <Mono size={10} tone="dim">
          Sent when the stack STARTS. Changing it mid-stack does not reach the one
          already running.
        </Mono>
        <ActionButton
          kind="ghost"
          onPress={props.onResetStack}
          lockedReason={props.resetReason}
          onExplain={props.onExplain}
          data-testid="capture-reset-stack"
        >
          RESET STACK
        </ActionButton>
        <LiveStackReadout preview={props.preview} />
      </div>

      {props.notices.map((n) => (
        <Mono key={n} size={10.5} tone="warn">{n}</Mono>
      ))}
    </div>
  );
}
