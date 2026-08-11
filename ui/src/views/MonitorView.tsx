// ============================================================================
// MonitorView — the unified glanceable run dashboard (monitor spec §4 / §7).
// Lane 2E. Reads ONLY store slices via the landed narrow hooks (no polling of
// its own beyond one cold-load snapshot on mount, §8). Read-only for device
// controls; the only writes are Pause/Resume (non-destructive) and Abort
// (hold-to-confirm, works over plain HTTP when the WS is down — A2).
//
// Mobile-first single column at 375px in night mode; sm => 2 cols; lg => 12-col
// mosaic. Glance order = most-actionable first (Progress + Countdowns above the
// fold). The 1s wall-clock ticker lives only inside leaf cells (LiveTimer /
// CountdownTile), never here (resolves G1).
// ============================================================================

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { u } from "../lib/base";
import {
  useSeq,
  useGuideRecent,
  useGuideRms,
  useCamera,
  useMeridian,
  useMount,
  useLiveness,
  usePreview,
  useStatus,
  useWsConnected,
  useTelemetryStale,
  useNight,
  useLogs,
  useSafety,
  useBackendLinks,
  useBootConnectFailed,
  useProviders,
  useWeather,
  useSite,
  useStore,
  usePhotometry,
} from "../store";
import { Panel, Stat, EmptyState } from "../components/ui";
import { Icon } from "../components/icons";
import {
  subNoise, skyElectronsPerSub, skyRateEPerSec, skyLimitedSubSeconds,
  subLengthVerdict, moreSubsForSnrMultiple,
} from "../lib/photometry";
import { formatScheduleStatus } from "../lib/scheduleStatus";
import SkyConditionsPanel from "../components/weather/SkyConditionsPanel";
import RadarMap from "../components/weather/RadarMap";
import { getDomeState, type DomeState } from "../api/backends";
import { domeStatusLabel } from "../lib/dome";
import { accessPhrase, useCanControlMount, useCanViewWeather } from "../lib/caps";
import {
  CountdownTile,
  deriveHealthIssues,
  HealthStrip,
  HoldButton,
  LiveTimer,
  LiveTrendStrip,
  MetricStrip,
  PauseButton,
  PreviewTile,
  RmsVerdict,
  Sparkline,
  StateBadge,
  ThermometerBar,
  useReducedMotion,
} from "../components/monitor";
import { pushLiveSample, pickSeries, type LiveSample } from "../lib/reportChart";
import {
  fmtCountdown,
  fmtDuration,
  GUIDE_STALE_S,
  LIVE_WINDOW_S,
  stallLevel,
  THUMB_BRIGHTNESS_NIGHT_DEFAULT,
} from "../lib/eta";
import { diagnoseFailure, runFailureLog } from "../lib/troubleshoot";
import type { MonitorSnapshot, PreviewInfo } from "../types";

// ---------------------------------------------------------------- thumb dimmer
// The per-tile night brightness slider is the monitor lane's own persisted pref
// (master §A.2 persistence rules: `astrodeck-monitor-thumb-brightness`). Day
// default 1.0, night default 0.5 (resolves D1/E.11).
const THUMB_KEY = "astrodeck-monitor-thumb-brightness";

function loadThumbBrightness(night: boolean): number {
  try {
    const raw = localStorage.getItem(THUMB_KEY);
    if (raw != null) {
      const n = Number(raw);
      if (Number.isFinite(n) && n >= 0.2 && n <= 1) return n;
    }
  } catch {
    /* unavailable */
  }
  return night ? THUMB_BRIGHTNESS_NIGHT_DEFAULT : 1;
}

// ---------------------------------------------------------------- pause phase
/** How long after an exposure ends the frame can still be downloading/saving —
 *  the window in which "finishing this frame" is still true even though the
 *  shutter has closed. Only a backstop; `frames_done` advancing is the signal. */
const FRAME_BANK_GRACE_S = 30;

