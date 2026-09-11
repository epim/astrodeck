// guider.tsx - the GUIDER device sheet (plan hub-rig.md B.7; design fragment
// `<s>/seams/proto/device-guider.html`; GAP-ANALYSIS section 5).
//
// WHAT THIS FILE OWNS AND WHAT IT BORROWS. The guiding logic is not re-written
// here. Five existing pieces are mounted as they are, because each one carries
// behaviour that took a recorded regression to get right:
//
//   GuideQuickBar        the state dot, the calibration-walk plot (an orthogonal
//                        L is a mount; a smeared diagonal is flexure or a wrong
//                        parity), the per-pulse ring, and the guide-camera
//                        Exposure/Gain/Binning/Offset dials - including the
//                        server-side refusal to change binning while guiding is
//                        live, in the reason's own words.
//   GuideFramePreview    its own 2500 ms poll, bidirectional recovery, and the
//                        last-good <img> kept mounted across cache-busted swaps.
//   GuideProviderControl the layer-aware write target (a profile pin is edited
//                        IN the profile, never in the global block it shadows -
//                        the #132 fix), the per-option server reason, and the
//                        resolver's own sentence.
//   GuideGraph/Scatter   the RA/Dec trace and the last-60 scatter.
//   lib/guideAssistant   summarize / formatRecommendations / toggleRecommendationKey
//                        / buildApplyBody / applyChangesAlgorithm / applyActionReason.
//
// WHAT THE SHEET ITSELF DECIDES, and why each one is here rather than inherited:
//
//   1. EVERY WRITE TO /api/guide/settings IS A WHOLE-BLOCK REPLACE. The route
//      takes a `GuideConfig` pydantic model (server/astrodeck/api/app.py:6343)
//      and `ConfigStore.set_guide` persists it wholesale, so a PUT carrying only
//      the six tuning fields resets `dither_pixels`, `recover_guiding`,
//      `exposure_s`, `gain`, `binning`, `offset` and
//      `recalibrate_after_pier_change` to their model defaults. This sheet
//      therefore GETs the block first, spreads it, and patches one field - the
//      plan's section 0.4 rule. Nothing here writes before that GET has landed;
//      `saveReason` says so on every control that would.
//
//   2. `startingUp` READS THE BUSY LANE, and `phase` only names the step. See
//      `lib/guiderModel.ts`'s header for the session-long lockout that trusting
//      the phase hint produced.
//
//   3. STOP GUIDING IS NEVER GATED ON THE `guide` LANE. It is the escape hatch
//      for the lane it ends (plan section C's never-blocked list). The same goes
//      for the assistant's STOP.
//
//   4. THE PREVIEW'S 4-STATE NARRATION IS RENDERED HERE. `guidePreviewLine` has
//      only ever existed inside `views/CaptureView.tsx` although all three mount
//      points need it; it is transcribed into `lib/guiderModel.ts` as
//      `previewNote` rather than moved (CaptureView is out of this task's
//      directory), and the duplication is named in the task report.
//
// Copy: hyphens, never em-dashes (ARCHITECTURE.md non-negotiable 5). The
// reason sentences are the shipped ones with that one substitution.

import { useEffect, useRef, useState, type JSX, type ReactNode } from "react";
import { api } from "../../../../api";
import {
  useStore, useStatus, useGuide, useProviders, useGuideRms, useGuideRecent,
  useGuideRmsByKind, useGuideAssistant, useFrameSettings, usePlan,
} from "../../../../store";
import type { CalibrationReport, GuideStats } from "../../../../types";
import { accessPhrase, resolveRoleConnected, useCan } from "../../../../lib/caps";
import { useBusy, useBusyLanes, useBusyOrPending } from "../../../../lib/useBusy";
import { guideNarration } from "../../../../lib/guideNarration";
import { selectGuideWindows } from "../../../../lib/guideRms";
import { compareRmsWindows } from "../../../../lib/rmsCompare";
import {
  RA_GUIDE_ALGORITHMS, DEC_GUIDE_ALGORITHMS, DEC_GUIDE_MODES,
  GUIDE_ALGORITHM_DEFAULTS, defaultGuideSettings, validateGuideSettings,
  isValidRaAlgorithm, isValidDecAlgorithm, isValidDecGuideMode, toSnake,
  type GuideAlgorithmKind, type GuideAlgorithmParamDefaults, type GuideSettings,
  type DecGuideMode,
} from "../../../../lib/guideSettings";
import {
  summarize, formatRecommendations, toggleRecommendationKey, buildApplyBody,
  applyChangesAlgorithm, applyActionReason, backlashResultSentence,
  EMPTY_SELECTION_REASON,
  type AssistantReport, type GuideSettingsPutBody,
} from "../../../../lib/guideAssistant";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { getGuideOffset, startGuideOffsetMeasure,
  type GuideOffsetMeasurement } from "../../../../api/align";
