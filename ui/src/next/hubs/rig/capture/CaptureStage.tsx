// CaptureStage.tsx - the manual bench's picture: the shared PreviewStage in the
// design's 250 px card, with the four overlays the prototype draws on it.
//
// The stage itself is MOUNTED, not re-implemented (ARCHITECTURE.md #11). It is
// props-driven and already owns pan/zoom, the stretch transfer function, the
// star/clip/tilt/field overlays, the link-down ribbon and the empty state, and
// every one of those is a behaviour this screen would otherwise have to grow a
// second, diverging copy of.
//
// THE FOUR-STEP FRAME PRECEDENCE is transcribed from LivePreview.tsx:47,61-83
// rather than re-invented, because step 4 is load-bearing and easy to get wrong:
//   1. the pinned-or-live frame;
//   2. else the newest ring entry, if any capture happened this session;
//   3. else null - the stage paints its own empty face;
//   4. unless the last-session stand-in can find something honest to put there.
// `enabled: !shown` is the whole ordering guarantee: the moment (1) or (2) has
// something, the stand-in is retired and can never come back. It is never
// fetched while `linkDown`, because a frozen `sequence` makes an hours-old frame
// measure as seconds old.

import { useRef, type JSX } from "react";
import { PreviewStage, type StageControls } from "../../../../components/preview/PreviewStage";
import { useLastSessionFrame } from "../../../../components/preview/useLastSessionFrame";
import ActivityRing from "../../../../components/ui/ActivityRing";
import {
  useStore, usePreviews, useLivePreview, useSelectedPreviewId, useLivePreviewId,
  useViewport, useStretch, useOverlays, useHfrThresholds, useNight, useLinkDown,
  useSequence, useMount,
} from "../../../../store";
import { ActionButton, Bar, IconButton48, Mono, Pill } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import type { PreviewInfo } from "../../../../types";

/** The badge over the top-left corner: what the picture IS. */
export function stageLabel(
  opts: { looping: boolean; exposureS: number; filter: string; shot: boolean },
): string {
  if (opts.looping) return `LIVE · ${opts.exposureS} s loop · ${opts.filter}`;
  if (opts.shot) return `LAST FRAME · ${opts.filter} ${opts.exposureS} s`;
  return `IDLE · ${opts.filter} ${opts.exposureS} s next`;
}

/** The bottom-left line: where the rig is looking, and on whose authority.
 *
 *  `PreviewField.source` distinguishes a MEASURED solve from the mount's own
 *  claim, and the mount's claim is not trustworthy - this rig's AM5 has no brake
 *  and has been found 50 degrees from where it said it was - so a `pointing`
 *  source renders as unsolved rather than as a coordinate claim. */
export function pointingLine(opts: {
  runOwnsMount: boolean;
  seqTarget: string | null;
  target: string;
  fieldSource: "solve" | "pointing" | null;
  errorArcmin: number | null;
  parked: boolean;
}): string {
  if (opts.runOwnsMount && opts.seqTarget) return `tracking ${opts.seqTarget} · session running`;
  if (opts.fieldSource === "solve") {
    const where = opts.target ? `on ${opts.target}` : "solved";
    return opts.errorArcmin != null
      ? `${where} · solved ${opts.errorArcmin.toFixed(1)}'`
      : `${where} · solved`;
  }
  if (opts.fieldSource === "pointing") {
    return opts.target
      ? `on ${opts.target} · pointing (unsolved)`
      : "pointing (unsolved)";
  }
  if (opts.parked) return "parked · aim with SKY";
  return "tracking · nothing selected";
}

export interface CaptureStageProps {
  exposureS: number;
  gain: number;
  binning: number;
  filterFace: string;
  /** The operator's target name, for the pointing line. */
  target: string;
  looping: boolean;
  /** The arm latch's phase, so the ring and the bar agree with the buttons. */
  exposing: boolean;
  downloading: boolean;
  fillPct: number;
  elapsedS: number;
  /** The right-hand aim control, resolved by the screen (F.4). */
  aim: { label: string; onPress: () => void; lockedReason: string | null };
  onExplain: (reason: string) => void;
}

