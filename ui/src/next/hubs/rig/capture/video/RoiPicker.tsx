// RoiPicker.tsx - the subframe a recording will be written at.
//
// A planetary recording is small and fast: the whole point of the ROI is that a
// 320 px window off a 1000 px sensor is ten times the frame rate and a tenth of
// the file. So this is not an advanced control hidden behind a disclosure - it
// is the first thing on the screen, with the four presets that cover every use
// of it and the four numbers underneath for the case they do not.
//
// EVERYTHING COMMITS THROUGH `alignRoi`. The field shows what will actually be
// sent, not what was typed: a width of 1023 becomes 1016 in the box the moment
// it is committed, because the alternative is a picker that says 1023 and a
// recording that says 1016 with nothing in between to explain the difference.
//
// Props only, no store, no fetch (ARCHITECTURE section 6's rule for a card).

import type { JSX } from "react";
import { Label, Mono, NumberField, Segmented } from "../../../../ui";
import { alignRoi, centredRoi, roiAlignNote, roiLine } from "./videoModel";
import type { VideoRoi } from "../../../../../types";

/** Square windows, centred. The sizes are the ones planetary capture actually
 *  uses: the whole sensor for finding the target, then 1024 / 640 / 320 as it
 *  is centred and the rate climbs. */
export const ROI_PRESETS: readonly { side: number; label: string }[] = [
  { side: 0, label: "FULL" },
  { side: 1024, label: "1024" },
  { side: 640, label: "640" },
  { side: 320, label: "320" },
];

export interface RoiPickerProps {
  roi: VideoRoi;
  onChange: (next: VideoRoi) => void;
  sensorW: number;
  sensorH: number;
  align: readonly [number, number];
  /** Did the engine publish `camera.roi_align`, or is this the default? The
   *  note says which, because a rounded number whose rule is a guess is a
   *  different promise from one whose rule came off the camera. */
  alignPublished: boolean;
  maxBin: number;
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}

export function RoiPicker(props: RoiPickerProps): JSX.Element {
  const { roi, sensorW, sensorH, align } = props;
  const commit = (patch: Partial<VideoRoi>) =>
    props.onChange(alignRoi({ ...roi, ...patch }, sensorW, sensorH, align));

  const activeSide = ROI_PRESETS.find((p) => {
    const preset = centredRoi(p.side, sensorW, sensorH, roi.bin, align);
    return preset.w === roi.w && preset.h === roi.h && preset.x === roi.x && preset.y === roi.y;
  })?.side;

  const binOptions = Array.from(
    { length: Math.max(1, Math.min(4, Math.trunc(props.maxBin) || 1)) },
    (_, i) => ({ value: i + 1, label: `${i + 1}x${i + 1}` }),
  );

  return (
    <div className="nx-vid-roi" data-testid="video-roi">
      <Label size={11}>SUBFRAME</Label>

      <div className="nx-vid-roi-presets" role="group" aria-label="Subframe size">
        {ROI_PRESETS.map((p) => {
          const on = activeSide === p.side;
          return (
            <button
              key={p.label}
              type="button"
              className="nx-vid-roi-preset"
              data-on={on ? "true" : "false"}
              aria-pressed={on}
              onClick={() => {
                if (props.lockedReason) { props.onExplain(props.lockedReason); return; }
                props.onChange(centredRoi(p.side, sensorW, sensorH, roi.bin, align));
              }}
              aria-label={p.side === 0
                ? `Whole sensor, ${sensorW} by ${sensorH} pixels`
                : `Centred ${p.side} by ${p.side} pixel window`}
              data-testid={`video-roi-preset-${p.label.toLowerCase()}`}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <div className="nx-vid-roi-fields">
        <NumberField
          label="X" value={roi.x} onCommit={(v) => commit({ x: v })}
          min={0} max={Math.max(0, sensorW - 1)} integer
          ariaLabel="Subframe left edge, unbinned sensor pixels"
          lockedReason={props.lockedReason} onExplain={props.onExplain}
          data-testid="video-roi-x"
        />
        <NumberField
          label="Y" value={roi.y} onCommit={(v) => commit({ y: v })}
          min={0} max={Math.max(0, sensorH - 1)} integer
          ariaLabel="Subframe top edge, unbinned sensor pixels"
          lockedReason={props.lockedReason} onExplain={props.onExplain}
          data-testid="video-roi-y"
        />
        <NumberField
          label="WIDTH" value={roi.w} onCommit={(v) => commit({ w: v })}
          min={1} max={sensorW} integer unit="px"
          ariaLabel="Subframe width, unbinned sensor pixels"
          lockedReason={props.lockedReason} onExplain={props.onExplain}
          data-testid="video-roi-w"
        />
        <NumberField
          label="HEIGHT" value={roi.h} onCommit={(v) => commit({ h: v })}
          min={1} max={sensorH} integer unit="px"
          ariaLabel="Subframe height, unbinned sensor pixels"
          lockedReason={props.lockedReason} onExplain={props.onExplain}
          data-testid="video-roi-h"
        />
      </div>

      <Segmented<number>
        label="Binning"
        options={binOptions}
        value={roi.bin}
        // Through `alignRoi` and not `centredRoi`: the step is `lcm(align, bin)`,
        // so a bin change re-rounds the rectangle - but it must re-round the one
        // on screen, not snap a hand-placed window back to a centred preset.
        onChange={(bin) => commit({ bin })}
        lockedReason={props.lockedReason}
        onExplain={props.onExplain}
        data-testid="video-roi-bin"
      />

      <Mono size={10.5} tone="dim">{roiLine(roi)} on a {sensorW} x {sensorH} sensor</Mono>
      <Mono size={10} tone="dim">{roiAlignNote(align, props.alignPublished)}</Mono>
    </div>
  );
}