import { GuideGraph, GuideScatter } from "../../../../components/graphs";
import GuideQuickBar from "../../../../components/GuideQuickBar";
import GuideFramePreview from "../../../../components/GuideFramePreview";
import GuideProviderControl from "../../../../components/GuideProviderControl";
import {
  Sheet, Card, Label, ActionButton, ReadoutGrid, ReadoutTile, Dial, Stepper2,
  Switch, SubNav, EmptyCard, ListRow, Bar, Divider,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { useBreakpoint } from "../../../breakpoint";
import {
  startExtra, stopExtra, calibrateExtra, ditherExtra, liveLine,
  calibrationLine, calibrationTone, savedCalClearedNote, previewNote,
  exposureSub, starValue, BOTH_AXES_NOTE, MIN_MOVE_UNIT,
} from "../lib/guiderModel";

// ------------------------------------------------------------------ constants

const EXPOSURE_STOPS = [1, 1.5, 2, 3, 4];
const AGGRESSION_STOPS = [40, 55, 70, 85, 100];
const MIN_MOVE_STOPS = [0.1, 0.15, 0.2, 0.3];

/** The design's own footer note (device-guider.html), which is also the answer
 *  to "why did guiding start on its own": flows own the guider. */
const FOOTER_NOTE =
  "Flows start and stop guiding themselves; these controls are for checking the "
  + "guide star between targets. Recalibration is only needed after you move the "
  + "guide camera.";

/** Why the numbers are pixels. The fix lives in the Optics sheet, which is where
 *  the guide-scope focal length belongs (GAP-2 "Partial - imaging train"). */
const PIXELS_NOTE =
  "RMS is in guide-camera pixels, not arcsec - the guide scope's focal length is "
  + "not set. Set it in Optics and these numbers become arcsec.";

const OFFSET_NOTE =
  "Solves both cameras where the mount is pointing now and reports how far the "
  + "guide scope looks from the OTA. Nothing moves.";

const ALGO_CHANGE_NOTE =
  "This changes the guiding algorithm. Your saved calibration is kept, so guiding "
  + "still starts straight away - if it behaves oddly afterwards, clear the "
  + "calibration and let the mount re-learn its directions (about 2 minutes).";

/** A one-off note. `nx-sheet-sub` ellipsises on one line, which is right for the
 *  header and wrong for a paragraph, so the sheet's notes carry the plan's own
 *  footer-note type (11.5 px, `--text-faint`, line-height 1.5). */
function Note({ children, tone = "dim", ...rest }: {
  children: ReactNode;
  tone?: "dim" | "warn" | "bad" | "good";
  "data-testid"?: string;
}): JSX.Element {
  const color = tone === "warn" ? "var(--warn)"
    : tone === "bad" ? "var(--bad)"
      : tone === "good" ? "var(--good)" : "var(--text-faint)";
  return (
    <p style={{ fontSize: "11.5px", lineHeight: 1.5, color, padding: "0 2px", margin: 0 }}
      data-testid={rest["data-testid"]}>{children}</p>
  );
}

function Row({ children, gap = 8 }: { children: ReactNode; gap?: number }): JSX.Element {
  return <div style={{ display: "flex", gap: `${gap}px`, flexWrap: "wrap", alignItems: "center" }}>{children}</div>;
}

function Stack({ children, gap = 8 }: { children: ReactNode; gap?: number }): JSX.Element {
  return <div style={{ display: "flex", flexDirection: "column", gap: `${gap}px` }}>{children}</div>;
}

const nearest = (stops: number[], v: number): number =>
  stops.reduce((best, s) => (Math.abs(s - v) < Math.abs(best - v) ? s : best), stops[0]);

// ------------------------------------------------------- the guide config I/O

/** snake_case -> camelCase for ONE param key: the inverse of `toSnake`, applied
 *  at the READ boundary only (client state stays camelCase throughout). */
const toCamelKey = (k: string) => k.replace(/_([a-z])/g, (_m, c: string) => c.toUpperCase());

/** The algorithm's dossier section 15 defaults with the axis's PERSISTED
 *  overrides laid on top. Server-side every param is optional-null and null
 *  means "not pinned - the engine's own default applies", so a null must leave
 *  the default it stands for alone rather than blanking the field
 *  (GuideView.tsx:565-585). */
function withSavedParams(
  kind: GuideAlgorithmKind,
  saved?: Record<string, number | null> | null,
): GuideAlgorithmParamDefaults {
  const out: GuideAlgorithmParamDefaults = { ...GUIDE_ALGORITHM_DEFAULTS[kind] };
  for (const [k, v] of Object.entries(saved ?? {})) {
    if (typeof v !== "number" || !Number.isFinite(v)) continue;
    const key = toCamelKey(k);
    if (key in out) out[key] = v;
  }
  return out;
}

type RawGuideConfig = Record<string, unknown>;

function draftFromRaw(raw: RawGuideConfig): GuideSettings {
  const d = defaultGuideSettings();
  const ra = typeof raw.ra_algorithm === "string" && isValidRaAlgorithm(raw.ra_algorithm)
    ? raw.ra_algorithm : d.ra.algorithm;
  const dec = typeof raw.dec_algorithm === "string" && isValidDecAlgorithm(raw.dec_algorithm)
    ? raw.dec_algorithm : d.dec.algorithm;
  const mode = typeof raw.dec_guide_mode === "string" && isValidDecGuideMode(raw.dec_guide_mode)
    ? (raw.dec_guide_mode as DecGuideMode) : d.decGuideMode;
  return {
    ra: { algorithm: ra, params: withSavedParams(ra, raw.ra_params as Record<string, number | null> | null) },
    dec: { algorithm: dec, params: withSavedParams(dec, raw.dec_params as Record<string, number | null> | null) },
    decGuideMode: mode,
    blcPulseMs: typeof raw.blc_pulse_ms === "number" ? raw.blc_pulse_ms : d.blcPulseMs,
  };
}

/** The GET/PUT pair for `/api/guide/settings`, with the whole-block replace rule
 *  in one place. `raw` is the LAST BLOCK THE SERVER SENT, kept verbatim, so a
 *  field this UI does not render (`recover_guiding`, `recalibrate_after_pier_
 *  change`, the guide-camera dials the quick bar owns) survives every write from
 *  this sheet instead of snapping back to a model default. */
function useGuideConfig(connected: boolean, canRead: boolean): {
  raw: RawGuideConfig | null;
  loadError: string | null;
  saving: boolean;
  reload: () => void;
  put: (patch: RawGuideConfig) => Promise<void>;
} {
  const [raw, setRaw] = useState<RawGuideConfig | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!connected) return;
    // GET /api/guide/settings needs control.guide (app.py `guide_settings_get`)
    // - the same cap the tuning editor's own SAVE is gated on. A viewer never
    // holds it, so the request would always come back 403; render the SAME
    // "could not read the saved tuning" honesty the sheet already has for a
    // real failure, without spending the request.
    if (!canRead) {
      setRaw(null);
      setLoadError(`needs ${accessPhrase("control.guide")}`);
      return;
    }
    let cancelled = false;
    api.get<RawGuideConfig>("/api/guide/settings")
      .then((r) => { if (!cancelled) { setRaw(r); setLoadError(null); } })
      .catch((e) => { if (!cancelled) setLoadError((e as Error).message); });
    return () => { cancelled = true; };
  }, [connected, canRead, tick]);

  const put = async (patch: RawGuideConfig) => {
    if (!raw) throw new Error("the saved guide settings have not been read yet");
    setSaving(true);
    try {
      const body = { ...raw, ...patch };
      const next = await api.put<RawGuideConfig>("/api/guide/settings", body);
      setRaw(next && typeof next === "object" ? next : body);
    } finally {
      setSaving(false);
    }
  };

  return { raw, loadError, saving, reload: () => setTick((t) => t + 1), put };
}

// ==================================================================== the sheet

