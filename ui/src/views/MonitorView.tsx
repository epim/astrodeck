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
  useStore,
} from "../store";
import { Panel, Stat, EmptyState } from "../components/ui";
import { Icon } from "../components/icons";
import SkyConditionsPanel from "../components/weather/SkyConditionsPanel";
import RadarMap from "../components/weather/RadarMap";
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
import { humanizeSeqError } from "../lib/humanize";
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
  const etaAnchorRef = useRef<{ etaS?: number; receivedAtMs: number }>({ receivedAtMs: Date.now() });
  const lastEtaSentinel = useRef<number | null>(null);
  // Re-anchor when the server emits a fresh frame (server_now_ms changes) or eta.
  const sentinel = progress?.server_now_ms ?? progress?.frames_done ?? null;
  if (sentinel !== lastEtaSentinel.current) {
    lastEtaSentinel.current = sentinel;
    etaAnchorRef.current = { etaS: progress?.eta_s, receivedAtMs: Date.now() };
  }

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
      } catch {
        /* WS catches up within ~2s — non-fatal */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ----- derived liveness -----
  const frameAgeMs = liveness.frame != null ? now - liveness.frame : null;
  const guideAgeMs = liveness.guide != null ? now - liveness.guide : null;
  const frameAgeS = frameAgeMs != null ? frameAgeMs / 1000 : null;
  const live = frameAgeMs != null && frameAgeMs < LIVE_WINDOW_S * 1000;
  const guideStale = guideAgeMs != null && guideAgeMs > GUIDE_STALE_S * 1000;

  const state = seq.state;
  const running = state === "running";
  const paused = state === "paused";
  const runActive = running || paused;
  const finished = state === "complete";
  const failed = state === "aborted" || state === "error";
  const ninaNative = state === "nina_native";
  const idle = state === "idle";

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

  // ----- stall detection (resolves A5; gated to running-only — R3-MON-01) -----
  const curExp = progress?.current_exposure_s ?? 0;
  const stallLvl = stallLevel(state, frameAgeS, curExp);
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
  const sendControl = (label: string, path: string) => {
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
        toast({ level: "success", title: `${label} sent` });
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
  const healthIssues = useMemo(
    () =>
      deriveHealthIssues({
        safety,
        weather,
        disk: status?.disk,
        meridian: status?.meridian,
        ninaLink: status?.nina_link,
        backendLinks,
        bootConnectFailed,
        providers,
        seqState: state,
        endReason: seq.end_reason,
        wsConnected,
        telemetryStale,
      }),
    [safety, weather, status, backendLinks, bootConnectFailed, providers, state, seq.end_reason, wsConnected, telemetryStale],
  );

  // ====================================================================== render
  return (
    <div className="px-3 pb-20 sm:px-0 sm:pb-4">
      <div className="mb-3">
        <HealthStrip issues={healthIssues} />
      </div>

      <div
        className={`grid gap-3 grid-cols-1 sm:grid-cols-2 lg:grid-cols-12 auto-rows-min
          ${!wsConnected ? "[&_.data-dim]:opacity-60" : ""}`}
      >
        {/* ================================================== HEADER STRIP */}
        <header className="col-span-full sticky top-0 z-10 panel p-3 sm:p-4 backdrop-blur">
          <div className="flex items-start justify-between gap-3 flex-wrap">
            <div className="min-w-0 flex flex-col gap-1">
              <StateBadge state={state} reducedMotion={reducedMotion} />
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
              </div>
            </div>

            {/* Finish clock = largest header text (>=20px). Hidden unless a run. */}
            {(running || paused) && (
              <div className="data-dim">
                <LiveTimer
                  etaS={etaAnchorRef.current.etaS}
                  receivedAtMs={etaAnchorRef.current.receivedAtMs}
                  confident={progress?.eta_confident}
                  paused={paused}
                />
              </div>
            )}
          </div>

          {/* controls row — only when run-related (idle/complete/nina show none).
              Rendered for EVERY role in the same position; non-control.mount
              roles get the controls DISABLED plus one shared lock note whose
              copy derives from the enforced capability (R4B-MON-01/02). */}
          {runActive && (
            <>
              <div className="flex items-center gap-2 mt-3">
                <PauseButton
                  paused={paused}
                  disabled={!canRun}
                  onPause={() => sendControl("Pause", "/api/sequence/pause")}
                  onResume={() => sendControl("Resume", "/api/sequence/resume")}
                />
                <HoldButton
                  face="Abort"
                  label="Abort sequence"
                  danger
                  disabled={!canRun}
                  onConfirm={abort}
                  hint={!wsConnected && canRun ? "link down — sending anyway" : undefined}
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
                {/* failure renders inline (resolves E3) — no drawer punt */}
                {failed && (
                  <div className="flex items-start gap-2 border border-bad/50 bg-bad/5 px-3 py-2">
                    <Icon name={state === "error" ? "x" : "stop"} size={16} className="text-bad mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <p className="text-sm text-ink">
                        {state === "error" ? "Sequence failed" : "Sequence aborted"}
                      </p>
                      <p className="text-xs text-ink/85 mt-0.5">{humanizeSeqError(seq.detail)}</p>
                      {/* last error/warning log lines, inline */}
                      <div className="mt-1.5 flex flex-col gap-0.5">
                        {logs
                          .filter((l) => l.data.level === "error" || l.data.level === "warning")
                          .slice(-5)
                          .map((l, i) => (
                            <p key={i} className="text-[10px] mono text-dim break-words leading-snug">
                              {l.data.message}
                            </p>
                          ))}
                      </div>
                      <button className="btn !py-1 mt-2 min-h-[44px]" onClick={() => setView("sequence")}>
                        Plan →
                      </button>
                    </div>
                  </div>
                )}

                {progress && (
                  <>
                    {/* main progress bar */}
                    <div className="progress-track">
                      <div className="progress-fill" style={{ width: `${progress.percent}%` }} />
                    </div>

                    {/* sub-frame bar — client-interpolated; freezes+greys on stall */}
                    {running && curExp > 0 && progress.frame_started_at_ms != null && (
                      <SubFrameBar
                        startedAtMs={progress.frame_started_at_ms}
                        serverNowMs={progress.server_now_ms}
                        exposureS={curExp}
                        stalled={stallSoft}
                        reducedMotion={reducedMotion}
                      />
                    )}

                    <div className="flex items-end justify-between gap-2 flex-wrap">
                      <span className="mono text-lg tabular-nums text-ink">
                        {progress.frames_done}/{progress.frames_total}
                        <span className="text-dim text-sm ml-2">{progress.percent}%</span>
                      </span>
                      <span className="text-xs text-dim mono">
                        {fmtDuration(progress.elapsed_s)} elapsed
                      </span>
                    </div>

                    {seq.detail && !failed && <p className="text-xs text-dim">{seq.detail}</p>}

                    {/* stall line — non-motion liveness channel (resolves A5/B9) */}
                    {frameAgeS != null && (
                      <p
                        className={`text-xs mono ${
                          stallHard ? "text-bad font-semibold" : stallSoft ? "text-warn" : "text-dim"
                        }`}
                      >
                        {stallHard && "CAPTURE STALLED? "}
                        last frame {fmtDuration(frameAgeS)} ago
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
            <MeridianCountdown reducedMotion={reducedMotion} />
            <CoolingCountdown detail={seq.detail} />
          </div>
        </Panel>

        {/* ================================================== THUMBNAIL */}
        <Panel className="col-span-full sm:col-span-2 lg:col-span-6" title="Last frame">
          <PreviewTile
            previewId={preview?.id ?? null}
            live={live}
            stale={!live && preview != null}
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
}: {
  startedAtMs: number;
  serverNowMs?: number;
  exposureS: number;
  stalled: boolean;
  reducedMotion?: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, []);
  // Offset-correct the server epoch to the client clock via the emit instant.
  const skew = serverNowMs != null ? Date.now() - serverNowMs : 0;
  const elapsed = stalled ? exposureS : (now - (startedAtMs + skew)) / 1000;
  const frac = Math.max(0, Math.min(1, elapsed / exposureS));
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
        frame {fmtCountdown(Math.min(elapsed, exposureS))} / {Math.round(exposureS)}s
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- meridian cell
function MeridianCountdown({ reducedMotion }: { reducedMotion?: boolean }) {
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
  const { status, hours_to_flip } = meridian;
  if (status === "counting" || status === "due") {
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
  // non-counting variants: render the static label + word (no ring tick).
  if (status === "flip_disabled") {
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
