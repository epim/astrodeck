// LiveScreen.tsx - MONITOR - LIVE. The 3 a.m. glance.
//
// Read order is the order the questions get asked: is the night OK (health
// strip), is anything wrong right now (incident-shaped cards), is the camera
// still landing frames (stall strip), is the guiding holding (trace), and then
// the six numbers that decide whether to go back to bed.
//
// WHAT THIS SCREEN REUSES RATHER THAN REBUILDS, and why each one is a defect
// already paid for:
//   HealthStrip/deriveHealthIssues  the Tier-2/1/0 ladder, pure and DOM-free.
//   GuideGraph/GuideScatter/RmsVerdict  fixed +-4" scale, RA solid / DEC dashed,
//                                  and a `stale` that downgrades the verdict
//                                  regardless of the number.
//   PreviewTile                    the two-tier fetch: DISPLAY bytes first,
//                                  160 px thumb only on a 404. Reversing that
//                                  was a 4x-stretched thumbnail on every rig.
//   ThermometerBar                 degrades to LED + word when the camera
//                                  cannot report cooler power.
//   LiveTrendStrip                 four trends over ONE ring, not two.
//
// The weather panels are mounted by the CALLER's cap gate (they do no cap check
// of their own), and only at tablet/desktop: on a phone the WEATHER hub is one
// tab away and the design gives Monitor no weather block (plan §F.10).