export function GuiderSheet(): JSX.Element {
  const status = useStatus();
  const guide = useGuide();
  const providers = useProviders();
  const stats: GuideStats | null = useGuideRms();
  const recent = useGuideRecent();
  const plan = usePlan();
  const guideCam = useFrameSettings("guide");
  const setFrameSettings = useStore((s) => s.setFrameSettings);
  const showToast = useStore((s) => s.showToast);
  const bp = useBreakpoint();
  const wide = bp !== "phone";

  // "connected" for the NARRATION is the guider object's presence, exactly as
  // GuideView reads it: a bridge guider publishes stats without a backend link.
  const connected = !!status?.guider || !!guide;
  const equipConnected = useStore((s) => s.equipConnected);
  // "connected" for the GATE is the role, which is what `useLock` blocks on -
  // so the empty card and the lock reasons can never disagree.
  const role = resolveRoleConnected(
    "guider", status?.backend_links, status?.connected, equipConnected,
  );

  const isArcsec = stats?.is_arcsec === true;
  const unit = isArcsec ? "″" : "px";

  const narration = guideNarration({
    connected,
    phase: stats?.phase,
    guiding: !!stats?.guiding,
    rmsTotal: stats?.rms_total ?? 0,
    isArcsec,
    imageScale: stats?.image_scale ?? 0,
    hasSamples: recent.length > 0,
  });

  // ---------------------------------------------------------------- lane truth
  const { busy: guideLaneBusy, arm: armGuideLane } = useBusyOrPending("guide");
  const guideLaneLive = useBusy("guide");
  const lanes = useBusyLanes();
  const phase = stats?.phase ?? "";
  // The lane is the authority; `phase` is a fallback ONLY for a server too old
  // to publish `busy_lanes` (undefined in exactly that case - an idle rig
  // publishes []). See lib/guiderModel.ts's header.
  const startingUp = guideLaneBusy || (lanes === undefined && phase === "calibrating");

  const [acting, setActing] = useState(false);
  const act = async (fn: () => Promise<unknown>) => {
    if (acting) return;
    setActing(true);
    try { await fn(); } catch (e) { showToast("error", (e as Error).message, { verbatim: true }); }
    finally { setActing(false); }
  };

  const actionInput = {
    guiding: !!stats?.guiding, startingUp, laneLive: guideLaneLive, phase, acting,
  };

  // ------------------------------------------------------------ guide settings
  const canReadGuideSettings = useCan("control.guide");
  const cfg = useGuideConfig(connected, canReadGuideSettings);
  const [draft, setDraft] = useState<GuideSettings>(() => defaultGuideSettings());
  useEffect(() => { if (cfg.raw) setDraft(draftFromRaw(cfg.raw)); }, [cfg.raw]);

  // "Open in tuning editor": the assistant hands its recommended body to the
  // editor below for hand-tuning before a save. NOTHING is written by the
  // hand-off itself - the editor's own SAVE is still the only thing that PUTs,
  // which is what makes it a different action from APPLY SELECTED.
  const [tuningSeed, setTuningSeed] = useState<GuideSettingsPutBody | null>(null);
  useEffect(() => {
    if (!tuningSeed) return;
    const camel = (p: Record<string, number>): GuideAlgorithmParamDefaults => {
      const out = { minMove: 0 } as GuideAlgorithmParamDefaults;
      for (const [k, v] of Object.entries(p)) out[toCamelKey(k)] = v;
      return out;
    };
    setDraft({
      ra: {
        algorithm: isValidRaAlgorithm(tuningSeed.ra_algorithm) ? tuningSeed.ra_algorithm : "hysteresis",
        params: camel(tuningSeed.ra_params),
      },
      dec: {
        algorithm: isValidDecAlgorithm(tuningSeed.dec_algorithm) ? tuningSeed.dec_algorithm : "resist_switch",
        params: camel(tuningSeed.dec_params),
      },
      decGuideMode: isValidDecGuideMode(tuningSeed.dec_guide_mode) ? tuningSeed.dec_guide_mode : "auto",
      blcPulseMs: tuningSeed.blc_pulse_ms,
    });
  }, [tuningSeed]);

  // Until the GET lands every tuning field is a FACTORY DEFAULT, not the rig's,
  // and a PUT would replace a tuned axis with it. Hold every write behind this.
  const saveReason = cfg.raw == null
    ? (cfg.loadError
      ? `Could not read the saved tuning (${cfg.loadError}) - saving now would replace it with factory defaults`
      : "Reading the saved tuning from the rig...")
    : cfg.saving ? "Saving the last change..." : null;

  const commit = async (next: GuideSettings) => {
    const v = validateGuideSettings(next);
    setDraft(v);
    try {
      await cfg.put({
        ra_algorithm: v.ra.algorithm,
        dec_algorithm: v.dec.algorithm,
        ra_params: toSnake(v.ra.params),
        dec_params: toSnake(v.dec.params),
        dec_guide_mode: v.decGuideMode,
        blc_pulse_ms: v.blcPulseMs,
      });
    } catch (e) {
      showToast("error", (e as Error).message, { verbatim: true });
      if (cfg.raw) setDraft(draftFromRaw(cfg.raw));
    }
  };

  /** One number, both axes. AGGRESSION and MIN MOVE are per-axis on the wire, so
   *  the single dial writes `ra_params` AND `dec_params` and says so (plan E15).
   *  Everything goes through `validateGuideSettings` + `toSnake` so the clamps
   *  are single-sourced. */
  const setBothAxes = (key: "aggression" | "minMove", value: number) =>
    commit({
      ...draft,
      ra: { ...draft.ra, params: { ...draft.ra.params, [key]: value } },
      dec: { ...draft.dec, params: { ...draft.dec.params, [key]: value } },
    });

  // ------------------------------------------------------------------- the tiles
  const [tile, setTile] = useState<"exposure" | "aggression" | "minmove">("exposure");

  const aggPct = Math.round((draft.ra.params.aggression ?? 0.7) * 100);
  const minMove = draft.ra.params.minMove ?? 0.2;

  // ---------------------------------------------------------------- calibration
  const [calReport, setCalReport] = useState<CalibrationReport | null>(null);
  const [calTick, setCalTick] = useState(0);
  const [savedCalCleared, setSavedCalCleared] = useState(false);
  const guidingNow = !!stats?.guiding;
  // `start_guiding` persists a calibration before it sets `_active`, so a guider
  // reporting `guiding` has just written a saved copy again - drop the line
  // rather than let it outlive the fact it states.
  useEffect(() => { if (guidingNow) setSavedCalCleared(false); }, [guidingNow]);
  useEffect(() => {
    if (!connected) { setCalReport(null); return; }
    let cancelled = false;
    api.get<{ report: CalibrationReport | null }>("/api/guide/calibration")
      .then((r) => { if (!cancelled) setCalReport(r.report); })
      .catch(() => { if (!cancelled) setCalReport(null); });
    return () => { cancelled = true; };
    // `guideLaneLive` covers RECALIBRATE, which clears the stored calibration at
    // the START of a walk that ends minutes later.
  }, [connected, guidingNow, guideLaneLive, calTick]);

  // ---------------------------------------------------------------------- locks
  const guideCap = { cap: "control.guide" as const, needsRole: "guider" };
  const start = useLock({ ...guideCap, extra: startExtra(actionInput) });
  const stop = useLock({ ...guideCap, extra: stopExtra(actionInput) });
  const recal = useLock({ ...guideCap, extra: calibrateExtra(actionInput) });
  const clearCal = useLock({ ...guideCap, extra: cfg.saving ? "Saving the last change..." : null });
  const ditherNow = useLock({ ...guideCap, busyLane: "dither", extra: ditherExtra(!!stats?.guiding) });
  const ditherPx = useLock({ ...guideCap, extra: saveReason });
  const tuning = useLock({ ...guideCap, extra: saveReason });
  const exposure = useLock({ ...guideCap });

  // ------------------------------------------------------------------- actions
  const startGuiding = () => act(async () => {
    await api.post("/api/guide/start");
    // The POST returns the moment the task is CREATED. Latch until the lane
    // shows up on a status frame (<= 2 s) so the button does not look pressable
    // again in between.
    armGuideLane();
  });
  const stopGuiding = () => act(() => api.post("/api/guide/stop"));
  const recalibrate = () => act(async () => {
    await api.post("/api/guide/calibrate");
    armGuideLane();
    showToast("info", "Recalibrating - the mount walks a fresh calibration, then guiding starts on its own");
  });
  const clearCalibration = () => act(async () => {
    const r = await api.del<{ cleared?: boolean }>("/api/guide/calibration");
    setCalTick((t) => t + 1);
    setSavedCalCleared(!!r?.cleared);
    showToast("info", r?.cleared
      ? "Cleared the saved calibration - the guider keeps the one it is holding until guiding stops"
      : "Nothing to clear - this profile has no saved calibration");
  });

  const ditherPixels = typeof cfg.raw?.dither_pixels === "number" ? cfg.raw.dither_pixels : 3;
  // UX-24's three optional settle overrides. BLANK means "the guider's own
  // default", which is not the same as 0 - so they are held as text and only
  // parsed at the press, and a field left alone is omitted from the body
  // entirely rather than sent as a zero the guider would obey.
  const [settlePixels, setSettlePixels] = useState("");
  const [settleTime, setSettleTime] = useState("");
  const [settleTimeout, setSettleTimeout] = useState("");
  const optNum = (v: string): number | undefined => {
    const n = Number(v);
    return v.trim() !== "" && Number.isFinite(n) ? n : undefined;
  };
  const dither = () => act(() => api.post("/api/guide/dither", {
    pixels: ditherPixels,
    settle_pixels: optNum(settlePixels),
    settle_time_s: optNum(settleTime),
    settle_timeout_s: optNum(settleTimeout),
  }));

  // ------------------------------------------------------------ the RMS compare
  const { native: nativeWindow, backend: bridgeWindow } = selectGuideWindows(useGuideRmsByKind());
  const cmp = compareRmsWindows(nativeWindow, bridgeWindow);

  const [tab, setTab] = useState<"trace" | "scatter">("trace");
  const preview = previewNote(
    status?.guide_camera?.preview_reason ?? "",
    status?.guide_camera?.preview_ok,
    status?.guide_camera?.preview_source ?? "the guide camera",
  );

  const providerLabel = providers?.guide?.label ?? "";
  const live = !!stats?.guiding || startingUp;

  return (
    <Sheet
      title={providerLabel ? `GUIDER · ${providerLabel.toUpperCase()}` : "GUIDER"}
      icon={<NxIcon name="guider" size={18} />}
      live={liveLine({ connected, phaseText: narration.phaseText, stats, unit })}
      backLabel="RIG"
      onBack={() => nav.back()}
      data-testid="rig-guider"
    >
      {!role.connected && (
        <EmptyCard
          title="NO GUIDER CONNECTED"
          hint="Assign a guider on ADD A DEVICE, then connect the rig. Everything below stays visible so you can see what it will do."
          action={
            <ActionButton kind="secondary" onPress={() => nav.go("/rig/devices/addDevice")}>
              GO TO ADD A DEVICE
            </ActionButton>
          }
          data-testid="guider-empty"
        />
      )}

      {/* Sticky glance + the guide camera's OWN speed dials. Renders null until
          a guider is connected, deliberately: the dials must be reachable BEFORE
          the first calibration, so it is gated on connection, not on a session. */}
      <GuideQuickBar />

      {/* ---------------------------------------------------- 1. the trace card */}
      <Card data-testid="guider-trace">
        <Stack gap={8}>
          <SubNav
            items={[{ id: "trace", label: "TRACE" }, { id: "scatter", label: "SCATTER" }]}
            value={tab}
            onChange={(id) => setTab(id === "scatter" ? "scatter" : "trace")}
            ariaLabel="Guide plot"
            data-testid="guider-tabs"
          />
          <div style={{ display: "flex", justifyContent: "space-between", gap: "8px", alignItems: "baseline" }}>
            <Label>RMS · LAST {recent.length} SAMPLES</Label>
            <span className="nx-mono" style={{ fontSize: "10.5px", color: "var(--text)" }}
              data-testid="guider-rms">
              {stats
                ? `${stats.rms_total.toFixed(2)}${unit} total · RA ${stats.rms_ra.toFixed(2)}${unit} · Dec ${stats.rms_dec.toFixed(2)}${unit}`
                : `no guide data yet`}
            </span>
          </div>
          {tab === "trace"
            ? <GuideGraph samples={recent} />
            : <div style={{ display: "flex", justifyContent: "center" }}>
              <GuideScatter samples={recent} />
            </div>}
          {narration.verdict && <Note tone={narration.tone === "neutral" ? "dim" : narration.tone}>{narration.verdict}</Note>}
          {stats && !isArcsec && (
            <>
              <Note tone="warn" data-testid="guider-pixels-note">{PIXELS_NOTE}</Note>
              <ActionButton kind="ghost" onPress={() => nav.go("/settings/general/optics")}>
                SET THE GUIDE SCOPE FL
              </ActionButton>
            </>
          )}
          {/* Same-night head-to-head. `comparable` (within 10%) never crowns a
              winner: real-sky RMS jitters run-to-run by more than that. */}
          <Note data-testid="guider-rms-compare">{cmp.message}</Note>
        </Stack>
      </Card>

      {/* ------------------------------------------- 2. readout tiles + the dial */}
      <ReadoutGrid data-testid="guider-tiles">
        <ReadoutTile
          label="EXPOSURE"
          value={`${guideCam.exposure_s} s`}
          sub={exposureSub(guideCam.exposure_s)}
          selected={tile === "exposure"}
          onSelect={() => setTile("exposure")}
          data-testid="tile-exposure"
        />
        <ReadoutTile
          label="AGGRESSION"
          value={`${aggPct}%`}
          sub="RA + Dec"
          selected={tile === "aggression"}
          onSelect={() => setTile("aggression")}
          data-testid="tile-aggression"
        />
        <ReadoutTile
          label="MIN MOVE"
          value={`${minMove.toFixed(2)} ${MIN_MOVE_UNIT}`}
          sub="dead band"
          selected={tile === "minmove"}
          onSelect={() => setTile("minmove")}
          data-testid="tile-minmove"
        />
        {/* Read-only: the engine has a number here and no verb. Only `snr` is on
            the stats bus, so the prototype's magnitude and size clauses are
            dropped rather than invented (plan E16). */}
        <ReadoutTile label="STAR" value={starValue(stats)} sub="guide star" data-testid="tile-star" />
      </ReadoutGrid>

      {tile === "exposure" && (
        <Dial
          label="EXPOSURE"
          options={EXPOSURE_STOPS.map((v) => ({ value: v, label: `${v} s` }))}
          value={nearest(EXPOSURE_STOPS, guideCam.exposure_s)}
          onChange={(v) => setFrameSettings("guide", { exposure_s: v })}
          lockedReason={exposure.lockedReason}
          onExplain={exposure.onExplain}
          data-testid="guider-dial"
        />
      )}
      {tile === "aggression" && (
        <>
          <Dial
            label="AGGRESSION"
            options={AGGRESSION_STOPS.map((v) => ({ value: v, label: `${v}%` }))}
            value={nearest(AGGRESSION_STOPS, aggPct)}
            onChange={(v) => void setBothAxes("aggression", v / 100)}
            lockedReason={tuning.lockedReason}
            onExplain={tuning.onExplain}
            data-testid="guider-dial"
          />
          <Note data-testid="guider-both-axes">{BOTH_AXES_NOTE}</Note>
        </>
      )}
      {tile === "minmove" && (
        <>
          <Dial
            label={`MIN MOVE (${MIN_MOVE_UNIT})`}
            options={MIN_MOVE_STOPS.map((v) => ({ value: v, label: v.toFixed(2) }))}
            value={nearest(MIN_MOVE_STOPS, minMove)}
            onChange={(v) => void setBothAxes("minMove", v)}
            lockedReason={tuning.lockedReason}
            onExplain={tuning.onExplain}
            data-testid="guider-dial"
          />
          <Note data-testid="guider-both-axes">{BOTH_AXES_NOTE}</Note>
        </>
      )}

      {/* ------------------------------------------- 4. the guide-camera preview */}
      <Card data-testid="guider-preview">
        <GuideFramePreview compact reticle />
        {preview && <Note tone={preview.tone}>{preview.text}</Note>}
      </Card>

      {/* ------------------------------------------------------------ 5. dither */}
      <Card data-testid="guider-dither">
        <Stack gap={8}>
          <Label>DITHER</Label>
          <Row>
            <Stepper2
              label="Dither pixels"
              value={ditherPixels}
              step={0.5}
              min={0}
              max={100}
              format={(v) => `${v} PX`}
              onChange={(v) => void act(() => cfg.put({ dither_pixels: v }))}
              lockedReason={ditherPx.lockedReason}
              onExplain={ditherPx.onExplain}
              data-testid="guider-dither-px"
            />
            <ActionButton
              kind="secondary"
              onPress={dither}
              lockedReason={ditherNow.lockedReason}
              onExplain={ditherNow.onExplain}
              data-testid="guider-dither-now"
            >DITHER NOW</ActionButton>
          </Row>
          <Note>0 turns dithering off.</Note>
          <Row>
            <SettleField label="SETTLE PX" placeholder="1.5" value={settlePixels}
              onChange={setSettlePixels} lockedReason={ditherNow.lockedReason} testId="guider-settle-px" />
            <SettleField label="SETTLE S" placeholder="8" value={settleTime}
              onChange={setSettleTime} lockedReason={ditherNow.lockedReason} testId="guider-settle-s" />
            <SettleField label="TIMEOUT S" placeholder="60" value={settleTimeout}
              onChange={setSettleTimeout} lockedReason={ditherNow.lockedReason} testId="guider-settle-timeout" />
          </Row>
          <Note>Leave the three settle fields empty to use the guider&rsquo;s own defaults.</Note>
          {/* The CADENCE is per-night (SequencePlan.dither_every) while the
              DISTANCE above is rig-level (config.guide.dither_pixels), so this
              row reads and does not write (plan E14). */}
          <ListRow
            title="EVERY N SUBS"
            sub={plan ? `every ${plan.dither_every} subs - set per night in the plan` : "no plan loaded"}
            right={<span className="nx-mono">{plan ? plan.dither_every : "--"}</span>}
            onPress={() => nav.go("/session/now/planEditor")}
            chevron
            data-testid="guider-dither-cadence"
          />
        </Stack>
      </Card>

      {/* ------------------------------------------------------- 6. calibration */}
      <Card data-testid="guider-calibration">
        <Stack gap={8}>
          <ListRow
            title="CALIBRATION"
            sub={calibrationLine(calReport)}
            right={
              <span className="nx-status-dot" data-tone={calibrationTone(calReport)} aria-hidden="true" />
            }
          />
          <Row>
            <ActionButton
              kind="secondary"
              onPress={recalibrate}
              lockedReason={recal.lockedReason}
              onExplain={recal.onExplain}
              data-testid="guider-recalibrate"
            >RECALIBRATE</ActionButton>
            <ActionButton
              kind="ghost"
              onPress={clearCalibration}
              lockedReason={clearCal.lockedReason}
              onExplain={clearCal.onExplain}
              data-testid="guider-clear-calibration"
            >CLEAR CALIBRATION</ActionButton>
          </Row>
          {calReport && (
            <Note data-testid="guider-cal-report">
              {`orthogonality ${calReport.ortho_error_deg.toFixed(1)}° · binning ${calReport.binning}x`}
              {calReport.declination_deg != null ? ` · dec ${calReport.declination_deg.toFixed(0)}°` : ""}
              {calReport.pier_side && calReport.pier_side !== "unknown" ? ` · pier ${calReport.pier_side}` : ""}
            </Note>
          )}
          {calReport?.advisories.map((a, i) => (
            <Note key={i} tone="warn">{a}</Note>
          ))}
          {savedCalCleared && (
            <Note tone="warn" data-testid="guider-saved-cleared">{savedCalClearedNote(!!stats?.guiding)}</Note>
          )}
        </Stack>
      </Card>

      {/* --------------------------------------------------- 7. primary action */}
      {live ? (
        <ActionButton
          kind="danger"
          size="lg"
          full
          onPress={stopGuiding}
          lockedReason={stop.lockedReason}
          onExplain={stop.onExplain}
          data-testid="guider-primary"
        >STOP GUIDING</ActionButton>
      ) : (
        <ActionButton
          kind="primary"
          size="lg"
          full
          glyph={<NxIcon name="guider" size={14} />}
          onPress={startGuiding}
          lockedReason={start.lockedReason}
          onExplain={start.onExplain}
          data-testid="guider-primary"
        >LOOP + PICK STAR</ActionButton>
      )}
      {stop.lockedReason && live && <Note tone="warn">{stop.lockedReason}</Note>}
      {start.lockedReason && !live && <Note tone="warn">{start.lockedReason}</Note>}

      {/* ------------------------------------------------- 8. guiding assistant */}
      <GuidingAssistant
        connected={connected}
        onOpenInTuning={setTuningSeed}
        onCalibrationChanged={(cleared) => {
          setCalTick((t) => t + 1);
          if (cleared) setSavedCalCleared(true);
        }}
      />

      {/* ------------------------------------------------------- 9. provider row */}
      <Card data-testid="guider-provider">
        <GuideProviderControl label="Guide" layout="stacked" />
      </Card>

      {/* ---------------------------------------------------- 10. tuning editor */}
      <TuningEditor
        wide={wide}
        draft={draft}
        setDraft={setDraft}
        commit={commit}
        lockedReason={tuning.lockedReason}
        onExplain={tuning.onExplain}
        loadError={cfg.loadError}
        onRetry={cfg.reload}
        seeded={tuningSeed != null}
      />

      {/* --------------------------------------------- 11. guide-scope offset */}
      <GuideScopeOffset />

      <Note data-testid="guider-footer">{FOOTER_NOTE}</Note>
      <div style={{ height: "8px", flexShrink: 0 }} />
    </Sheet>
  );
}

