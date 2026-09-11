// VideoMode.tsx - VIDEO · PLANETS on the manual bench (D-RIG-1).
//
// WHAT THIS MODE IS. Not a stream: a burst of short exposures written straight
// into one SER file. The camera is owned for the whole recording, the frames go
// to disk unstretched, and what comes back is a file - which is why the surface
// looks like a capture and not like a preview, and why the lane is labelled
// "recording" (`hub.py:307-311`): a restart mid-file leaves a truncated .ser.
//
// TWO CHANNELS CARRY THE PROGRESS AND BOTH ARE NEEDED.
//   * The `video` bus event, about 2 Hz, is the fast one - and the only one in
//     the store (`store.ts case "video"`).
//   * `GET /api/capture/video` is the complete one: `roi`, `actual_fps`,
//     `clamped` and `clamp_reason` exist nowhere on the bus.
// So: one cold GET on mount (the bus is silent when nothing is recording), one
// GET after the 202, and a 5 s WATCHDOG GET while the `video` lane is busy but
// no tick has arrived. A dropped relay frame must not leave a bar frozen at a
// number that still looks live - that is the "green while wrong" shape this
// branch keeps finding, and a two-minute recording is long enough to meet it.
//
// WHAT IS DECIDED HERE AND WHAT IS THE SERVER'S. `video_routes.py:96-154`
// refuses in a fixed order and every refusal is the server's to make. This
// screen pre-empts only the facts the RIG published about itself (link, caps,
// roles, lanes, `camera.video_path`) and carries the rest back verbatim - which
// is why `no_video_path` on an engine older than S7c arrives as a 409 and
// replaces the controls, rather than being guessed from a camera's name.

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
import { ApiError } from "../../../../../api";
import {
  getVideoState, startVideo, stopVideo, videoSpaceShortfall,
  type VideoSpaceShortfall,
} from "../../../../../api/video";
import { fmtBytes } from "../../../../../lib/gallery";
import { useStatus, useStore, useVideo } from "../../../../../store";
import { useLock } from "../../../../lib/gateHook";
import { nav } from "../../../../router";
import { ActionButton, Card, Divider, Label, Mono } from "../../../../ui";
import { videoRefusal, videoStopReason, type CaptureGateInput } from "../captureGate";
import { RecordingsList } from "./RecordingsList";
import { RoiPicker } from "./RoiPicker";
import { VideoControls, type VideoSettings } from "./VideoControls";
import { useVideoLibrary } from "./useVideoLibrary";
import {
  STILL_STILL_WORKS_NOTE, centredRoi, diskShortfallSentence, estimateBytes,
  laneBusy, mergeVideoState, noVideoPathReason, plannedFrames, progress,
  videoCapability,
} from "./videoModel";
import type { VideoRoi, VideoState } from "../../../../../types";

/** How long a recording may go without a bus tick before the screen asks the
 *  rig directly. Two ticks' worth of the recorder's own 2 Hz cadence would be
 *  1 s, which would poll for a slow relay; 5 s is slow enough to be a watchdog
 *  and fast enough that nobody watches a stale number for a whole sub. */
export const WATCHDOG_MS = 5000;

/** The engine has no video routes at all (S7L not deployed on this rig). Said
 *  once, in place of every control, because a screen full of locked buttons
 *  would blame the operator's role for a missing engine. */
export const NO_VIDEO_ROUTE_NOTE =
  "This engine does not carry SER recording yet. Update the rig to record video; "
  + "single frames, LOOP and Live View are unaffected.";

export const KEEP_OPTIONS = [10, 25, 50] as const;

export interface VideoModeProps {
  /** The bench's shared gate input, so VIDEO and STILL cannot disagree about
   *  who is holding the camera. */
  gate: CaptureGateInput;
  /** The name this bench is working on - the adopted capture target, or the
   *  one the Sky hand-off carried in `?target=`. Context only: `POST
   *  /api/capture/video` takes no target and the .ser carries no OBJECT card,
   *  which is what the line below says out loud. */
  target: string;
  onExplain: (reason: string) => void;
}