export function CaptureStage(props: CaptureStageProps): JSX.Element {
  const previews = usePreviews();
  const live = useLivePreview();
  const selectedPreviewId = useSelectedPreviewId();
  const liveId = useLivePreviewId();
  const viewport = useViewport();
  const stretch = useStretch();
  const overlays = useOverlays();
  const { good: hfrGood, warn: hfrWarn } = useHfrThresholds();
  const night = useNight();
  const linkDown = useLinkDown();
  const sequence = useSequence();
  const mount = useMount();
  const setViewport = useStore((s) => s.setViewport);
  const selectPreview = useStore((s) => s.selectPreview);

  const shown: PreviewInfo | null = live ?? (previews.length ? previews[previews.length - 1] : null);
  const stageBox = useRef<HTMLDivElement>(null);
  const standIn = useLastSessionFrame({
    enabled: !shown, sequence, linkDown, stageRef: stageBox,
  });
  const controls = useRef<StageControls | null>(null);

  const pinned = selectedPreviewId != null && selectedPreviewId !== liveId;
  const newSincePinned = pinned && selectedPreviewId != null
    ? previews.filter((p) => p.id > selectedPreviewId).length
    : 0;

  const runOwnsMount = sequence.state === "running" || sequence.state === "paused";
  const point = pointingLine({
    runOwnsMount,
    seqTarget: sequence.target ?? null,
    target: props.target,
    fieldSource: shown?.field?.source ?? null,
    errorArcmin: mount?.pointing?.error_arcmin ?? null,
    parked: !!mount?.parked,
  });

  // Tap-to-inspect. A pointer that MOVED was a pan of the stage, not a tap on
  // it, and a press that started inside a control belongs to that control - both
  // are excluded rather than swallowed. The keyboard path is the INSPECT button
  // below, which is why this div is not itself given a role it cannot support.
  const down = useRef<{ x: number; y: number } | null>(null);
  const openInspect = () => {
    if (!shown) return;
    nav.sheet("inspect", { id: String(shown.id) });
  };
  const onStageClick = (e: { target: unknown; clientX: number; clientY: number }) => {
    const d = down.current;
    down.current = null;
    if (d && (Math.abs(e.clientX - d.x) > 6 || Math.abs(e.clientY - d.y) > 6)) return;
    const el = e.target as { closest?: (s: string) => unknown } | null;
    if (el?.closest?.("button, input, a, [role='slider']")) return;
    openInspect();
  };

  return (
    <div
      data-testid="capture-stage"
      style={{
        position: "relative", borderRadius: 14, overflow: "hidden",
        border: "1px solid var(--line)", background: "#000",
      }}
      onPointerDown={(e) => { down.current = { x: e.clientX, y: e.clientY }; }}
      onClick={onStageClick}
    >
      <div ref={stageBox}>
        <PreviewStage
          preview={shown}
          viewport={viewport}
          setViewport={setViewport}
          stretch={stretch}
          overlays={overlays}
          hfrGood={hfrGood}
          hfrWarn={hfrWarn}
          night={night}
          linkDown={linkDown}
          pinned={pinned}
          newSincePinned={newSincePinned}
          onReturnToLive={() => selectPreview(null)}
          compact
          bottomRightReserve={0}
          lastCaptured={standIn}
          onControls={(c) => { controls.current = c; }}
        />
      </div>

      <div style={{ position: "absolute", left: 10, top: 10, pointerEvents: "none" }}>
        <Pill tone="dim" data-testid="capture-stage-label">
          {stageLabel({
            looping: props.looping,
            exposureS: props.exposureS,
            filter: props.filterFace,
            shot: shown != null,
          })}
        </Pill>
      </div>

      <div style={{ position: "absolute", right: 10, top: 10, display: "flex", gap: 6 }}>
        <ActionButton
          kind="secondary"
          size="md"
          onPress={props.aim.onPress}
          lockedReason={props.aim.lockedReason}
          onExplain={props.onExplain}
          data-testid="capture-aim"
        >
          {props.aim.label}
        </ActionButton>
        <IconButton48
          glyph={<NxIcon name="search" size={18} />}
          label="INSPECT"
          onPress={openInspect}
          lockedReason={shown ? null : "Take a frame first - there is nothing to inspect yet"}
          onExplain={props.onExplain}
        />
      </div>

      <div style={{
        position: "absolute", left: 10, bottom: 14, maxWidth: "70%",
        pointerEvents: "none", overflow: "hidden", whiteSpace: "nowrap",
        textOverflow: "ellipsis",
      }}>
        <Mono size={10} tone="dim">{point}</Mono>
      </div>
      <div style={{ position: "absolute", right: 10, bottom: 14, pointerEvents: "none" }}>
        <Mono size={10} tone="dim">{`bin ${props.binning} · gain ${props.gain}`}</Mono>
      </div>

      {(props.exposing || props.downloading) && (
        <div style={{
          position: "absolute", inset: 0, display: "flex",
          alignItems: "center", justifyContent: "center", pointerEvents: "none",
        }}>
          <ActivityRing
            mode={props.downloading ? "orbit" : "fill"}
            seconds={props.exposureS}
            elapsedS={props.elapsedS}
            word={props.downloading ? "DOWNLOADING" : "EXPOSING"}
          />
        </div>
      )}

      {(props.exposing || props.downloading) && (
        <div
          style={{ position: "absolute", left: 0, right: 0, bottom: 0 }}
          data-testid="capture-progress"
        >
          <Bar value={props.fillPct / 100} height={3} label="Exposure progress" />
        </div>
      )}
    </div>
  );
}