/** One optional settle override. `readOnly`, never the native `disabled`: a
 *  locked field stays focusable and announced, so the reason beside it is
 *  reachable (ARCHITECTURE.md non-negotiable 6). */
function SettleField({ label, placeholder, value, onChange, lockedReason, testId }: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
  lockedReason: string | null;
  testId: string;
}): JSX.Element {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
      <span className="nx-field-label">{label}</span>
      <input
        className={lockedReason ? "nx-input nx-locked" : "nx-input"}
        style={{ width: "84px" }}
        inputMode="decimal"
        placeholder={placeholder}
        value={value}
        readOnly={!!lockedReason}
        aria-disabled={lockedReason ? true : undefined}
        title={lockedReason ?? undefined}
        aria-label={label}
        onChange={(e) => { if (!lockedReason) onChange(e.target.value); }}
        data-testid={testId}
      />
    </label>
  );
}

// ============================================================ tuning editor

/** A `<select>` that keeps a reason when it is locked. There is no `readOnly`
 *  for a select, and the native `disabled` attribute would strip both the
 *  control and its reason out of the accessibility tree - so a locked picker
 *  renders as a focusable, tappable chip showing the current value, which is the
 *  house honest-disabled idiom (ARCHITECTURE.md non-negotiable 6). */
function AlgoPicker({ label, options, value, onChange, lockedReason, onExplain, testId }: {
  label: string;
  options: readonly { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
  lockedReason: string | null;
  onExplain: (r: string) => void;
  testId: string;
}): JSX.Element {
  const shown = options.find((o) => o.value === value)?.label ?? value;
  if (lockedReason) {
    return (
      <div className="nx-field">
        <span className="nx-field-label">{label}</span>
        <button
          type="button"
          className="nx-btn nx-locked"
          data-kind="secondary"
          aria-disabled="true"
          data-locked="true"
          title={lockedReason}
          aria-label={`${label} - ${lockedReason}`}
          onClick={() => onExplain(lockedReason)}
          data-testid={testId}
        ><span className="nx-btn-label">{shown}</span></button>
      </div>
    );
  }
  return (
    <div className="nx-field">
      <label className="nx-field-label" htmlFor={testId}>{label}</label>
      <select
        id={testId}
        className="nx-input"
        value={value}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
      >
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
}

function TuningEditor({ wide, draft, setDraft, commit, lockedReason, onExplain, loadError, onRetry, seeded }: {
  wide: boolean;
  draft: GuideSettings;
  setDraft: (s: GuideSettings) => void;
  commit: (s: GuideSettings) => Promise<void>;
  lockedReason: string | null;
  onExplain: (r: string) => void;
  loadError: string | null;
  onRetry: () => void;
  /** The assistant handed these fields over. Say so, because the numbers on
   *  screen are then a RECOMMENDATION and not what the rig is running. */
  seeded: boolean;
}): JSX.Element {
  // Open by default where there is room, folded on a phone. `wide` is read once
  // as the initial state rather than gating the render, so rotating a phone
  // into landscape does not slam a drawer the user just opened.
  const [paramsOpen, setParamsOpen] = useState(wide);
  const chooseRa = (kind: GuideAlgorithmKind) =>
    void commit({ ...draft, ra: { algorithm: kind, params: { ...GUIDE_ALGORITHM_DEFAULTS[kind] } } });
  const chooseDec = (kind: GuideAlgorithmKind) =>
    void commit({ ...draft, dec: { algorithm: kind, params: { ...GUIDE_ALGORITHM_DEFAULTS[kind] } } });

  return (
    <Card data-testid="guider-tuning">
      <Stack gap={8}>
        <Label>GUIDE TUNING</Label>
        {seeded && (
          <Note tone="warn" data-testid="guider-tuning-seeded">
            These are the Guiding Assistant&rsquo;s recommended values, not the
            rig&rsquo;s. Nothing is saved until you press SAVE TUNING.
          </Note>
        )}
        {loadError && (
          <Row>
            <Note tone="warn">
              Could not read the saved tuning ({loadError}). These are factory
              defaults, not your rig&rsquo;s settings - saving is held back so it
              cannot replace them.
            </Note>
            <ActionButton kind="ghost" onPress={onRetry}>TRY AGAIN</ActionButton>
          </Row>
        )}
        <AlgoPicker
          label="RA ALGORITHM"
          options={RA_GUIDE_ALGORITHMS}
          value={draft.ra.algorithm}
          onChange={(v) => { if (isValidRaAlgorithm(v)) chooseRa(v); }}
          lockedReason={lockedReason}
          onExplain={onExplain}
          testId="guider-ra-algorithm"
        />
        {/* PPEC is RA-ONLY. It is absent from DEC_GUIDE_ALGORITHMS and
            `validateGuideSettings` THROWS on PPEC for Dec, so the Dec picker
            must never offer it (lib/guideSettings.ts:44-53, 196-201). */}
        <AlgoPicker
          label="DEC ALGORITHM"
          options={DEC_GUIDE_ALGORITHMS}
          value={draft.dec.algorithm}
          onChange={(v) => { if (isValidDecAlgorithm(v)) chooseDec(v); }}
          lockedReason={lockedReason}
          onExplain={onExplain}
          testId="guider-dec-algorithm"
        />
        <Switch
          label="PREDICTIVE PEC"
          note="Gaussian-process periodic-error correction on RA. RA only - the Dec axis has no PPEC."
          checked={draft.ra.algorithm === "ppec"}
          onChange={(on) => chooseRa(on ? "ppec" : "hysteresis")}
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid="guider-ppec"
        />

        {/* A DISCLOSURE, NOT A BREAKPOINT (review #26).
            GAP-5 deferred the full editor to tablet, and on a portrait phone
            that took the per-axis `AlgoParams`, Dec guide direction, the Dec
            backlash pulse and SAVE TUNING off the screen entirely - with no
            "rotate" anywhere, and with `BOTH_AXES_NOTE` ("Per-axis values are
            in the tuning editor") rendering right beside an editor whose
            per-axis half was not there. `hysteresis` is only reachable through
            `AxisParams`, so the DEFAULT RA algorithm's one tunable was
            unreachable on the field-dominant device.
            The density argument still holds, so the block stays folded on a
            phone and open at tablet width - collapsed, not absent. */}
        <Divider />
        <ActionButton
          kind="ghost"
          onPress={() => setParamsOpen((o) => !o)}
          ariaLabel="Per-axis parameters, Dec guide direction and backlash pulse"
          data-testid="guider-params-toggle"
        >
          {paramsOpen ? "HIDE PER-AXIS PARAMETERS" : "PER-AXIS PARAMETERS"}
        </ActionButton>
        {paramsOpen && (
          <>
            <Label>PER-AXIS PARAMETERS</Label>
            <AxisParams
              axis="RA" kind={draft.ra.algorithm} params={draft.ra.params}
              onChange={(p) => setDraft({ ...draft, ra: { ...draft.ra, params: p } })}
              lockedReason={lockedReason}
            />
            <AxisParams
              axis="DEC" kind={draft.dec.algorithm} params={draft.dec.params}
              onChange={(p) => setDraft({ ...draft, dec: { ...draft.dec, params: p } })}
              lockedReason={lockedReason}
            />
            <AlgoPicker
              label="DEC GUIDE DIRECTION"
              options={DEC_GUIDE_MODES}
              value={draft.decGuideMode}
              onChange={(v) => {
                if (isValidDecGuideMode(v)) void commit({ ...draft, decGuideMode: v });
              }}
              lockedReason={lockedReason}
              onExplain={onExplain}
              testId="guider-dec-mode"
            />
            <div className="nx-field">
              <label className="nx-field-label" htmlFor="guider-blc">DEC BACKLASH PULSE (MS)</label>
              <input
                id="guider-blc"
                className={lockedReason ? "nx-input nx-locked" : "nx-input"}
                inputMode="numeric"
                value={String(draft.blcPulseMs)}
                readOnly={!!lockedReason}
                aria-disabled={lockedReason ? true : undefined}
                title={lockedReason ?? undefined}
                aria-label="Dec backlash pulse, milliseconds"
                onChange={(e) => setDraft({ ...draft, blcPulseMs: Number(e.target.value) || 0 })}
                data-testid="guider-blc"
              />
            </div>
            <ActionButton
              kind="primary"
              onPress={() => void commit(draft)}
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid="guider-tuning-save"
            >SAVE TUNING</ActionButton>
          </>
        )}
        <Note>
          Per-axis parameters start at the PHD2 default set and are clamped on
          save. Swapping an algorithm resets its parameters to that default.
          Changes apply on the next guiding start.
        </Note>
      </Stack>
    </Card>
  );
}

function AxisParams({ axis, kind, params, onChange, lockedReason }: {
  axis: string;
  kind: GuideAlgorithmKind;
  params: GuideAlgorithmParamDefaults;
  onChange: (p: GuideAlgorithmParamDefaults) => void;
  lockedReason: string | null;
}): JSX.Element {
  return (
    <Row>
      <span className="nx-label" style={{ width: "44px" }}>{axis}</span>
      {Object.entries(params).map(([k, v]) => (
        <label key={k} style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          <span className="nx-field-label">{k}</span>
          <input
            className={lockedReason ? "nx-input nx-locked" : "nx-input"}
            style={{ width: "84px" }}
            inputMode="decimal"
            value={String(v)}
            readOnly={!!lockedReason}
            aria-disabled={lockedReason ? true : undefined}
            title={lockedReason ?? undefined}
            aria-label={`${axis} ${kind} ${k}`}
            onChange={(e) => onChange({ ...params, [k]: Number(e.target.value) || 0 })}
          />
        </label>
      ))}
    </Row>
  );
}

// ========================================================== guiding assistant

function GuidingAssistant({ connected, onOpenInTuning, onCalibrationChanged }: {
  connected: boolean;
  onOpenInTuning: (body: GuideSettingsPutBody) => void;
  onCalibrationChanged: (clearedSaved: boolean) => void;
}): JSX.Element {
  const providers = useProviders();
  const status = useStatus();
  const guide = useGuide();
  const progress = useGuideAssistant();
  const clearProgress = useStore((s) => s.clearGuideAssistant);
  const showToast = useStore((s) => s.showToast);

  const [report, setReport] = useState<AssistantReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [includeBacklash, setIncludeBacklash] = useState(true);
  const [advanced, setAdvanced] = useState(false);

  // The RUN outlives this sheet: it is a 2-4 minute background task on the rig,
  // so its liveness is read off the server's own lane, never a local boolean.
  const { busy: running, arm: armRun } = useBusyOrPending("guide_assistant");
  const guideStarting = useBusy("guide");

  const kind = providers?.guide?.kind;
  const isNative = kind === "astrodeck" || kind === "sim";
  const guiding = !!(guide?.guiding ?? status?.guider?.guiding);

  // Two lanes block a start (`guide_assistant` and `guide`), and `useLock` takes
  // one at a time - so they are chained, in the gate's own order.
  const base = useLock({ cap: "control.guide", needsRole: "guider", busyLane: "guide_assistant" });
  const laneGuide = useLock({ cap: "control.guide", needsRole: "guider", busyLane: "guide" });
  const runReason = base.lockedReason ?? laneGuide.lockedReason
    ?? (!isNative ? "The Guiding Assistant works with the AstroDeck native guider"
      : guiding ? "Stop guiding first"
        : guideStarting ? "Guiding is starting - the assistant needs the mount to itself"
          : null);
  // STOP is an escape hatch and is never gated on the lane it ends.
  const stopReason = useLock({ cap: "control.guide", needsRole: "guider" }).lockedReason;
  const applyLock = useLock({ cap: "control.guide", needsRole: "guider", busyLane: "guide_assistant" });

  // The "done" tick is what says a report EXISTS to fetch, deduped by tick
  // IDENTITY so a retained tick cannot re-fetch on every unrelated render.
  const fetchedTick = useRef<unknown>(null);
  useEffect(() => {
    if (progress?.phase !== "done" || fetchedTick.current === progress) return;
    fetchedTick.current = progress;
    api.get<{ report: AssistantReport | null }>("/api/guide/assistant/report")
      .then((r) => {
        setReport(r.report);
        if (r.report) {
          setSelected(new Set(r.report.recommendations.filter((x) => !x.advanced).map((x) => x.key)));
        }
      })
      .catch((e) => showToast("error", (e as Error).message, { verbatim: true }));
  }, [progress, showToast]);

  useEffect(() => { if (!running) setStopping(false); }, [running]);

  const run = async () => {
    setReport(null);
    clearProgress();
    try {
      await api.post("/api/guide/assistant/start", { include_backlash: includeBacklash });
      // Only after the POST is accepted: arming on a 409/403 would show a
      // progress bar for a run that never started.
      armRun();
    } catch (e) { showToast("error", (e as Error).message, { verbatim: true }); }
  };

  const stop = async () => {
    if (stopping) return;
    setStopping(true);
    try { await api.post("/api/guide/assistant/stop"); }
    catch (e) { setStopping(false); showToast("error", (e as Error).message, { verbatim: true }); }
  };

  const apply = async (keys?: string[]) => {
    if (!report || busy) return;
    setBusy(true);
    try {
      const body: GuideSettingsPutBody = buildApplyBody(report, keys);
      const changesAlgo = applyChangesAlgorithm(report, keys);
      // Applying NEVER discards the saved calibration on its own (D4). The
      // selective path offers it through the app's own themed confirm, whose
      // dismiss answer is KEEP - the old native confirm() fell back to `?? true`,
      // destroying it by default.
      let clear = false;
      if (changesAlgo && keys) {
        clear = await confirmDialog({
          title: "Clear the saved calibration?",
          body: "You changed a guiding algorithm. You can keep the calibration you "
            + "already have, or clear it so the mount re-learns which way is which - "
            + "that adds about 2 minutes the next time guiding starts.",
          confirmLabel: "Clear it",
          cancelLabel: "Keep it",
          tone: "warn",
        });
      }
      await api.put("/api/guide/settings", body);
      if (clear) {
        let cleared = false;
        try {
          const r = await api.del<{ cleared?: boolean }>("/api/guide/calibration");
          cleared = !!r?.cleared;
        } catch (e) { showToast("warning", (e as Error).message, { verbatim: true }); }
        onCalibrationChanged(cleared);
      }
      showToast("success", "Recommended guide settings applied - they take effect on the next guiding start");
    } catch (e) {
      showToast("error", (e as Error).message, { verbatim: true });
    } finally { setBusy(false); }
  };

  const summary = report ? summarize(report) : null;
  const rows = report ? formatRecommendations(report) : [];
  const showRunning = running && !report && progress?.phase !== "error";
  const failed = !showRunning && !report && progress?.phase === "error";
  const failMessage = (progress?.message ?? "")
    .replace(/^native guider:\s*/i, "").replace(/^Guiding Assistant\s*/i, "");

  const applyReason = applyActionReason({ noPermissionReason: applyLock.lockedReason, busy });
  const applySelectedReason = applyActionReason({
    noPermissionReason: applyLock.lockedReason, busy, selectedCount: selected.size,
  });

  return (
    <Card data-testid="guider-assistant">
      <Stack gap={8}>
        <Label>GUIDING ASSISTANT</Label>
        {showRunning ? (
          <>
            <Bar value={(progress?.pct ?? 0) / 100} tone="accent" height={6} />
            <Note>{progress?.message ?? "Working... (started before this sheet loaded)"}</Note>
            <ActionButton
              kind="danger"
              onPress={() => void stop()}
              busy={stopping}
              lockedReason={stopReason}
              onExplain={base.onExplain}
              data-testid="guider-assistant-stop"
            >{stopping ? "STOPPING" : "STOP"}</ActionButton>
            {stopping && <Note>The run checks for this between pulses, so it can take a few seconds to halt.</Note>}
          </>
        ) : failed ? (
          <>
            <Note tone="bad">The Guiding Assistant stopped: {failMessage || "something went wrong."}</Note>
            <Note>Nothing was changed. Check that a star is visible in the guide camera and that the mount is tracking, then try again.</Note>
            <ActionButton kind="primary" onPress={() => void run()} lockedReason={runReason}
              onExplain={base.onExplain} data-testid="guider-assistant-start">TRY AGAIN</ActionButton>
          </>
        ) : !report ? (
          <>
            <Switch
              label="ALSO MEASURE MOUNT SLACK (MOVES THE SCOPE)"
              checked={includeBacklash}
              onChange={setIncludeBacklash}
              lockedReason={runReason}
              onExplain={base.onExplain}
              data-testid="guider-assistant-backlash"
            />
            <ActionButton
              kind="primary"
              onPress={() => void run()}
              lockedReason={runReason}
              onExplain={base.onExplain}
              data-testid="guider-assistant-start"
            >RUN GUIDING ASSISTANT</ActionButton>
            <Note tone={runReason ? "warn" : "dim"}>
              {runReason ?? (includeBacklash
                ? "This takes 2-4 minutes. AstroDeck watches a guide star, then deliberately nudges the mount up and down a few times to measure its slack. Make sure the scope can move freely."
                : "This takes about 2 minutes. AstroDeck watches a guide star drift and recommends guide settings. The mount keeps tracking and is not moved.")}
            </Note>
            {!connected && <Note tone="warn">No guider is connected, so there is nothing to measure.</Note>}
          </>
        ) : (
          <>
            {summary && <Note tone={summary.tone} data-testid="guider-assistant-summary">{summary.headline}</Note>}
            {applyChangesAlgorithm(report) && <Note>{ALGO_CHANGE_NOTE}</Note>}
            <Row>
              <ActionButton
                kind="primary"
                onPress={() => void apply()}
                busy={busy}
                lockedReason={applyReason}
                onExplain={base.onExplain}
                data-testid="guider-assistant-apply"
              >APPLY RECOMMENDED SETTINGS</ActionButton>
              <ActionButton
                kind="ghost"
                onPress={() => setReport(null)}
                lockedReason={applyActionReason({ busy })}
                onExplain={base.onExplain}
              >START OVER</ActionButton>
            </Row>
            {/* The measurements were `wide`-only, so a phone got a verdict and
                a single APPLY with no way to see the numbers behind either, or
                to take one recommendation and leave the rest (review #26). The
                disclosure IS the density answer - it is already closed by
                default at every width. */}
            <ActionButton kind="ghost" onPress={() => setAdvanced((a) => !a)}
              ariaLabel="Advanced measurements and per-setting apply"
              data-testid="guider-assistant-advanced">
              {advanced ? "HIDE MEASUREMENTS" : "MEASUREMENTS AND PER-SETTING APPLY"}
            </ActionButton>
            {advanced && (
            <Stack gap={8}>
              <div style={{ display: "flex", justifyContent: "center" }}>
                <GuideScatter samples={report.samples} />
              </div>
              <GuideGraph samples={report.samples} />
              <Note>
                {`RMS RA ${report.measurements.rms_ra_px.toFixed(2)} px · `}
                {`RMS Dec ${report.measurements.rms_dec_px.toFixed(2)} px · `}
                {`RMS total ${report.measurements.rms_total_px.toFixed(2)} px · `}
                {`drift ${report.measurements.drift_per_min_px.toFixed(2)} px/min · `}
                {`PE p-p ${(report.measurements.pe_amplitude_px * 2).toFixed(2)} px · `}
                {`jitter ${report.measurements.jitter_px.toFixed(2)} px`}
              </Note>
              <Note>
                {`backlash ${report.measurements.backlash.bl_ms} ± ${report.measurements.backlash.sigma_ms.toFixed(0)} ms - `}
                {backlashResultSentence(report.measurements.backlash.result_code)}
              </Note>
              {rows.map((r) => (
                <Switch
                  key={r.key}
                  label={`${r.label}: ${String(r.current)} to ${String(r.recommended)}${r.unit}`}
                  note={r.conflicts.length > 0
                    ? `${r.rationale} Either/or with the other suggestion for this setting - ticking this one unticks it.`
                    : r.rationale}
                  checked={selected.has(r.key)}
                  onChange={() => setSelected((prev) => toggleRecommendationKey(report, prev, r.key))}
                />
              ))}
              <ActionButton
                kind="secondary"
                onPress={() => void apply([...selected])}
                lockedReason={applySelectedReason}
                onExplain={base.onExplain}
                data-testid="guider-assistant-apply-selected"
              >APPLY SELECTED</ActionButton>
              {/* Hands the (possibly selective) body to the tuning editor
                  below for hand-tuning. It writes nothing by itself. */}
              <ActionButton
                kind="ghost"
                onPress={() => onOpenInTuning(
                  buildApplyBody(report, selected.size ? [...selected] : undefined))}
                data-testid="guider-open-in-tuning"
              >OPEN IN TUNING EDITOR</ActionButton>
              {selected.size === 0 && <Note>{EMPTY_SELECTION_REASON}</Note>}
            </Stack>
            )}
          </>
        )}
      </Stack>
    </Card>
  );
}

// ======================================================== guide-scope offset

/** Where the guide scope points relative to the OTA. A MEASUREMENT, not a
 *  setting: it solves both cameras where the mount is now and reports the
 *  difference. It moves nothing. */
function GuideScopeOffset(): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<GuideOffsetMeasurement | null>(null);
  const [err, setErr] = useState<string | null>(null);
  // Cap, then the guider role, then the lane - the gate's own priority, spread
  // over three calls because this control needs two roles.
  const capLock = useLock({ cap: "control.mount", needsRole: "telescope" });
  const guiderLock = useLock({ needsRole: "guider" });
  const laneLock = useLock({ busyLane: "solve" });
  const locked = capLock.lockedReason ?? guiderLock.lockedReason ?? laneLock.lockedReason
    ?? (busy ? "Solving both cameras..." : null);

  const run = async () => {
    setBusy(true); setErr(null); setRes(null);
    try {
      const before = (await getGuideOffset()).last;
      await startGuideOffsetMeasure();
      // POLLED, NOT AWAITED. Two solves run about forty seconds and the POST
      // returns as soon as the lane starts, so awaiting it would report success
      // before anything had been solved.
      const deadline = Date.now() + 180_000;
      for (;;) {
        await new Promise((r) => setTimeout(r, 3000));
        const now = (await getGuideOffset()).last;
        if (now && now !== before && JSON.stringify(now) !== JSON.stringify(before)) {
          setRes(now); break;
        }
        if (Date.now() > deadline) { setErr("the measurement did not finish within three minutes"); break; }
      }
    } catch (e) {
      setErr((e as Error).message || "the measurement did not complete");
    } finally { setBusy(false); }
  };

  const off = res?.offset ?? null;
  return (
    <Card data-testid="guider-offset">
      <Stack gap={8}>
        <Label>GUIDE SCOPE OFFSET</Label>
        <Note>{OFFSET_NOTE}</Note>
        <ActionButton
          kind="secondary"
          onPress={() => void run()}
          busy={busy}
          lockedReason={locked}
          onExplain={capLock.onExplain}
          data-testid="guider-measure-offset"
        >MEASURE OFFSET</ActionButton>
        {err && <Note tone="bad">{err}</Note>}
        {res && !off && <Note tone="warn">{res.reason ?? "no offset could be computed from this pair"}</Note>}
        {off && (
          <Note>
            {`separation ${(off.sep_arcsec / 60).toFixed(2)}' (${Math.round(off.sep_arcsec)}") · `}
            {`angle ${off.pa_deg.toFixed(1)}° in the instrument frame, stored against `}
            {`${off.measured_pa_deg.toFixed(1)}° so it stays true through a meridian flip.`}
          </Note>
        )}
      </Stack>
    </Card>
  );
}

export default GuiderSheet;