// ---------------------------------------------------------------- 1s page tick
// A single coarse ticker for the NON-leaf liveness text ("last frame N ago",
// STALE chips, stall detection). Leaf countdowns own their own tick; this one
// drives only the few derived booleans below, so a per-second repaint of the
// grid is cheap and bounded.
function useCoarseTick(): number {
  const [n, setN] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setN(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return n;
}

export default function MonitorView() {
  const seq = useSeq();
  const guideRecent = useGuideRecent();
  const guideRms = useGuideRms();
  const camera = useCamera();
  const mount = useMount();
  const liveness = useLiveness();
  const preview = usePreview();
  const status = useStatus();
  const wsConnected = useWsConnected();
  const telemetryStale = useTelemetryStale();
  const night = useNight();
  const logs = useLogs();
  const safety = useSafety();
  const backendLinks = useBackendLinks();
  const bootConnectFailed = useBootConnectFailed();
  const providers = useProviders();
  const setView = useStore((s) => s.setView);
  const openHelp = useStore((s) => s.openHelp);
  // PRO-6 "Sub quality" tile (photometry/SNR design §3 Task 6) — reads the same
  // tested photometry.ts core as Capture's Suggest + Sequence's advisory chips.
  const photometryProfile = usePhotometry();
  // VIEWER-READ-ONLY (W2.5): the Monitor is a glance dashboard; its only writes are
  // Pause/Resume/Abort. Those hit the same /api/sequence/{pause,resume,abort}
  // routes as SequenceView's run controls, which require control.mount (a
  // running sequence slews the mount) — NOT control.capture. A viewer (and an
  // operator, who lacks control.mount) sees the dashboard fully; the controls
  // row stays in place but DISABLED with a lock note (R4B-MON-01/02: stable
  // screen anatomy — permissions change enabled state, never what exists).
  const canRun = useCanControlMount();

  const reducedMotion = useReducedMotion();
  const now = useCoarseTick();

  // ----- thumbnail night-dimmer (persisted) -----
  const [thumbBrightness, setThumbBrightness] = useState(() => loadThumbBrightness(night));
  // Stable identity so the memoized PreviewTile isn't re-rendered every coarse
  // tick just because this callback was re-created (P3-8).
  const setBrightness = useCallback((v: number) => {
    setThumbBrightness(v);
    try {
      localStorage.setItem(THUMB_KEY, String(v));
    } catch {
      /* quota */
    }
  }, []);

  // ----- memo-stable PreviewTile props (P3-8): recompute only when the frame
  // changes, not on every 1s coarse tick, so the memoized tile stays put. -----
  const previewMeta = useMemo(
    () => (preview ? `${preview.exposure_s}s · g${preview.gain} · ${preview.binning}×` : undefined),
    [preview],
  );
  const previewClip = useMemo(() => isClipping(preview), [preview]);
  const openCapture = useCallback(() => setView("capture"), [setView]);

  // ----- ETA anchor: store {eta_s, receivedAt} on each new sequence frame so the
  // LiveTimer derives finish from one client clock (resolves C11/B4). -----
  const progress = seq.progress;
  // PRO-6 "Sub quality" (Task 6): computed once per render from the live preview
  // + profile, gated below on data_is_linear + a non-empty profile so a NINA
  // (decoded, non-linear) frame or an inert profile never produces a chip.
  const subQualityLinear = !!preview && preview.data_is_linear;
  const subQualityProfileFilled = photometryProfile.egain > 0 && photometryProfile.readNoiseE > 0;
  const subQuality = (subQualityLinear && subQualityProfileFilled && preview)
    ? (() => {
        const skyE = skyElectronsPerSub(preview.stats.median, photometryProfile.biasAdu, photometryProfile.egain);
        const noise = subNoise(skyE, photometryProfile.readNoiseE);
        const skyLimitedS = skyLimitedSubSeconds(
          photometryProfile.readNoiseE, skyRateEPerSec(skyE, preview.exposure_s));
        const verdict = subLengthVerdict(preview.exposure_s, skyLimitedS);
        return { noise, skyLimitedS, verdict };
      })()
    : null;
  const etaAnchorRef = useRef<{ etaS?: number; receivedAtMs: number }>({ receivedAtMs: Date.now() });
  const lastEtaSentinel = useRef<number | null>(null);
  // Re-anchor when the server emits a fresh frame (server_now_ms changes) or eta.
  const sentinel = progress?.server_now_ms ?? progress?.frames_done ?? null;
  if (sentinel !== lastEtaSentinel.current) {
    lastEtaSentinel.current = sentinel;
    etaAnchorRef.current = { etaS: progress?.eta_s, receivedAtMs: Date.now() };
  }
  // ----- did THIS client watch the current frame start? (UX round-4 S4) -----
  // The engine publishes a progress snapshot at each frame BOUNDARY and never
  // again while the exposure runs, so every field in it — server_now_ms,
  // frame_started_at_ms, elapsed_s — is frozen for the whole sub (measured on
  // /api/sequence/state: both ms values byte-identical for 40 s, then jumping
  // together). A client that was watching when the snapshot arrived knows the
  // frame's real age; a client that opened the dashboard mid-sub does NOT, and
  // must say so instead of drawing a bar that reads "this frame just started".
  const frameStartMs = progress?.frame_started_at_ms ?? null;
  const sawFrameGapRef = useRef(false);
  const firstFrameStartRef = useRef<number | null>(null);
  if (frameStartMs == null) sawFrameGapRef.current = true;
  else if (firstFrameStartRef.current == null) firstFrameStartRef.current = frameStartMs;
  const joinedMidFrame =
    !sawFrameGapRef.current && frameStartMs != null && frameStartMs === firstFrameStartRef.current;

  // ----- live 4-series ring for the dew early-warning trend AND the "Live
  // trend" panel (resolves B9; generalizes the old single-HFR ring — report
  // viewer spec §3 Task 5). Snapshots HFR/stars/RMS/temp on each new sub
  // (`preview.id` change); capped at 40, no store slice. The inline Progress
  // sparkline below AND LiveTrendStrip both read this SAME ring — one ring,
  // not two. -----
  const liveRing = useRef<LiveSample[]>([]);
  const lastSubId = useRef<number | null>(null);
  if (preview && preview.id !== lastSubId.current) {
    lastSubId.current = preview.id;
    liveRing.current = pushLiveSample(
      liveRing.current,
      {
        t: Date.now(),
        hfr: preview.hfr ?? null,
        stars: preview.stars ?? null,
        rms: guideRms?.rms_total ?? null,
        temp: camera?.temperature ?? null,
      },
      40,
    );
  }
  const liveHfr = pickSeries(liveRing.current, "hfr");

  // ----- cold-load hydration (one shot, non-fatal, §8) -----
  // UX-2026-07-26 #22: reopening the dashboard mid-run showed LAST FRAME as an
  // empty black box captioned NO FRAME YET with dozens of frames already on
  // disk, because `preview` only ever arrives over the WS on the NEXT frame —
  // so the 3am first read was "the camera has died" for a whole exposure. The
  // snapshot already carries `preview_id`; hold it here and hand it to the tile
  // (as STALE, which is the truth: it is the last frame, not a live one) until
  // a real preview event lands.
  const [coldPreviewId, setColdPreviewId] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const signal =
          typeof AbortSignal !== "undefined" && "timeout" in AbortSignal
            ? (AbortSignal as unknown as { timeout(ms: number): AbortSignal }).timeout(4000)
            : undefined;
        const res = await fetch(u("/api/monitor/snapshot"), signal ? { signal } : undefined);
        if (!res.ok || cancelled) return;
        const snap = (await res.json()) as MonitorSnapshot;
        if (cancelled) return;
        // Seed the store via handleEvent so the regular WS path stays the SSOT.
        const h = useStore.getState().handleEvent;
        const ts = Date.now() / 1000;
        if (snap.status) h({ type: "status", data: snap.status as unknown as Record<string, unknown>, ts });
        if (snap.sequence) h({ type: "sequence", data: snap.sequence as unknown as Record<string, unknown>, ts });
        if (snap.preview_id != null) setColdPreviewId(snap.preview_id);
      } catch {
        /* WS catches up within ~2s — non-fatal */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ----- observatory roof / dome (UX-2026-07-26 #27) -----
  // The roof state existed ONLY as a badge in Settings → Safety, so a run that
  // ended with the roof shut said nothing on the dashboard. /api/dome/state is
  // the same route that badge reads; it is outside the WS status frame, so it
  // gets its own slow poll (15s — a shutter takes tens of seconds to move).
  const [dome, setDome] = useState<DomeState | null>(null);
  useEffect(() => {
    let live = true;
    const tick = () => {
      getDomeState()
        .then((d) => { if (live) setDome(d); })
        .catch(() => { /* no dome route / offline — the row simply stays hidden */ });
    };
    tick();
    const id = window.setInterval(tick, 15000);
    return () => { live = false; window.clearInterval(id); };
  }, []);

  // ----- derived liveness -----
  const frameAgeMs = liveness.frame != null ? now - liveness.frame : null;
  const guideAgeMs = liveness.guide != null ? now - liveness.guide : null;
  // TWO CLOCKS, on purpose (#206). `frameAge` is how long since a PREVIEW
  // arrived here — the right input for a LIVE badge, which is a claim about
  // pictures. `captureAge` is how long since the RIG's frames_done advanced —
  // the right input for a stall alarm, which is a claim about the camera. They
  // used to be the same number, so a slow relay dropping preview JPEGs read as
  // CAPTURE STALLED while every sub was landing on disk.
  const captureAgeMs = liveness.capture != null ? now - liveness.capture : null;
  const captureAgeS = captureAgeMs != null ? captureAgeMs / 1000 : null;
  const live = frameAgeMs != null && frameAgeMs < LIVE_WINDOW_S * 1000;
  const guideStale = guideAgeMs != null && guideAgeMs > GUIDE_STALE_S * 1000;

  const state = seq.state;
  const running = state === "running";
  const paused = state === "paused";
  // "aborting" IS LIVE (types.ts): the engine publishes it for the whole ~210 s
  // wind-down — the exposure is being aborted, the guider stopped, the flat
  // panel switched off — and only says "aborted" once the rig has stopped.
  // Excluding it from `runActive` unmounted the controls row (with the Abort
  // button in it), the health strip's run context and the meridian chip over a
  // rig that was still moving. Same fold as PolarView's "pausing".
  const aborting = state === "aborting";
  const runActive = running || paused || aborting;
  const finished = state === "complete";
  const failed = state === "aborted" || state === "error";
  const ninaNative = state === "nina_native";
  const idle = state === "idle";
  const waitStatus = formatScheduleStatus(seq.schedule, seq.live, now / 1000);

  // ----- abort/error one-shot vibration (resolves H / §7) -----
  const vibratedError = useRef(false);
  useEffect(() => {
    if ((state === "error" || state === "aborted") && !vibratedError.current) {
      vibratedError.current = true;
      try {
        navigator.vibrate?.([60, 40, 60]);
      } catch {
        /* unsupported */
      }
    }
    if (state === "running" || state === "idle") vibratedError.current = false;
  }, [state]);

  // ----- when did THIS run start? (UX-2026-07-26 #23) -----
  // The failure card quotes the tail of the error/warning log, and unfiltered
  // that tail reached back into a DIFFERENT run — a card inventing a weather
  // event that never happened. There is no run-start field on the wire, but
  // `progress.elapsed_s` is server-computed, so while the run is live the start
  // instant is `now - elapsed`. Recompute it while running and FREEZE it at the
  // terminal state (elapsed stops advancing there, so `now - elapsed` would
  // drift later and later after the fact). Null until a run is observed live:
  // a page opened after the run ended can attribute nothing, and runFailureLog
  // then shows no excerpt rather than someone else's lines.
  //
  // Caveat, stated: elapsed_s EXCLUDES paused time, so on a run that was paused
  // the anchor lands late by the pause duration and a few early lines can drop
  // out of the excerpt. That errs toward showing less, never toward fabricating.
  const runStartedAtRef = useRef<number | null>(null);
  if (state === "idle") runStartedAtRef.current = null;
  else if (runActive && progress) {
    runStartedAtRef.current = Date.now() / 1000 - progress.elapsed_s;
  }

  // ----- how did this run END? (UX-2026-07-26 #23) -----
  // One diagnosis, computed once: the failure card renders it, and the health
  // strip below needs to know whether this was a fault at all.
  const endDiag = diagnoseFailure(seq.detail, {
    state,
    framesDone: progress?.frames_done,
    framesTotal: progress?.frames_total,
  });
  const stoppedByUser = state === "aborted" && !!endDiag.userInitiated;

  // ----- PAUSE IS NOT INSTANT (UX-2026-08-05 #5) -----
  // `engine.pause()` sets state="paused" the moment the route is called, but the
  // engine only reads the flag at the top of the frame loop (`_checkpoint`), so
  // the sub already in flight runs to completion — up to ten minutes of open
  // shutter under a PAUSED badge, which is the state someone uncaps the scope or
  // walks back out with a head-torch in. Hold a distinct PAUSING phase from the
  // instant the pause lands until that frame is banked, and keep the sub-frame
  // bar up for the whole of it: the bar is the only thing on this screen that
  // says the shutter is still open.
  //
  // The pause POST itself publishes a fresh progress snapshot, so
  // `server_now_ms - frame_started_at_ms` read at that moment is the frame's
  // TRUE age even for a client that opened the dashboard mid-sub; the client
  // clock carries it from there (same anchor idiom as SubFrameBar below).
  const curExp = progress?.current_exposure_s ?? 0;
  const frameElapsedS =
    progress?.frame_started_at_ms != null
      ? Math.max(0, ((progress.server_now_ms ?? progress.frame_started_at_ms)
          - progress.frame_started_at_ms) / 1000)
        + Math.max(0, now - etaAnchorRef.current.receivedAtMs) / 1000
      : null;
  // Set while a pause is waiting on the frame that was in flight when it landed;
  // null when the pause was clean (it arrived between frames) or has completed.
  const pausingRef = useRef<{ framesDone: number; untilMs: number } | null>(null);
  const wasPausedRef = useRef(paused);
  if (paused !== wasPausedRef.current) {
    wasPausedRef.current = paused;
    const remainS = curExp > 0 && frameElapsedS != null ? curExp - frameElapsedS : 0;
    pausingRef.current =
      paused && progress != null && remainS > 0
        ? {
            framesDone: progress.frames_done,
            // frames_done advancing is the real terminal signal. This is only
            // the backstop for the cases where it never does — a frame the
            // quality gate rejects in accepted-count mode, or a snapshot lost
            // with the socket — because a claim about the shutter must not
            // outlive the evidence for it.
            untilMs: Date.now() + (remainS + FRAME_BANK_GRACE_S) * 1000,
          }
        : null;
  }
  const pausing =
    paused
    && pausingRef.current != null
    && progress != null
    && progress.frames_done === pausingRef.current.framesDone
    && now < pausingRef.current.untilMs;
  const pauseFrameRemainingS =
    pausing && frameElapsedS != null ? Math.max(0, curExp - frameElapsedS) : null;

  // ----- stall detection (resolves A5; gated to running-only — R3-MON-01) -----
  const stallLvl = stallLevel(state, captureAgeS, curExp);
  const stallSoft = stallLvl !== "none";
  const stallHard = stallLvl === "red";
  const vibratedStall = useRef(false);
  useEffect(() => {
    if (stallHard && !vibratedStall.current) {
      vibratedStall.current = true;
      try {
        navigator.vibrate?.(120);
      } catch {
        /* unsupported */
      }
    }
    if (!stallHard) vibratedStall.current = false;
  }, [stallHard]);

  // UX-41: control writes must give feedback even when the WS is DOWN (the exact
  // case Abort exists for) — the store's log->toast path rides the WS, so a
  // failed/timed-out POST there is invisible. Send over a plain fetch (with a 4s
  // timeout), check res.ok, and enqueue a CLIENT-side toast on both outcomes so
  // confirm-to-act is never silent.
  //
  // `ok` overrides the success copy. "Pause sent" was true about the REQUEST and
  // false about the rig: the route returns the instant the flag is set, while
  // the exposure it interrupts keeps running (see the PAUSING phase above), so
  // the toast said green-and-done over an open shutter.
  const sendControl = (label: string, path: string, ok?: { title: string; detail?: string }) => {
    const toast = useStore.getState().enqueueToast;
    void (async () => {
      try {
        const res = await fetch(u(path), {
          method: "POST",
          signal:
            typeof AbortSignal !== "undefined" && "timeout" in AbortSignal
              ? (AbortSignal as unknown as { timeout(ms: number): AbortSignal }).timeout(4000)
              : undefined,
        });
        if (!res.ok) {
          toast({ level: "error", title: `${label} failed (${res.status})` });
          return;
        }
        toast({ level: "success", title: ok?.title ?? `${label} sent`, detail: ok?.detail });
      } catch {
        toast({ level: "error", title: `${label} failed — link may be down` });
      }
    })();
  };
  const abort = () => sendControl("Abort", "/api/sequence/abort");

  // ----- the single "is my night OK?" verdict (implementation brief §5) -----
  // Folds safety/disk/backend_links/meridian/nina_link/status.providers/boot +
  // the engine's end_reason into ranked tier-1 (Notice)/tier-2 (Act) issues.
  const weather = useWeather();
  // view.weather (2026-07-17 decisions wave I2): split off view.site_precise
  // so an operator sees Sky Conditions + Radar too, not just admin.
  const canSeeWeather = useCanViewWeather();

  // ----- is anything actually watching the sky? (UX-2026-07-26 #28) -----
  // Mirrors the server's own gate (weather.py tick: `wcfg.enabled and not
  // site.is_default`) — an enabled monitor on a default (0,0) site fetches
  // nothing, so it is just as unwatched. Config is the SSOT here rather than
  // the `weather` store slice, because a DISABLED monitor never publishes a
  // weather event at all, so that slice stays null and can't be distinguished
  // from "not hydrated yet".
  const site = useSite();
  const weatherCfgEnabled = useStore((s) => s.config?.weather?.enabled);
  const weatherMonitored =
    (weatherCfgEnabled ?? weather?.enabled ?? true) && !(site?.is_default ?? false);

  // ----- roof, derived (UX-2026-07-26 #27) -----
  // Only rendered when a dome actually exists: a rig with no roof must not grow
  // a permanent "no roof" row. `roofAlarm` is the case the pro hit — the run is
  // live and the shutter is anything but open.
  const roofConnected = !!dome?.connected;
  const roofShutter = dome?.shutter ?? "unknown";
  const roofAlarm = roofConnected && runActive && roofShutter !== "open";
  // UX round-4 S4. "Roof open" carried a ✓ whenever it wasn't the closed-during-a-
  // run case — including while the safety monitor was reporting rain, three lines
  // under a red "Unsafe — rain sensor wet" banner. Open is only the GOOD state
  // when it is safe to be open; with an unsafe (or non-reporting, which the
  // engine's own gate treats as unsafe) monitor, an open roof is the exposure,
  // so it must not wear the tick. The banner above already says WHY — this only
  // stops the glyph from contradicting it.
  const safetyUnsafe =
    !!safety?.connected && (safety.reading == null || safety.reading.stale || !safety.reading.is_safe);
  const roofOpenWhileUnsafe = roofConnected && roofShutter === "open" && safetyUnsafe;

  const healthIssues = useMemo(
    () =>
      deriveHealthIssues({
        safety,
        weather,
        disk: status?.disk,
        // UX-2026-07-26 #21 (alarm fatigue): the meridian block is a property of
        // a RUN. With no plan loaded the hub reports `flip_disabled` simply
        // because there is no plan to enable it on, and the strip then shouted
        // "Meridian flip disabled — pier risk near meridian" at an idle rig,
        // forever. Feed it only while a run is actually in flight.
        meridian: runActive ? status?.meridian : null,
        ninaLink: status?.nina_link,
        backendLinks,
        bootConnectFailed,
        providers,
        // UX-2026-07-26 #23: the strip raises a red ✗ "Sequence aborted" for
        // ANY aborted state, so holding ABORT lit the "is my night OK?" verdict
        // red for the one event that is not a problem — the operator's own
        // decision. Same idiom as the `meridian` line above: feed the helper
        // only what actually applies. The abort itself is still stated (the
        // ABORTED badge and the "You stopped the run" card), just not alarmed.
        seqState: stoppedByUser ? undefined : state,
        endReason: stoppedByUser ? undefined : seq.end_reason,
        wsConnected,
        telemetryStale,
      }),
    [safety, weather, status, backendLinks, bootConnectFailed, providers, state, seq.end_reason, wsConnected, telemetryStale, runActive, stoppedByUser],
  );

  // ====================================================================== render
  return (
    <div className="px-3 pb-20 sm:px-0 sm:pb-4">
      <div className="mb-3">
        {/* UX-2026-07-26 #28: with `weather.enabled === false` nothing is polling
            the sky, so lib/health.ts can never raise a weather issue and the
            empty-issues branch printed an unconditional "✓ Night looks OK".
            Pass the monitoring state so the calm line can say what it is
            actually calm ABOUT. */}
        <HealthStrip
          issues={healthIssues}
          weatherMonitored={weatherMonitored}
          onEnableWeather={canSeeWeather ? () => setView("settings") : undefined}
        />
      </div>

      {/* Roof/dome alarm (UX-2026-07-26 #27) — the run is live and the shutter
          is not open. Rendered above the grid so it is impossible to miss; the
          steady-state readout is the header chip below. */}
      {roofAlarm && (
        <div
          role="alert"
          className="mb-3 flex items-center gap-2 border border-bad/60 bg-bad/10 px-3 py-2 text-xs text-bad"
        >
          <Icon name="alert" size={14} className="shrink-0" />
          <span className="font-semibold">
            {domeStatusLabel(roofShutter)} — a run is in progress.
          </span>
        </div>
      )}

      <div
        className={`grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-12 auto-rows-min
          ${!wsConnected ? "[&_.data-dim]:opacity-60" : ""}`}
      >
        {/* ================================================== HEADER STRIP */}
        <header className="col-span-full sticky top-0 z-10 panel p-3 sm:p-4 backdrop-blur">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="min-w-0 flex flex-col gap-1">
              {/* PAUSING is a phase the wire has no word for — the server says
                  "paused" from the moment the flag is set. StateBadge only
                  speaks the server's five states, so the phase renders here. */}
              {pausing ? <PausingBadge /> : <StateBadge state={state} reducedMotion={reducedMotion} />}
              <div className="flex items-center gap-2 min-w-0 text-xs text-dim">
                {live && (
                  <span
                    className="inline-flex items-center gap-1 px-1.5 py-0.5 border text-[10px] tracking-widest uppercase"
                    style={{ borderColor: "var(--accent-dim)", color: "var(--accent)" }}
                  >
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--accent)" }} aria-hidden />
                    LIVE
                  </span>
                )}
                <span className="truncate text-ink">{seq.target ?? seq.plan_name ?? "—"}</span>
                {status?.filterwheel && (
                  <span className="mono text-dim shrink-0">
                    {status.filterwheel.names[status.filterwheel.position] ?? "—"}
                  </span>
                )}
                {/* roof state (UX-2026-07-26 #27) — glyph + the WORD, never a
                    colour alone; "open" is only GOOD while a run is in flight,
                    so the neutral info glyph is the resting case. */}
                {roofConnected && (
                  <span
                    className={`inline-flex items-center gap-1 shrink-0 ${
                      roofAlarm ? "text-bad font-semibold"
                        : roofShutter === "error" || roofOpenWhileUnsafe ? "text-warn"
                          : "text-dim"
                    }`}
                  >
                    <Icon
                      name={
                        roofAlarm || roofShutter === "error" || roofOpenWhileUnsafe ? "alert"
                          : roofShutter === "open" ? "check"
                            : "info"
                      }
                      size={12}
                    />
                    {domeStatusLabel(roofShutter)}
                  </span>
                )}
              </div>
            </div>

            {/* Finish clock = largest header text (>=20px). Hidden unless a run.
                While PAUSING the finish clock has nothing honest to say (LiveTimer
                prints "PAUSED"), so the slot carries the number that matters
                instead: how long until the shutter actually closes. Same for the
                teardown: the run has been cancelled, so the engine stops sending
                an ETA and the last one it did send is a countdown to a completion
                that will never arrive — the slot says what is actually happening
                instead of going blank, which would read as "over". */}
            {runActive && (
              <div className="data-dim">
                {aborting ? (
                  <div className="flex flex-col items-end leading-tight">
                    <span className="mono text-[20px] text-warn tabular-nums">—:—</span>
                    <span className="label !text-[10px]">stopping — no finish time</span>
                  </div>
                ) : pausing ? (
                  <div className="flex flex-col items-end leading-tight">
                    <span className="mono text-[20px] text-warn tabular-nums">
                      {pauseFrameRemainingS != null ? fmtCountdown(pauseFrameRemainingS) : "—:—"}
                    </span>
                    <span className="label !text-[10px]">shutter open — this frame first</span>
                  </div>
                ) : (
                  <LiveTimer
                    etaS={etaAnchorRef.current.etaS}
                    receivedAtMs={etaAnchorRef.current.receivedAtMs}
                    confident={progress?.eta_confident}
                    paused={paused}
                  />
                )}
              </div>
            )}
          </div>

          {waitStatus && (
            <div
              className={`mt-2 flex items-start gap-2 text-sm leading-snug ${
                waitStatus.tone === "warn" ? "text-warn" : "text-ink"
              }`}
              aria-live="polite"
            >
              <Icon
                name={waitStatus.tone === "warn" ? "alert" : "clock"}
                size={16}
                className={`shrink-0 mt-0.5 ${waitStatus.tone === "warn" ? "text-warn" : "text-accent"}`}
              />
              <span>{waitStatus.text}</span>
            </div>
          )}

          {/* controls row — only when run-related (idle/complete/nina show none).
              Rendered for EVERY role in the same position; non-control.mount
              roles get the controls DISABLED plus one shared lock note whose
              copy derives from the enforced capability (R4B-MON-01/02). */}
          {runActive && (
            <>
              <div className="flex items-center gap-2 mt-3">
                {/* A teardown is not pausable and not resumable — the engine
                    refuses both while it winds down (sequence/engine.py), so the
                    button must not offer them. */}
                <PauseButton
                  paused={paused}
                  disabled={!canRun || aborting}
                  onPause={() => sendControl("Pause", "/api/sequence/pause", {
                    title: "Pausing",
                    detail: "Any exposure already in flight finishes first — "
                      + "the run stops at the next frame boundary.",
                  })}
                  onResume={() => sendControl("Resume", "/api/sequence/resume")}
                />
                {/* THE ABORT STAYS ON SCREEN FOR THE WHOLE TEARDOWN — it is how
                    the operator knows the press landed — but it reports instead
                    of inviting a second hold. The engine no-ops a second abort
                    now; before that, a re-press cancelled a task already inside
                    its own cancellation handler and severed the wind-down. */}
                <HoldButton
                  face={aborting ? "Aborting…" : "Abort"}
                  label="Abort sequence"
                  danger
                  disabled={!canRun || aborting}
                  onConfirm={abort}
                  hint={aborting ? "ending the exposure and the guider"
                    : !wsConnected && canRun ? "link down — sending anyway" : undefined}
                />
              </div>
              {!canRun && (
                <p className="text-[11px] text-dim mt-2 inline-flex items-center gap-1.5">
                  <Icon name="lock" size={11} />
                  View only — pausing, resuming or aborting this run needs{" "}
                  {accessPhrase("control.mount")}.
                </p>
              )}
            </>
          )}
        </header>

        {/* ================================================== NINA-NATIVE */}
        {ninaNative && (
          <Panel className="col-span-full lg:col-span-8" title="NINA is driving">
            <div className="flex items-start gap-3">
              <Icon name="link" size={20} className="text-accent shrink-0 mt-0.5" />
              <div className="text-sm text-ink leading-snug">
                NINA is running this sequence. Open NINA for full progress and control.
                {seq.detail && <div className="text-xs text-dim mono mt-1 break-words">{seq.detail}</div>}
              </div>
            </div>
          </Panel>
        )}

        {/* ================================================== PROGRESS */}
        {!ninaNative && (
          <Panel className="col-span-full lg:col-span-8" title="Progress">
            {idle ? (
              <EmptyState
                icon="plan"
                title="No run active"
                hint="Plan a session to start capturing. Live guide, thermal and preview still work below if devices are connected."
                action={
                  <button className="btn btn-accent min-h-[44px]" onClick={() => setView("sequence")}>
                    Plan a session →
                  </button>
                }
              />
            ) : (
              <div className="data-dim flex flex-col gap-3">
                {/* Failure renders inline (resolves E3) — no drawer punt.
                    UX-2026-07-26 #23: a run the operator held ABORT to stop is
                    NOT a fault. It gets a neutral card, its own headline, no
                    advisory and no Help deep-link; only a genuine fault keeps
                    the red border + alarm glyph. The log excerpt is scoped to
                    THIS run (runFailureLog) so the card can never quote a
                    previous run's lines back as if they explained this one. */}
                {failed && (() => {
                  const diag = endDiag;
                  const lines = stoppedByUser
                    ? []
                    : runFailureLog(logs, runStartedAtRef.current);
                  return (
                  <div className={`flex items-start gap-2 border px-3 py-2 ${
                    stoppedByUser ? "border-line bg-line2/20" : "border-bad/50 bg-bad/5"}`}>
                    <Icon name={state === "error" ? "x" : "stop"} size={16}
                      className={`mt-0.5 shrink-0 ${stoppedByUser ? "text-dim" : "text-bad"}`} />
                    <div className="min-w-0">
                      <p className="text-sm text-ink">
                        {stoppedByUser
                          ? diag.title
                          : state === "error" ? "Sequence failed" : "Sequence aborted"}
                      </p>
                      <p className="text-xs text-ink/85 mt-0.5">
                        {diag.cause}{diag.fix ? ` ${diag.fix}` : ""}
                      </p>
                      {diag.topic && (
                        <button type="button" onClick={() => openHelp(diag.topic!)}
                          className="text-[11px] text-accent hover:underline mt-1">How to fix →</button>
                      )}
                      {/* this run's error/warning log lines, inline */}
                      {lines.length > 0 && (
                        <div className="mt-1.5 flex flex-col gap-0.5">
                          {lines.map((l, i) => (
                            <p key={i} className="text-[10px] mono text-dim break-words leading-snug">
                              {l.data.message}
                            </p>
                          ))}
                        </div>
                      )}
                      <button className="btn !py-1 mt-2 min-h-[44px]" onClick={() => setView("sequence")}>
                        Plan →
                      </button>
                    </div>
                  </div>
                  );
                })()}

                {progress && (
                  <>
                    {/* main progress bar */}
                    <div className="progress-track">
                      <div className="progress-fill" style={{ width: `${progress.percent}%` }} />
                    </div>

                    {/* sub-frame bar — client-interpolated; freezes+greys on stall.
                        Kept up through PAUSING: the frame the pause interrupted is
                        still being taken, and this bar is the only thing on the
                        screen that shows it running down. */}
                    {(running || pausing) && curExp > 0 && progress.frame_started_at_ms != null && (
                      <SubFrameBar
                        startedAtMs={progress.frame_started_at_ms}
                        serverNowMs={progress.server_now_ms}
                        exposureS={curExp}
                        stalled={stallSoft}
                        reducedMotion={reducedMotion}
                        joinedMidFrame={joinedMidFrame}
                      />
                    )}

                    <div className="flex items-end justify-between gap-2 flex-wrap">
                      <span className="mono text-lg tabular-nums text-ink">
                        {progress.frames_done}/{progress.frames_total}
                        <span className="text-dim text-sm ml-2">{progress.percent}%</span>
                      </span>
                      <span className="text-xs text-dim mono">
                        {/* Same frozen snapshot as the sub-frame bar: elapsed_s is
                            stamped at the frame boundary, so a 5-minute sub read
                            "0s elapsed" for five minutes. Advance it from the
                            instant this snapshot arrived. NOT while paused —
                            elapsed_s excludes paused time, so the stored value is
                            already the right one to show there. */}
                        {running && joinedMidFrame && "≥"}
                        {fmtDuration(
                          running
                            ? progress.elapsed_s
                              + Math.max(0, now - etaAnchorRef.current.receivedAtMs) / 1000
                            : progress.elapsed_s,
                        )} elapsed
                      </span>
                    </div>

                    {seq.detail && !failed && <p className="text-xs text-dim">{seq.detail}</p>}

                    {/* stall line — non-motion liveness channel (resolves A5/B9).
                        Reads the RIG's frame counter, not preview arrival (#206),
                        so "last frame" now means the frame, not its picture. */}
                    {captureAgeS != null && (
                      <p
                        className={`text-xs mono ${
                          stallHard ? "text-bad font-semibold" : stallSoft ? "text-warn" : "text-dim"
                        }`}
                      >
                        {stallHard && "CAPTURE STALLED? "}
                        last frame {fmtDuration(captureAgeS)} ago
                      </p>
                    )}

                    {/* flagged micro-label (resolves F1) — never implies data loss */}
                    {progress.rejected > 0 && (
                      <p className="text-xs text-dim">
                        {progress.rejected} flagged (HFR/cloud check — frames kept)
                      </p>
                    )}

                    {/* HFR-trend mini-sparkline (resolves B9) — dew early warning.
                        Reads the same generalized `liveRing` as the Live Trend
                        panel below (one ring, not two). */}
                    {liveHfr.length >= 3 && (
                      <div>
                        <span className="label !text-[9px]">HFR trend</span>
                        <Sparkline samples={liveHfr} mode="trend" />
                      </div>
                    )}
                  </>
                )}

                {finished && progress && (
                  <p className="text-sm text-good">
                    done · {progress.frames_done} frames
                    {progress.rejected > 0 ? ` · ${progress.rejected} flagged (kept)` : ""}
                  </p>
                )}
              </div>
            )}
          </Panel>
        )}

        {/* ================================================== COUNTDOWNS */}
        <Panel className="col-span-full sm:col-span-1 lg:col-span-4" title="Countdowns">
          <div className="data-dim flex flex-col gap-3">
            <MeridianCountdown reducedMotion={reducedMotion} runActive={runActive} />
            <CoolingCountdown detail={seq.detail} />
          </div>
        </Panel>

        {/* ================================================== THUMBNAIL */}
        <Panel className="col-span-full sm:col-span-2 lg:col-span-6" title="Last frame">
          <PreviewTile
            previewId={preview?.id ?? coldPreviewId}
            live={live}
            stale={!live && (preview != null || coldPreviewId != null)}
            hfr={preview?.hfr}
            stars={preview?.stars}
            meta={previewMeta}
            clip={previewClip}
            brightness={thumbBrightness}
            onBrightness={setBrightness}
            onOpen={openCapture}
            reducedMotion={reducedMotion}
          />
        </Panel>

        {/* ================================================== GUIDE */}
        <Panel className="col-span-full sm:col-span-1 lg:col-span-3" title="Guiding">
          <div className="data-dim flex flex-col gap-2">
            {guideRecent.length > 0 ? (
              <>
                <Sparkline samples={guideRecent} mode="guide" />
                <div className="flex items-center gap-3 text-[10px] text-dim">
                  <span className="inline-flex items-center gap-1">
                    <span className="inline-block w-4 h-px" style={{ background: "var(--accent)" }} /> RA
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <span
                      className="inline-block w-4 h-px"
                      style={{ background: "var(--text-dim)", borderTop: "1px dashed var(--text-dim)" }}
                    />{" "}
                    DEC
                  </span>
                  <span className="ml-auto">±{4}″ scale</span>
                </div>
                <RmsVerdict rms={guideRms?.rms_total} stale={guideStale} />
                {guideStale && <p className="text-xs text-warn mono">guider stale (frozen reading)</p>}
              </>
            ) : (
              <p className="text-dim text-xs py-6 text-center tracking-widest uppercase">not guiding</p>
            )}
          </div>
        </Panel>

        {/* ================================================== LIVE TREND
            (report viewer spec §3 Task 5) — gated the same idiom as the inline
            HFR sparkline above: run active + ring has >=3 samples. Reads the
            SAME `liveRing` as that sparkline (one ring, not two). */}
        {runActive && liveRing.current.length >= 3 && (
          <Panel className="col-span-full sm:col-span-1 lg:col-span-3" title="Live trend">
            <LiveTrendStrip ring={liveRing.current} />
          </Panel>
        )}

        {/* ==================================== SKY CONDITIONS (weather spec §10) */}
        {canSeeWeather && <SkyConditionsPanel />}

        {/* ========================================= RADAR MAP (weather spec §11) */}
        {canSeeWeather && weather?.enabled && <RadarMap />}

        {/* ================================================== THERMAL */}
        <Panel className="col-span-full sm:col-span-1 lg:col-span-3" title="Thermal">
          <div className="data-dim flex flex-col gap-3">
            {camera?.can_cool === false || camera == null ? (
              <p className="text-dim text-xs py-4 text-center tracking-widest uppercase">no cooler</p>
            ) : (
              <>
                <MetricStrip>
                  <Stat
                    label="sensor"
                    value={camera.temperature != null ? camera.temperature.toFixed(1) : null}
                    unit="°C"
                    tone={
                      camera.temperature != null && camera.temperature < 0
                        ? "good"
                        : camera.cooler?.on && !camera.cooler.at_target
                          ? "warn"
                          : undefined
                    }
                  />
                  <Stat
                    label="target"
                    value={camera.cooler?.target_c != null ? camera.cooler.target_c : null}
                    unit="°C"
                  />
                </MetricStrip>
                {camera.cooler ? (
                  <ThermometerBar
                    power={camera.cooler.power}
                    on={camera.cooler.on}
                    target={camera.cooler.target_c}
                    atTarget={camera.cooler.at_target}
                    canReportPower={camera.cooler.can_report_power}
                  />
                ) : (
                  <p className="text-xs text-dim">cooler idle</p>
                )}
                {camera.has_dew_heater && (
                  <button
                    className="text-xs text-dim hover:text-accent text-left inline-flex items-center gap-1 min-h-[44px]"
                    onClick={() => setView("capture")}
                  >
                    <Icon name="power" size={12} /> dew heater — control on Capture →
                  </button>
                )}
              </>
            )}
          </div>
        </Panel>

        {/* ================================================== SUB QUALITY
            (photometry/SNR design §3 Task 6) — live noise/read-fraction/sky-limited
            readout over usePreview + the photometry profile. Honest-disabled per
            §11.8 when the profile is empty (never a native-disabled button — a
            link to Capture where the profile lives). */}
        <Panel className="col-span-full sm:col-span-1 lg:col-span-3" title="Sub quality">
          <div className="data-dim flex flex-col gap-3">
            {!subQualityLinear ? (
              <p className="text-dim text-xs py-6 text-center tracking-widest uppercase">
                no linear frame
              </p>
            ) : !subQualityProfileFilled ? (
              <button
                className="tap min-h-[44px] text-xs text-dim hover:text-accent text-left inline-flex items-center gap-1.5"
                onClick={openCapture}
                title="Add camera gain + read noise (Capture → Camera photometry) to see sub SNR"
              >
                <Icon name="lock" size={12} />
                Add camera gain + read noise (Capture → Camera photometry) to see sub SNR →
              </button>
            ) : subQuality && (
              <>
                <MetricStrip>
                  <Stat label="total noise" value={subQuality.noise.totalNoiseE.toFixed(1)} unit="e-" />
                  <Stat
                    label="read fraction"
                    value={(subQuality.noise.readFraction * 100).toFixed(0)}
                    unit="%"
                    tone={subQuality.noise.readFraction > 0.2 ? "warn" : "good"}
                  />
                </MetricStrip>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-dim">sky-limited</span>
                  <span className="mono">
                    {subQuality.skyLimitedS != null ? `${Math.round(subQuality.skyLimitedS)}s` : "—"}
                    {" vs "}
                    {preview!.exposure_s}s actual
                  </span>
                </div>
                <p className={`text-[11px] ${
                  subQuality.verdict === "too_short" ? "text-warn"
                    : subQuality.verdict === "good" ? "text-good" : "text-dim"
                }`}>
                  {subQuality.verdict === "too_short" && "read-noise limited — subs shorter than the sky-limited length"}
                  {subQuality.verdict === "good" && "✓ sky-limited"}
                  {subQuality.verdict === "long" && "longer than needed for this sky"}
                  {subQuality.verdict === "unknown" && "—"}
                </p>
                {progress && progress.frames_done > 0 && (
                  <p className="text-[11px] text-dim">
                    to double stack SNR: +{moreSubsForSnrMultiple(progress.frames_done, 2)} subs
                  </p>
                )}
              </>
            )}
          </div>
        </Panel>
      </div>

      {/* below-horizon chip (surfaced, not enforced — §0 rejected/deferred) */}
      {mount && mount.alt < 0 && (
        <div className="mt-3 inline-flex items-center gap-2 border border-bad/60 bg-bad/5 px-3 py-1.5 text-xs text-bad">
          <Icon name="alert" size={14} /> BELOW HORIZON — mount at {mount.alt.toFixed(0)}°
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- pausing badge
/** The badge for the phase the wire has no state for: the run is committed to
 *  stopping, and the shutter is still open. Same typography as StateBadge — the
 *  header must not visibly change shape when the phase hands over to PAUSED —
 *  with the pause glyph and the warn tone, because "not stopped yet" is the
 *  whole message. */
function PausingBadge() {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs font-display font-semibold
        tracking-[0.2em] uppercase text-warn"
      role="status"
    >
      <Icon name="pause" size={15} />
      PAUSING
    </span>
  );
}

// ---------------------------------------------------------------- sub-frame bar
/** Client-interpolated in-flight exposure bar (resolves D19 — the engine only
 *  emits on frame boundaries). Freezes + greys when stalled (resolves A5). Own
 *  leaf ticker. */
function SubFrameBar({
  startedAtMs,
  serverNowMs,
  exposureS,
  stalled,
  reducedMotion,
  joinedMidFrame,
}: {
  startedAtMs: number;
  serverNowMs?: number;
  exposureS: number;
  stalled: boolean;
  reducedMotion?: boolean;
  /** True when this client did not witness the frame start, so the age below is
   *  a floor rather than a measurement (see MonitorView's anchor). */
  joinedMidFrame?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, []);
  // ONE anchor per frame — the bug this replaces (UX round-4, #3): the skew was
  // re-derived on EVERY tick as `Date.now() - serverNowMs`, and the engine emits
  // server_now_ms and frame_started_at_ms from the same instant and then never
  // republishes, so the subtraction cancelled exactly and the bar read 0.0 s for
  // the entire exposure — an empty bar and "frame 00:00 / 300s" for five
  // minutes, on the one indicator that answers "is this frame running or has it
  // stalled?". Anchor at the moment the snapshot lands (taking the server's own
  // reported age of the frame if it gives one), then interpolate on the client
  // clock, which is the only clock ticking between boundaries.
  const anchorRef = useRef<{ key: number; atMs: number; ageS: number } | null>(null);
  if (anchorRef.current?.key !== startedAtMs) {
    const reportedAgeS = serverNowMs != null ? (serverNowMs - startedAtMs) / 1000 : 0;
    anchorRef.current = {
      key: startedAtMs,
      atMs: Date.now(),
      ageS: Number.isFinite(reportedAgeS) ? Math.max(0, reportedAgeS) : 0,
    };
  }
  const anchor = anchorRef.current;
  const elapsed = stalled
    ? exposureS
    : Math.max(0, anchor.ageS + (now - anchor.atMs) / 1000);
  const frac = Math.max(0, Math.min(1, elapsed / exposureS));
  // We joined a frame already in flight and the server did not tell us how old
  // it was, so all we can honestly claim is "at least this long".
  const floorOnly = !stalled && !!joinedMidFrame && anchor.ageS < 0.5;
  return (
    <div className="flex flex-col gap-1">
      <div className="progress-track !h-2">
        <div
          className={`progress-fill ${stalled || reducedMotion ? "!transition-none" : ""}`}
          style={{
            width: `${frac * 100}%`,
            background: stalled ? "var(--text-dim)" : undefined,
          }}
        />
      </div>
      <span className={`mono text-[10px] tabular-nums ${stalled ? "text-warn" : "text-dim"}`}>
        frame {floorOnly ? "≥" : ""}{fmtCountdown(Math.min(elapsed, exposureS))} /{" "}
        {Math.round(exposureS)}s
        {floorOnly && " · started before this screen opened"}
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- meridian cell
/** Window after the crossing in which a flip really can still be in flight. */
const FLIP_IN_FLIGHT_S = 300;

function MeridianCountdown({
  reducedMotion,
  runActive,
}: {
  reducedMotion?: boolean;
  runActive?: boolean;
}) {
  const meridian = useMeridian();
  if (!meridian) {
    return (
      <CountdownTile
        label="meridian flip"
        seconds={null}
        warnAtS={300}
        variant="flip"
        tone="dim"
        sub="flip n/a"
      />
    );
  }
  const { status, hours_to_flip, pier_side } = meridian;
  if (status === "counting") {
    const secs = hours_to_flip != null ? Math.max(0, hours_to_flip * 3600) : null;
    return (
      <CountdownTile
        label="meridian flip"
        seconds={secs}
        warnAtS={300}
        totalS={4 * 3600}
        dueLabel="FLIP DUE"
        variant="flip"
        tone="accent"
        reducedMotion={reducedMotion}
        sub={secs != null && secs <= 300 ? "FLIP SOON" : undefined}
      />
    );
  }
  // UX-2026-07-26 #21. `status: "due"` is RAW HOUR-ANGLE GEOMETRY: it says the
  // meridian crossing is behind us (hours_to_flip <= 0), not that the sequencer
  // owes a flip. The engine arms `_flip_armed` only for a target ACQUIRED EAST
  // of the meridian and disarms it the moment it flips, so a negative countdown
  // means either "we already flipped" or "this target was acquired west and was
  // never armed" — nothing is owed either way. The old tile clamped the
  // countdown at zero, which drove CountdownTile's `due` branch, so a target
  // sitting west rendered a red blinking "⚠ FLIP DUE" for the whole ~12 h it
  // stayed there — a permanent alarm on the one indicator that must never cry
  // wolf (pro + designer, three runs, ~90 frames of log with no flip in them).
  //
  // Trade-off, stated: if a flip were genuinely armed and the mount then FAILED
  // to execute it, this tile would read calm. That failure is loud elsewhere
  // (the engine logs it and the run state moves), and a real alarm you can't
  // trust is worth less than no alarm at all.
  if (status === "due") {
    const agoS = hours_to_flip != null ? Math.abs(hours_to_flip) * 3600 : null;
    const inFlight = !!runActive && agoS != null && agoS <= FLIP_IN_FLIGHT_S;
    return (
      <div className="flex items-center gap-3 min-w-0">
        <Icon
          name={inFlight ? "alert" : "info"}
          size={22}
          className={`shrink-0 ${inFlight ? "text-warn" : "text-dim"}`}
        />
        <div className="leading-tight min-w-0">
          <div className="label !text-[10px]">meridian flip</div>
          <div className={`text-xs ${inFlight ? "text-warn font-semibold" : "text-dim"}`}>
            {inFlight ? "CROSSING NOW — flip if armed" : "no flip owed — meridian passed"}
          </div>
          {!inFlight && agoS != null && (
            <div className="label !text-[9px] text-dim truncate">
              {fmtDuration(agoS)} ago{pier_side !== "unknown" ? ` · pier ${pier_side}` : ""}
            </div>
          )}
        </div>
      </div>
    );
  }
  // non-counting variants: render the static label + word (no ring tick).
  // UX-2026-07-26 #21: `flip_disabled` is emitted whenever the ACTIVE PLAN
  // doesn't ask for a flip — and `hub._plan_flip_enabled()` returns False when
  // there is no plan at all, so an idle rig with nothing loaded rendered an
  // amber "FLIP DISABLED — risk near meridian" permanently. The risk it names is
  // real only while something is driving the mount across the meridian.
  if (status === "flip_disabled") {
    if (!runActive) {
      return (
        <div className="flex items-center gap-3">
          <Icon name="info" size={22} className="text-dim shrink-0" />
          <div className="leading-tight">
            <div className="label !text-[10px]">meridian flip</div>
            <div className="text-xs text-dim">no run — flip not scheduled</div>
          </div>
        </div>
      );
    }
    return (
      <div className="flex items-center gap-3">
        <Icon name="alert" size={22} className="text-warn shrink-0" />
        <div className="leading-tight">
          <div className="label !text-[10px]">meridian flip</div>
          <div className="text-xs text-warn font-semibold">FLIP DISABLED — risk near meridian</div>
        </div>
      </div>
    );
  }
  const word =
    status === "n_a_fork" ? "no flip needed (fork mount)" : "flip n/a (mount doesn't report)";
  return (
    <div className="flex items-center gap-3">
      <Icon name="info" size={22} className="text-dim shrink-0" />
      <div className="leading-tight">
        <div className="label !text-[10px]">meridian flip</div>
        <div className="text-xs text-dim">{word}</div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- cooling cell
function CoolingCountdown({ detail }: { detail?: string }) {
  const camera = useCamera();
  const cooler = camera?.cooler;
  const cooling = (detail ?? "").toLowerCase().startsWith("cooling");

  // hidden entirely when not cooling and not before-lights (no cooler context).
  if (!cooler && !cooling) return null;
  if (cooler?.at_target) {
    const t = cooler.target_c;
    return (
      <div className="flex items-center gap-2 text-good text-sm">
        <Icon name="check" size={16} />
        AT {t != null ? `${t > 0 ? "+" : ""}${t}°C` : "TARGET"}
      </div>
    );
  }
  if (cooling) {
    const cur = camera?.temperature;
    const tgt = cooler?.target_c;
    return (
      <div className="flex items-center gap-2 text-xs text-warn mono">
        {cur != null ? `${cur.toFixed(1)}°C` : "—"} → {tgt != null ? `${tgt}°C` : "—"} · cooling…
      </div>
    );
  }
  return null;
}

// ---------------------------------------------------------------- clip helper
/** CLIP only when the linear stats hit the driver's full_well (NOT a hardcoded
 *  65535 — a 12/14-bit-in-16-bit CMOS saturates well below full-scale, exactly
 *  what full_well exists for). `data_is_linear` is false on NINA (decoded 8-bit
 *  copy, unreliable for clip — D15), so the NINA guard is now subsumed. Gate
 *  matches PreviewStage/StretchHistogram/FrameStats. full_well comes from the
 *  preview event (FIX-A); null => clip mask disabled. */
function isClipping(p: PreviewInfo | null): boolean {
  if (!p) return false;
  const fw = p.full_well;
  return p.data_is_linear && fw != null && p.stats.max >= fw;
}