export function VideoMode(props: VideoModeProps): JSX.Element {
  const status = useStatus();
  const busEvent = useVideo();
  const enqueueToast = useStore((s) => s.enqueueToast);

  const cap = useMemo(() => videoCapability(status), [status]);
  const alignPublished = Array.isArray(
    (status?.camera as { roi_align?: unknown } | undefined)?.roi_align,
  );

  // ---------------------------------------------------------------- settings
  // Seeded ONCE from the still bench's gain and offset, so a fresh VIDEO mode
  // starts at numbers this camera is known to work at rather than at a
  // hard-coded guess - and held locally afterwards, because a recording's gain
  // is a per-recording decision and writing it back would move the defaults
  // every flow starts from.
  const [settings, setSettings] = useState<VideoSettings>(() => {
    const cap0 = useStore.getState().frameSettings.capture;
    return {
      fps: 30, exposureMs: 8, gain: cap0.gain, offset: cap0.offset, durationS: 10,
    };
  });
  const [roiDraft, setRoiDraft] = useState<VideoRoi | null>(null);
  const roi = roiDraft
    ?? centredRoi(0, cap.sensorW, cap.sensorH, 1, cap.roiAlign);

  const frames = plannedFrames(settings.durationS, settings.fps);
  const estBytes = estimateBytes({ w: roi.w, h: roi.h, bin: roi.bin, targetFrames: frames });

  // ------------------------------------------------------------- the recorder
  const [fetched, setFetched] = useState<VideoState | null>(null);
  const [routeAbsent, setRouteAbsent] = useState(false);
  const [starting, setStarting] = useState(false);
  /** The synchronous half of the in-flight guard - see `onRecord`. */
  const startingRef = useRef(false);
  const [refusal, setRefusal] = useState<{ code?: string; message: string } | null>(null);
  const [shortfall, setShortfall] = useState<VideoSpaceShortfall | null>(null);

  const lastTickRef = useRef(0);
  const refreshState = useCallback(() => {
    lastTickRef.current = Date.now();
    return getVideoState().then(
      (s) => { setFetched(s); setRouteAbsent(false); },
      (e: unknown) => {
        const err = e as ApiError;
        if (err?.status === 404) { setRouteAbsent(true); return; }
        // Any other failure leaves whatever was on screen: the bus is still a
        // source, and blanking a live recording because one GET failed would
        // be worse than the stale number the watchdog exists to prevent.
      },
    );
  }, []);

  useEffect(() => { void refreshState(); }, [refreshState]);

  // A bus tick is proof the channel is alive; stamp it so the watchdog can tell
  // "nothing is happening" from "nothing is arriving".
  useEffect(() => { if (busEvent) lastTickRef.current = Date.now(); }, [busEvent]);

  const recordingLane = laneBusy(status, "video");
  useEffect(() => {
    if (!recordingLane) return;
    const t = setInterval(() => {
      if (Date.now() - lastTickRef.current >= WATCHDOG_MS) void refreshState();
    }, WATCHDOG_MS);
    return () => clearInterval(t);
  }, [recordingLane, refreshState]);

  const live = mergeVideoState(fetched, busEvent);
  const prog = progress(live);
  const recording = !!live?.active || recordingLane;

  // ------------------------------------------------------------------- gates
  const mediaLock = useLock({ cap: "view.media" });
  const previewLock = useLock({ cap: "view.preview" });
  const stackLock = useLock({ cap: "control.capture" });

  const pathReason = noVideoPathReason(cap.name);
  const recordReason = videoRefusal(props.gate, {
    path: cap.path,
    pathReason,
    // Only `no_video_path` is sticky: it is a property of this camera, not of
    // this moment. A `camera_busy` answer that latched onto the button would
    // keep refusing after the camera was free again.
    serverRefusal: refusal?.code === "no_video_path" ? refusal.message : null,
    recording,
    capturing: laneBusy(status, "capture") || laneBusy(status, "looping"),
    starting,
  });
  const stopReason = videoStopReason(props.gate);
  const editReason = recording
    ? "A recording is running - these apply to the next one."
    : videoStopReason(props.gate);

  const stackBusyReason = laneBusy(status, "video_stack")
    ? "A stack is already running - only one runs at a time."
    : null;

  // ------------------------------------------------------------- the library
  const [keepPct, setKeepPct] = useState<number>(25);
  const library = useVideoLibrary(!routeAbsent);

  // -------------------------------------------------------------- the verbs
  const onRecord = () => {
    // BELT AND BRACES, and the ref is the belt. `starting` now reaches
    // `recordReason`, so the armed button refuses the second press and says
    // why; the ref catches the case state cannot - two handlers firing inside
    // one React batch both read the same stale `starting`, which is the shape
    // the power sheet's `inFlightRef` exists for. A second POST here does not
    // fail cleanly: it starts a SECOND recording and abandons the first file
    // half-written.
    if (startingRef.current) return;
    startingRef.current = true;
    setShortfall(null);
    setRefusal(null);
    setStarting(true);
    startVideo({
      roi, fps: settings.fps, exposure_ms: settings.exposureMs,
      gain: settings.gain, offset: settings.offset,
      duration_s: settings.durationS, format: "ser",
    }).then(
      (r) => {
        // The 202's own claim, on screen at once so the press has an effect
        // before the first bus tick: the recorder accepted and is arming, no
        // frames have landed. `arming` renders an indeterminate bar, never a
        // 0 percent one - see `progress()`. The GET below replaces it.
        setFetched({
          active: true, id: r.id, state: "arming", frames: 0,
          target_frames: r.target_frames, elapsed_s: 0, fps: 0, dropped: 0, bytes: 0,
          roi: r.roi, requested_fps: settings.fps, actual_fps: r.actual_fps,
          clamped: r.clamped, clamp_reason: r.clamp_reason,
          started_ts: null, finished_ts: null, error: null,
        });
        // The server rounded the subframe again; adopt what it actually used so
        // the picker stops showing a rectangle nothing is recording at.
        setRoiDraft(r.roi);
        void refreshState();
      },
      (e: unknown) => {
        const short = videoSpaceShortfall(e);
        if (short) { setShortfall(short); return; }
        const err = e as ApiError;
        setRefusal({ code: err?.code, message: err?.message ?? "the recording was refused" });
      },
    ).finally(() => { startingRef.current = false; setStarting(false); });
  };

  const onStop = () => {
    stopVideo().then(
      (r) => {
        enqueueToast({
          level: "info",
          title: r.cancelled
            ? `Recording stopped - ${r.frames.toLocaleString("en")} frames, ${fmtBytes(r.bytes)} written.`
            : "Nothing was recording - the file, if there is one, is already closed and valid.",
        });
        void refreshState();
        library.refresh();
      },
      (e: unknown) => {
        const err = e as ApiError;
        enqueueToast({ level: "warning", title: err?.message ?? "the stop was refused" });
      },
    );
  };

  // ------------------------------------------------------------------ render
  if (routeAbsent) {
    return (
      <div className="nx-vid" data-testid="rig-capture-video">
        <Card tone="dashed">
          <Label size={11}>VIDEO · PLANETS</Label>
          <Mono size={11} tone="dim" data-testid="video-no-route">{NO_VIDEO_ROUTE_NOTE}</Mono>
        </Card>
      </div>
    );
  }

  const cannotRecord = cap.path === "none" || refusal?.code === "no_video_path";
  const refusalSentence = refusal?.code === "no_video_path" ? refusal.message : pathReason;

  return (
    <div className="nx-vid" data-testid="rig-capture-video">
      <div className="nx-vid-head">
        <Label size={11}>VIDEO · PLANETS</Label>
        <Mono size={10} tone="dim">
          {props.target
            ? `on ${props.target} - the .ser is named by its timestamp, not by the target`
            : "no target named - the .ser is named by its timestamp"}
        </Mono>
      </div>

      {cannotRecord ? (
        <Card tone="dashed">
          <div className="nx-vid-refusal" data-testid="video-refusal">
            <Mono size={11} tone="warn">{refusalSentence}</Mono>
            <Mono size={10.5} tone="dim">{STILL_STILL_WORKS_NOTE}</Mono>
          </div>
        </Card>
      ) : (
        <>
          <RoiPicker
            roi={roi}
            onChange={setRoiDraft}
            sensorW={cap.sensorW}
            sensorH={cap.sensorH}
            align={cap.roiAlign}
            alignPublished={alignPublished}
            maxBin={status?.camera?.max_bin ?? 4}
            lockedReason={editReason}
            onExplain={props.onExplain}
          />

          <Divider />

          <VideoControls
            settings={settings}
            onChange={(patch) => {
              setSettings((s) => ({ ...s, ...patch }));
              setRefusal(null);
              setShortfall(null);
            }}
            plannedFrames={frames}
            estBytes={estBytes}
            maxFps={cap.maxFps}
            state={live}
            progress={prog}
            recordReason={recordReason}
            stopReason={stopReason}
            editReason={editReason}
            recording={recording}
            starting={starting}
            onRecord={onRecord}
            onStop={onStop}
            onExplain={props.onExplain}
          />

          {shortfall && (
            <Card tone="dashed">
              <div className="nx-vid-refusal" data-testid="video-disk">
                <Mono size={11} tone="warn">
                  {diskShortfallSentence(shortfall.free_bytes, shortfall.required_bytes, fmtBytes)}
                </Mono>
                <ActionButton
                  kind="ghost"
                  onPress={() => nav.sheet("files", { src: "manual" })}
                  data-testid="video-files-link"
                >
                  FILES &gt;
                </ActionButton>
              </div>
            </Card>
          )}

          {refusal && refusal.code !== "no_video_path" && (
            <div className="nx-vid-refusal" data-testid="video-server-refusal">
              <Mono size={11} tone="warn">{refusal.message}</Mono>
              {refusal.code === "lane_busy" && recording && (
                <ActionButton
                  kind="danger"
                  onPress={onStop}
                  lockedReason={stopReason}
                  onExplain={props.onExplain}
                  data-testid="video-stop-other"
                >
                  STOP THE RECORDING
                </ActionButton>
              )}
            </div>
          )}
        </>
      )}

      <Divider />

      <div className="nx-vid-keep" role="group" aria-label="Frames a quick stack keeps">
        {KEEP_OPTIONS.map((k) => (
          <button
            key={k}
            type="button"
            className="nx-vid-keep-opt"
            data-on={keepPct === k ? "true" : "false"}
            aria-pressed={keepPct === k}
            onClick={() => setKeepPct(k)}
            aria-label={`Keep the sharpest ${k} percent of the frames`}
          >
            {`KEEP ${k}%`}
          </button>
        ))}
      </div>

      <RecordingsList
        recordings={library.recordings}
        loading={library.loading}
        error={library.error}
        limit={1}
        keepPct={keepPct}
        mediaReason={mediaLock.lockedReason}
        previewReason={previewLock.lockedReason}
        stackReason={stackLock.lockedReason ?? stackBusyReason}
        deleteReason={stackLock.lockedReason}
        stackingId={library.stackingId}
        writingId={live?.active ? live.id : null}
        onStack={(id) => library.stack(id, keepPct)}
        onDelete={library.remove}
        onExplain={props.onExplain}
      />

      <ActionButton
        kind="secondary"
        full
        onPress={() => nav.sheet("videoLibrary")}
        data-testid="video-all-recordings"
      >
        {library.recordings != null
          ? `ALL RECORDINGS (${library.recordings.length}) >`
          : "ALL RECORDINGS >"}
      </ActionButton>
    </div>
  );
}