import { useCallback, useEffect, useMemo, useRef, useState, type JSX } from "react";
import { u } from "../../../../lib/base";
import {
  useStore, useSeq, useStatus, useCamera, useMount, useGuideRecent, useGuideRms, useLiveness,
  usePreview, useLogs, useSafety, useNight, useWsConnected, useTelemetryStale, useWeather,
  useBackendLinks, useBootConnectFailed, useProviders, usePhotometry,
} from "../../../../store";
import { useCanViewWeather } from "../../../../lib/caps";
import { useLock } from "../../../lib/gateHook";
import {
  deriveHealthIssues, HealthStrip, LiveTrendStrip, PreviewTile, RmsVerdict, ThermometerBar,
  useReducedMotion,
} from "../../../../components/monitor";
import { GuideGraph, GuideScatter } from "../../../../components/graphs";
import { pushLiveSample, type LiveSample } from "../../../../lib/reportChart";
import {
  moreSubsForSnrMultiple, skyElectronsPerSub, skyLimitedSubSeconds, skyRateEPerSec,
  subLengthVerdict, subNoise,
} from "../../../../lib/photometry";
import { frameIsLive, GUIDE_STALE_S, THUMB_BRIGHTNESS_NIGHT_DEFAULT } from "../../../../lib/eta";
import { humanizeLog } from "../../../../lib/humanize";
import { diagnoseFailure } from "../../../../lib/troubleshoot";
import { fmtLogTime } from "../../../../lib/logFormat";
import { getDomeState, type DomeState } from "../../../../api/backends";
import SkyConditionsPanel from "../../../../components/weather/SkyConditionsPanel";
import RadarMap from "../../../../components/weather/RadarMap";
import type { MonitorSnapshot, PreviewInfo } from "../../../../types";
import { ActionButton, Card, Label, Mono, Pill, Segmented } from "../../../ui";
import { nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { useIncidents } from "../../../shell/useIncidents";
import { monState } from "../monState";
import { VitalsBand } from "./VitalsBand";
import { StallStrip, FailureCard } from "./StallStrip";
import { InterruptedRunCard, RunArmedCard, RunControls } from "./RecoveryCards";
import { LockControls } from "./LockControls";

const THUMB_KEY = "astrodeck-monitor-thumb-brightness";

function loadThumbBrightness(night: boolean): number {
  try {
    const raw = localStorage.getItem(THUMB_KEY);
    if (raw != null) {
      const n = Number(raw);
      if (Number.isFinite(n) && n >= 0.2 && n <= 1) return n;
    }
  } catch { /* unavailable */ }
  return night ? THUMB_BRIGHTNESS_NIGHT_DEFAULT : 1;
}

/** CLIP only when the linear stats hit the driver's `full_well` - NOT a
 *  hardcoded 65535, because a 12/14-bit-in-16-bit CMOS saturates well below
 *  full scale. (`MonitorView.tsx:1402-1406`.) */
function isClipping(p: PreviewInfo | null): boolean {
  if (!p) return false;
  const fw = p.full_well;
  return p.data_is_linear && fw != null && p.stats.max >= fw;
}

/** SUB QUALITY - is this exposure long enough for this sky?
 *
 *  Gated on `data_is_linear` AND a filled photometry profile, so a NINA frame
 *  (a decoded 8-bit copy, unmeasurable) and an inert profile each say WHICH of
 *  the two is missing rather than printing a number derived from neither. The
 *  arithmetic is `lib/photometry.ts`'s, the same core Capture's Suggest and the
 *  plan editor's advisory chips use - there is no second implementation. */
function SubQuality({ preview, framesDone }: {
  preview: PreviewInfo | null;
  framesDone: number;
}): JSX.Element {
  const profile = usePhotometry();
  const linear = !!preview && preview.data_is_linear;
  const filled = profile.egain > 0 && profile.readNoiseE > 0;

  let body: JSX.Element;
  if (!linear) {
    body = <Mono size={10.5} tone="dim">no linear frame - a decoded preview cannot be measured</Mono>;
  } else if (!filled) {
    body = (
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <Mono size={10.5} tone="dim">
          Sub SNR needs this camera&apos;s gain and read noise.
        </Mono>
        <ActionButton kind="secondary" onPress={() => nav.go("/rig/capture")}>
          ADD GAIN AND READ NOISE
        </ActionButton>
      </div>
    );
  } else {
    const p = preview as PreviewInfo;
    const skyE = skyElectronsPerSub(p.stats.median, profile.biasAdu, profile.egain);
    const noise = subNoise(skyE, profile.readNoiseE);
    const skyLimitedS = skyLimitedSubSeconds(profile.readNoiseE, skyRateEPerSec(skyE, p.exposure_s));
    const verdict = subLengthVerdict(p.exposure_s, skyLimitedS);
    body = (
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <Mono size={10.5}>
          {noise.totalNoiseE.toFixed(1)} e- total noise · {(noise.readFraction * 100).toFixed(0)}% of it read noise
        </Mono>
        <Mono size={10.5} tone="dim">
          sky-limited {skyLimitedS != null ? `${Math.round(skyLimitedS)}s` : "--"} vs {p.exposure_s}s actual
        </Mono>
        <Mono size={10.5} tone={verdict === "too_short" ? "warn" : verdict === "good" ? "good" : "dim"}>
          {verdict === "too_short" ? "read-noise limited - the subs are shorter than this sky needs"
            : verdict === "good" ? "sky-limited - this length is right for tonight"
              : verdict === "long" ? "longer than this sky needs"
                : "not enough signal to judge the length"}
        </Mono>
        {framesDone > 0 && (
          <Mono size={10} tone="dim">
            +{moreSubsForSnrMultiple(framesDone, 2)} subs would double the stack SNR
          </Mono>
        )}
      </div>
    );
  }

  return (
    <Card padding={12} data-testid="monitor-subquality">
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <Label>SUB QUALITY</Label>
        {body}
      </div>
    </Card>
  );
}

/** One coarse ticker for the derived liveness text ("last frame N ago", the
 *  countdowns). 1 s, not per-frame: everything it feeds is seconds-resolution. */
function useCoarseTick(): number {
  const [n, setN] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setN(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return n;
}

export function LiveScreen(): JSX.Element {
  const nowMs = useCoarseTick();
  const bp = useBreakpoint();
  const seq = useSeq();
  const status = useStatus();
  const camera = useCamera();
  const mount = useMount();
  const preview = usePreview();
  const liveness = useLiveness();
  const guideRecent = useGuideRecent();
  const guideRms = useGuideRms();
  const logs = useLogs();
  const safety = useSafety();
  const weather = useWeather();
  const night = useNight();
  const wsConnected = useWsConnected();
  const telemetryStale = useTelemetryStale();
  const backendLinks = useBackendLinks();
  const bootConnectFailed = useBootConnectFailed();
  const providers = useProviders();
  const canSeeWeather = useCanViewWeather();
  const reducedMotion = useReducedMotion();
  const incidents = useIncidents(nowMs);

  const state = seq.state;
  const runActive = state === "running" || state === "holding" || state === "paused"
    || state === "aborting";
  const progress = seq.progress;

  // ----- cold-load hydration (one shot, non-fatal; MonitorView.tsx:252-277).
  // Reopening the dashboard mid-run showed LAST FRAME as an empty black box
  // captioned NO FRAME YET with dozens of frames already on disk, because
  // `preview` only ever arrives over the WS on the NEXT frame. The snapshot
  // carries `preview_id`; hold it here and hand it to the tile as STALE - which
  // is the truth: it is the last frame, not a live one.
  const [coldPreviewId, setColdPreviewId] = useState<number | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch(u("/api/monitor/snapshot"));
        if (!res.ok) return;
        const snap = (await res.json()) as Partial<MonitorSnapshot>;
        if (cancelled) return;
        const ts = Date.now() / 1000;
        const h = useStore.getState().handleEvent;
        if (snap.status) h({ type: "status", data: snap.status as unknown as Record<string, unknown>, ts });
        if (snap.sequence) h({ type: "sequence", data: snap.sequence as unknown as Record<string, unknown>, ts });
        if (snap.preview_id != null) setColdPreviewId(snap.preview_id);
      } catch { /* offline - the WS fills this in on the next frame */ }
    })();
    return () => { cancelled = true; };
  }, []);

  // ----- the roof (UX-2026-07-26 #27). Polled at the same 15 s MonitorView
  // used; only rendered when a dome actually exists, so a rig without one never
  // grows a permanent "no roof" row.
  const [dome, setDome] = useState<DomeState | null>(null);
  useEffect(() => {
    let live = true;
    const tick = () => {
      getDomeState()
        .then((d) => { if (live) setDome(d); })
        .catch(() => { /* no dome route / offline - the row stays hidden */ });
    };
    tick();
    const id = window.setInterval(tick, 15000);
    return () => { live = false; window.clearInterval(id); };
  }, []);
  const roofAlarm = !!dome?.connected && runActive && (dome.shutter ?? "unknown") !== "open";

  // ----- one live ring, shared by the trend strip (MonitorView.tsx:225-241).
  const liveRing = useRef<LiveSample[]>([]);
  const lastSubId = useRef<number | null>(null);
  if (preview && preview.id !== lastSubId.current) {
    lastSubId.current = preview.id;
    liveRing.current = pushLiveSample(liveRing.current, {
      t: Date.now(),
      hfr: preview.hfr ?? null,
      stars: preview.stars ?? null,
      rms: guideRms?.rms_total ?? null,
      temp: camera?.temperature ?? null,
    }, 40);
  }

  // ----- run-start anchor for the failure card's log excerpt. There is no
  // run-start field on the wire, but `progress.elapsed_s` is server-computed,
  // so while the run is live the start instant is `now - elapsed`. FREEZE it at
  // the terminal state (elapsed stops advancing, so `now - elapsed` would drift
  // later and later after the fact). Null until a run is observed live: a page
  // opened after the run ended can attribute nothing, and quoting a DIFFERENT
  // run's lines inside a failure card invents an event that never happened.
  const runStartedAtRef = useRef<number | null>(null);
  if (state === "idle") runStartedAtRef.current = null;
  else if (runActive && progress) runStartedAtRef.current = Date.now() / 1000 - progress.elapsed_s;

  const [thumbBrightness, setThumb] = useState(() => loadThumbBrightness(night));
  const setBrightness = useCallback((v: number) => {
    setThumb(v);
    try { localStorage.setItem(THUMB_KEY, String(v)); } catch { /* unavailable */ }
  }, []);

  const frameAgeMs = liveness.frame != null ? nowMs - liveness.frame : null;
  const guideAgeMs = liveness.guide != null ? nowMs - liveness.guide : null;
  const guideStale = guideAgeMs != null && guideAgeMs > GUIDE_STALE_S * 1000;
  const live = frameIsLive(frameAgeMs, progress?.current_exposure_s);
  const previewMeta = useMemo(
    () => (preview ? `${preview.exposure_s}s · g${preview.gain} · ${preview.binning}x` : undefined),
    [preview],
  );

  // UX-2026-07-26 #23: the strip raises a red "Sequence aborted" for ANY
  // aborted state, so holding STOP lit the "is my night OK?" verdict red for
  // the one event that is not a problem - the operator's own decision. Feed the
  // helper only what applies; the abort is still stated by the failure card.
  const stoppedByUser = state === "aborted"
    && !!diagnoseFailure(seq.detail, { state }).userInitiated;

  const healthIssues = useMemo(
    () => deriveHealthIssues({
      safety,
      weather,
      disk: status?.disk,
      // The meridian block is a property of a RUN: with no plan loaded the hub
      // reports `flip_disabled` simply because there is no plan to enable it
      // on, and the strip then shouted "Meridian flip disabled" at an idle rig
      // forever. Feed it only while a run is in flight.
      meridian: runActive ? status?.meridian : null,
      ninaLink: status?.nina_link,
      backendLinks,
      bootConnectFailed,
      providers,
      seqState: stoppedByUser ? undefined : state,
      endReason: stoppedByUser ? undefined : seq.end_reason,
      wsConnected,
      telemetryStale,
    }),
    [safety, weather, status, backendLinks, bootConnectFailed, providers, state, seq.end_reason,
      wsConnected, telemetryStale, runActive, stoppedByUser],
  );
  // Mirrors the server's own gate (weather.py tick: `enabled and not
  // site.is_default`) - an enabled monitor on a default (0,0) site fetches
  // nothing, so it is just as unwatched.
  const weatherCfgEnabled = useStore((s) => s.config?.weather?.enabled);
  const siteIsDefault = useStore((s) => s.config?.site?.is_default);
  const weatherMonitored = !!weatherCfgEnabled && !siteIsDefault;

  const [trace, setTrace] = useState<"trace" | "scatter">("trace");
  const guideLock = useLock({ needsRole: "guider" });
  const recentLog = useMemo(() => [...logs].reverse().slice(0, 6), [logs]);

  return (
    <div data-testid="monitor-live" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {/* ------------------------------------------------------------- title */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
          <Label size={11}>MONITOR</Label>
          <Mono size={10} tone="dim">
            {monState({
              state,
              target: seq.target,
              incidentTitle: incidents.length > 0 ? incidents[0].title : null,
            })}
          </Mono>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
          <LockControls />
          <ActionButton
            kind="secondary"
            onPress={() => nav.go("/session/gallery")}
            data-testid="monitor-gallery"
          >
            GALLERY &rsaquo;
          </ActionButton>
        </div>
      </div>

      {/* ------------------------------------------------------ health strip */}
      <div data-testid="monitor-health">
        <HealthStrip
          issues={healthIssues}
          weatherMonitored={weatherMonitored}
          onEnableWeather={canSeeWeather ? () => nav.sheet("weatherSettings") : undefined}
        />
      </div>

      {roofAlarm && (
        <div role="alert" data-testid="monitor-roof">
          <Card tone="accent" padding={12}>
            <Mono size={11} tone="bad">
              The roof is not open and a run is live - the shutter reads {dome?.shutter ?? "unknown"}.
            </Mono>
          </Card>
        </div>
      )}

      {mount && mount.alt < 0 && (
        <Pill tone="warn" data-testid="monitor-below-horizon">
          the mount is pointing below the horizon
        </Pill>
      )}

      {/* --------------------------------------------- run state and controls */}
      <RunArmedCard />
      <InterruptedRunCard />
      <FailureCard runStartedAtS={runStartedAtRef.current} />
      <RunControls />
      <StallStrip nowMs={nowMs} />

      {/* --------------------------------------------------------- guiding */}
      <Card padding={12} data-testid="monitor-guiding">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <Label>GUIDING · RMS &Prime;</Label>
            <Mono size={11}>
              {guideRms?.rms_total != null ? `${guideRms.rms_total.toFixed(2)}" total` : "no RMS yet"}
            </Mono>
          </div>
          <Segmented
            label="Guiding view"
            options={[{ value: "trace", label: "TRACE" }, { value: "scatter", label: "SCATTER" }]}
            value={trace}
            onChange={setTrace}
            data-testid="guide-view"
          />
          {guideRecent.length >= 3 ? (
            trace === "trace"
              ? <GuideGraph samples={guideRecent} />
              : <div style={{ display: "flex", justifyContent: "center" }}>
                  <GuideScatter samples={guideRecent} />
                </div>
          ) : (
            <Mono size={10.5} tone="dim" data-testid="guide-empty">
              {guideLock.lockedReason ?? "no guide samples yet"}
            </Mono>
          )}
          <RmsVerdict rms={guideRms?.rms_total} stale={guideStale} />
        </div>
      </Card>

      {/* ---------------------------------------------------------- vitals */}
      <VitalsBand runActive={runActive} nowMs={nowMs} />

      {/* --------------------------------------------------------- thermal */}
      {camera?.can_cool !== false && camera?.cooler && (
        <Card padding={12} data-testid="monitor-thermal">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <Label>THERMAL</Label>
            <ThermometerBar
              power={camera.cooler.power}
              on={camera.cooler.on}
              target={camera.cooler.target_c}
              atTarget={camera.cooler.at_target}
              canReportPower={camera.cooler.can_report_power}
            />
          </div>
        </Card>
      )}

      {/* ------------------------------------------------------- last frame */}
      <Card padding={12} data-testid="monitor-lastframe">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Label>LAST FRAME</Label>
          <PreviewTile
            previewId={preview?.id ?? coldPreviewId}
            live={live}
            stale={!live && (preview != null || coldPreviewId != null)}
            ageMs={frameAgeMs}
            hfr={preview?.hfr}
            stars={preview?.stars}
            meta={previewMeta}
            clip={isClipping(preview)}
            brightness={thumbBrightness}
            onBrightness={setBrightness}
            onOpen={() => nav.go("/rig/capture")}
            reducedMotion={reducedMotion}
          />
        </div>
      </Card>

      <SubQuality preview={preview} framesDone={progress?.frames_done ?? 0} />

      {runActive && liveRing.current.length >= 3 && (
        <Card padding={12} data-testid="monitor-trends">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <Label>LIVE TREND</Label>
            <LiveTrendStrip ring={liveRing.current} />
          </div>
        </Card>
      )}

      {/* ------------------------------------------------------ log preview */}
      <Card padding={12} data-testid="monitor-logpreview">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
            <Label>LOG</Label>
            <ActionButton kind="ghost" onPress={() => nav.go("/monitor/log")} data-testid="monitor-open-log">
              FULL LOG &rsaquo;
            </ActionButton>
          </div>
          {recentLog.length === 0 && <Mono size={10.5} tone="dim">no events yet tonight</Mono>}
          {recentLog.map((l, i) => (
            <div key={`${l.ts}-${i}`} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
              <Mono size={10.5} tone="dim">{fmtLogTime(l.ts)}</Mono>
              <span style={{
                fontFamily: '"IBM Plex Mono", monospace', fontSize: 10.5, lineHeight: 1.5,
                color: l.data.level === "error" ? "var(--bad)"
                  : l.data.level === "warning" ? "var(--warn)" : "var(--text)",
                minWidth: 0, overflowWrap: "anywhere",
              }}>
                {humanizeLog(l.data)}
              </span>
            </div>
          ))}
        </div>
      </Card>

      {/* --------------------------------------------------------- weather */}
      {bp !== "phone" && canSeeWeather && (
        <div data-testid="monitor-weather" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <SkyConditionsPanel />
          <RadarMap />
        </div>
      )}
    </div>
  );
}
