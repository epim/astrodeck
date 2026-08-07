import { useEffect, useRef, useState, type ReactNode } from "react";
import type { CalibrationReport } from "../types";
import { api } from "../api";
import {
  useStore, useStatus, useGuide, useProviders, useGuideRmsByKind,
  useGuideAssistant,
} from "../store";
import {
  summarize, formatRecommendations, buildApplyBody, applyChangesAlgorithm,
  toggleRecommendationKey, backlashResultSentence,
  applyActionReason, EMPTY_SELECTION_REASON,
  type AssistantReport, type GuideSettingsPutBody,
} from "../lib/guideAssistant";
import { confirmDialog } from "../components/ConfirmDialog";
import { GuideGraph, GuideScatter } from "../components/graphs";
import { Icon } from "../components/icons";
import {
  Panel, Stat, Led, Toggle, Disclosure, LockedChip, LockedNote, lockedProps,
  LOCKED_CLASS,
} from "../components/ui";
import { useCanControlGuide, accessPhrase } from "../lib/caps";
import { useBusy, useBusyLanes, useBusyOrPending } from "../lib/useBusy";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import ProviderBadge from "../components/ProviderBadge";
import GuideFramePreview from "../components/GuideFramePreview";
import GuideProviderControl from "../components/GuideProviderControl";
import { compareRmsWindows } from "../lib/rmsCompare";
import { selectGuideWindows } from "../lib/guideRms";
import { guideNarration } from "../lib/guideNarration";
import GuideQuickBar from "../components/GuideQuickBar";
import {
  RA_GUIDE_ALGORITHMS,
  DEC_GUIDE_ALGORITHMS,
  DEC_GUIDE_MODES,
  GUIDE_ALGORITHM_DEFAULTS,
  defaultGuideSettings,
  validateGuideSettings,
  isValidRaAlgorithm,
  isValidDecAlgorithm,
  isValidDecGuideMode,
  toSnake,
  type GuideAlgorithmKind,
  type GuideAlgorithmParamDefaults,
  type DecGuideMode,
} from "../lib/guideSettings";

export default function GuideView() {
  const status = useStatus();
  const guide = useGuide();
  const showToast = useStore((s) => s.showToast);
  // UX #34: the pixels-not-arcsec note below needs to be able to DELIVER the
  // user to the control it names, not just name it.
  const setView = useStore((s) => s.setView);
  const canGuide = useCanControlGuide(); // viewer => graph visible, controls read-only
  const [ditherPx, setDitherPx] = useState("3");
  // UX-24: optional dither settle overrides (blank = the guider's default).
  const [settlePixels, setSettlePixels] = useState("");
  const [settleTime, setSettleTime] = useState("");
  const [settleTimeout, setSettleTimeout] = useState("");
  // Number(ditherPx) || 3 coerced a deliberately-entered "0" to 3 (0 is
  // falsy). Parse explicitly so 0 is honored; only fall back to the 3px
  // default for genuinely invalid (blank/non-numeric) input.
  const ditherNum = Number(ditherPx);
  const stats = guide ?? status?.guider ?? null;
  const connected = !!status?.guider || !!guide;

  // UX-15: when no guide-scope focal length is configured the native guider
  // reports RMS in guide-camera PIXELS, not arcsec. Label the unit honestly
  // (px vs ″) instead of stamping "arcsec" on raw pixels.
  //
  // ABSENT ⇒ PIXELS, matching GuideStats.is_arcsec's own default. This used to
  // read `!== false`, so a payload missing the field defaulted to ARCSEC —
  // fail-open on the client while the server fails closed. The two ends
  // disagreeing about the safe direction is how 0.90 px comes to be shown as
  // 0.90″, which on a 240 mm guide scope reads as excellent guiding when the
  // true figure is ~3.2″. Every live payload does carry the flag (it is a
  // dataclass field, spread into both the status poll and the guide event), so
  // this is the boundary being made honest rather than a bug being chased.
  const isArcsec = stats?.is_arcsec === true;
  // UX-35: the true prime glyph (″), not an ASCII quote, to match arcsec/arcmin
  // typography elsewhere in the app.
  const unit = isArcsec ? "″" : "px";
  const unitWord = isArcsec ? "arcsec" : "px";

  // NOV-7: plain-language narration — what the guider is doing right now +
  // an honest words verdict on the RMS number. Computed once; the render
  // below is a thin binding (design doc §1.6).
  const narration = guideNarration({
    connected,
    phase: stats?.phase,
    guiding: !!stats?.guiding,
    rmsTotal: stats?.rms_total ?? 0,
    isArcsec,
    imageScale: stats?.image_scale ?? 0,
    hasSamples: (stats?.recent?.length ?? 0) > 0,
  });
  const toneClass = { good: "text-good", warn: "text-warn", bad: "text-bad", neutral: "text-dim" }[narration.tone];

  // UX-16: in-flight guard on the multi-second guide actions (start / recalibrate)
  // so they can't be double-fired in the POST round-trip. Covers only the request
  // round-trip, so Stop stays usable while calibration/guiding runs server-side.
  const [acting, setActing] = useState(false);
  const act = async (fn: () => Promise<unknown>) => {
    if (acting) return;
    setActing(true);
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
    finally { setActing(false); }
  };

  // Start Guiding and Force Recalibrate both spawn the SERVER's "guide" lane,
  // and the work that lane does — find a star, then walk a 1–3 minute
  // calibration — happens with `guiding` still FALSE the whole time. Gating
  // these four buttons on `guiding` alone therefore left every one of them
  // reporting the wrong thing for the longest, most alarming part of a start.
  // The lane is the cross-provider truth (it survives a reload, and the bridge
  // guiders publish no `phase`); `phase` — native guider only — names WHICH
  // step, which is what makes the reasons below worth reading.
  const { busy: guideLaneBusy, arm: armGuideLane } = useBusyOrPending("guide");
  // Server truth with no local latch: Stop's copy must follow the RIG, never an
  // optimistic click of ours.
  const guideLaneLive = useBusy("guide");
  const phase = stats?.phase ?? "";
  // `phase` is a FALLBACK for a server too old to publish `busy_lanes` — and
  // only there. `useBusyLanes()` is undefined in exactly that case and in no
  // other (an idle rig publishes `[]`), so that is what the fallback is gated
  // on. Trusting `phase` unconditionally locked the hardware out: the native
  // guider sets `_phase_hint = "calibrating"` before the walk and clears it
  // only on the SUCCESS path (native.py:454) or in `stop_guiding` — a walk
  // that times out or loses its star leaves the hint set, `_spawn`'s wrapper
  // only logs the exception, and every 2s status frame republishes it. With
  // the lane long since idle, Start, Force Recalibrate and Stop were all dim
  // for the rest of the session, and the two that recover the rig are the ones
  // this page exists for. Lane says idle ⇒ nothing is starting up, whatever a
  // leftover hint claims. (`"finding"` was never trusted here anyway: the
  // guide loop also reports it once it is up and hunting for its lock, which
  // is a state Stop can and should end.)
  const lanes = useBusyLanes();
  const startingUp = guideLaneBusy || (lanes === undefined && phase === "calibrating");

  // UX-23: fetch the guider's calibration report so a bad/flipped calibration is
  // visible before it runs the mount away from the star. Refetch when guiding
  // (re)starts — a fresh calibration completes on start / Force Recalibrate.
  const [calReport, setCalReport] = useState<CalibrationReport | null>(null);
  // A calibration is destroyed from two places BELOW this panel — Guide
  // Tuning's Clear Calibration and the assistant's apply-with-clear — and
  // neither moves `guiding`, so the green "calibrated · Good calibration" LED
  // sat there describing something the user had just deleted. Both children
  // report in here.
  //
  // THE REFETCH ALONE CANNOT FIX THAT, and the first version of this claimed it
  // did. `DELETE /api/guide/calibration` unlinks the PERSISTED
  // `<profile>.json` (+ the PPEC window) and is documented "Does not disturb an
  // in-flight guide loop" (native.py:1278). `GET` answers with
  // `calibration_report()`, which dumps the ENGINE's in-memory calibration
  // (native.py:1216) — a different object, untouched by the delete. So the
  // re-read comes back byte-identical and the LED is right to stay green: the
  // guider really does still hold that calibration. What the user destroyed is
  // the copy the NEXT start would have reused. The panel says which, below —
  // the two only diverge from the moment something is cleared, so the line is
  // rendered from that moment and not before.
  const [calTick, setCalTick] = useState(0);
  const [savedCalCleared, setSavedCalCleared] = useState(false);
  const refreshCalibration = (clearedSaved: boolean) => {
    setCalTick((t) => t + 1);
    if (clearedSaved) setSavedCalCleared(true);
  };
  // `start_guiding` ends with `_persist_calibration()` before it sets
  // `_active` (native.py:449-456), so a guider that reports `guiding` has just
  // written a saved copy again — Force Recalibrate's walk included. Drop the
  // line then, rather than leaving it to outlive the fact it states.
  const guidingNow = !!stats?.guiding;
  useEffect(() => { if (guidingNow) setSavedCalCleared(false); }, [guidingNow]);
  // "Open in tuning editor" hand-off (design §4.2): the Guiding Assistant panel
  // seeds the existing GuideSettingsDrawer with recommended params for hand
  // tuning, reusing the AlgoParams editor rather than building a new one.
  const [tuningSeed, setTuningSeed] = useState<GuideSettingsPutBody | null>(null);
  // ------------------------------------------------- UX #24: stated reasons
  // Start / Stop / Force Recalibrate / Dither were natively `disabled` with no
  // title, no aria-label and no note: on the tablet, four dim silent boxes.
  // Each blocker is now a sentence, rendered through this file's `HonestButton`
  // (dim + aria-disabled + focusable + tap-to-explain) with the same sentence
  // printed underneath for anyone who never presses it.
  const guideReadOnlyReason = canGuide
    ? null
    : `Read-only session — ${accessPhrase("control.guide")} required`;
  const noGuiderReason = connected
    ? null
    : "No guider is connected — set one up on the Equipment page";
  // What the "guide" lane is doing right now, in the user's words. Used by
  // three of the four reasons below, so the screen never offers two different
  // accounts of the same operation.
  const startingWhat =
    phase === "calibrating"
      ? "Calibrating the guider — the mount is learning which way it moves"
      : phase === "finding" ? "Looking for a guide star"
        : "Guiding is already starting";
  const startReason =
    guideReadOnlyReason ?? noGuiderReason
    ?? (stats?.guiding ? "Guiding is already running"
      // Pressing again here is answered `'guide' is already running` — a 409
      // the user reads as a fault, half a minute into a start that is going
      // fine. Say what it is doing and that it finishes on its own.
      : startingUp ? `${startingWhat}. Guiding begins on its own when it finishes.`
        : acting ? "Still working on the last command" : null);
  const stopReason =
    guideReadOnlyReason ?? noGuiderReason
    ?? (stats?.guiding ? null
      : guideLaneLive
        // Inside the lane, the NATIVE guider polls nothing: `stop_guiding` sets
        // `_stop`, the walk never looks at it, and `start_guiding` clears it
        // again on the way into the loop — so the press is swallowed and guiding
        // starts anyway. Dimming it is right; claiming "there is nothing to
        // stop" beside a header reading "Calibrating the guider…" was not. A
        // bridge guider (PHD2/NINA) publishes no phase and its stop DOES abort a
        // start, so it keeps a live Stop.
        ? (phase === "calibrating" || phase === "finding"
          ? `${startingWhat}. This step can't be interrupted — Stop works once guiding is running.`
          : null)
        // Lane finished, `guiding` not yet true: the loop IS up and hunting its
        // lock, and Stop ends it. Only a genuinely idle guider has nothing to stop.
        : phase === "finding" ? null
          : "Guiding isn't running — there is nothing to stop");
  const calibrateReason =
    guideReadOnlyReason ?? noGuiderReason
    // This one is not merely a 409: /api/guide/calibrate spawns with
    // replace=True, so a second press CANCELS the walk in flight and starts the
    // whole thing over. The reason has to block the press, not just explain it.
    ?? (startingUp
      ? `${startingWhat}. Pressing again cancels it and starts the calibration over.`
      : acting ? "Still working on the last command" : null);
  const ditherReason =
    guideReadOnlyReason ?? noGuiderReason
    ?? (!stats?.guiding ? "Start guiding first — a dither nudges the star and re-settles" : null);
  // The blocker they all share is stated ONCE at the top of the panel; a
  // per-button line only earns its space when that button's reason DIFFERS
  // (Stop when idle, Dither when not guiding). Four identical lines under four
  // buttons is noise, and noise is how a reason stops being read.
  const sharedGuideReason = guideReadOnlyReason ?? noGuiderReason;
  const distinct = (r: string | null) => (r && r !== sharedGuideReason ? r : null);
  const explain = (r: string) => showToast("info", r);

  useEffect(() => {
    let cancelled = false;
    if (!connected) { setCalReport(null); return; }
    api.get<{ report: CalibrationReport | null }>("/api/guide/calibration")
      .then((r) => { if (!cancelled) setCalReport(r.report); })
      .catch(() => { if (!cancelled) setCalReport(null); });
    return () => { cancelled = true; };
    // `guideLaneLive` covers Force Recalibrate, which clears the stored
    // calibration at the START of a walk that ends minutes later; `calTick` is
    // the two children below that clear it with no state change of their own.
  }, [connected, stats?.guiding, guideLaneLive, calTick]);

  return (
    <div className="flex flex-col gap-3">
    {/* sticky glance + guide-camera speed dials — state, RMS, and the
        calibration walk, visible from any scroll position */}
    <GuideQuickBar />
    <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
      <Panel title={`Guide Error · ${unitWord}`}
        right={
          <span className="flex items-center gap-2">
            <ProviderBadge cap="guide" />
            <span className={`text-[11px] font-medium ${toneClass}`}>
              {narration.phaseText}
            </span>
          </span>
        }>
        <GuideGraph samples={stats?.recent ?? []} />
        <div className="grid grid-cols-4 gap-3 mt-4 border-t border-line pt-3">
          <Stat label='RMS RA' value={stats ? stats.rms_ra.toFixed(2) : "—"} unit={unit} />
          <Stat label='RMS Dec' value={stats ? stats.rms_dec.toFixed(2) : "—"} unit={unit} />
          <Stat label='RMS Total' value={stats ? stats.rms_total.toFixed(2) : "—"} unit={unit}
            tone={stats && stats.rms_total > 0 ? (stats.rms_total < 1 ? "good" : stats.rms_total < 2 ? "warn" : "bad") : undefined} />
          <Stat label="SNR" value={stats ? stats.snr.toFixed(0) : "—"} />
        </div>
        {narration.verdict && (
          <p className={`text-[11px] mt-2 leading-snug ${toneClass}`}>{narration.verdict}</p>
        )}
        {stats && !isArcsec && (
          /* UX-15: raw pixels, not arcsec — tell the user why and how to fix it.
             UX #34: this used to point at "Optics", a panel that exists NOWHERE
             by that name (the Settings tabs are Connect/Profiles/Calibration/
             Safety/Alerts/Updates/Account/Users/Auth). The control is the
             "Guide scope FL" field in Atlas's framing header — name it, and
             carry the user there rather than making them hunt, because the
             consequence of not finding it is the pixels-labelled-arcsec gate. */
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <p className="text-[11px] text-dim leading-snug flex-1 min-w-[220px]">
              RMS is in guide-camera pixels, not arcsec — the guide scope&rsquo;s focal
              length isn&rsquo;t set. It lives on the Atlas page, in the framing
              header, as <span className="mono">Guide scope FL</span>. If Atlas is
              still empty, press <span className="mono">Free-roam the sky</span> first
              to open the framing controls.
            </p>
            <button
              className="btn tap min-h-[44px] !px-3 text-[11px]"
              onClick={() => setView("atlas")}>
              Open Atlas
            </button>
          </div>
        )}
      </Panel>

      <div className="flex flex-col gap-4">
        <Panel title="Scatter">
          <div className="flex justify-center">
            <GuideScatter samples={stats?.recent ?? []} />
          </div>
        </Panel>

        {/* Guide-camera glance with a lock-region reticle. */}
        <GuideFramePreview reticle />

        <Panel title="Control" right={!canGuide && <ReadOnlyBadge />}>
          {!connected && (
            <p className="text-xs text-warn mb-3">
              no guider — connect the simulator rig, an Alpaca guide camera, or PHD2 on the Rig page
            </p>
          )}
          {guideReadOnlyReason && <LockedNote reason={guideReadOnlyReason} className="mb-3" />}
          <div className="flex flex-col gap-2">
            {/* min-h-11: these measured 34px tall, under the 44px touch floor. */}
            <HonestButton className="btn btn-accent min-h-11" reason={startReason}
              onExplain={explain}
              onClick={() => act(async () => {
                await api.post("/api/guide/start");
                // The POST returns the moment the task is CREATED. Latch until
                // the lane shows up on a status frame (≤2s) so the button does
                // not look pressable again in between.
                armGuideLane();
              })}>
              <Icon name="guide" size={14} className="inline -mt-0.5 mr-1" />Start Guiding
            </HonestButton>
            {distinct(startReason) && <LockedNote reason={distinct(startReason)!} className="-mt-1" />}
            <HonestButton className="btn min-h-11" reason={stopReason}
              onExplain={explain}
              onClick={() => act(() => api.post("/api/guide/stop"))}>
              Stop
            </HonestButton>
            {distinct(stopReason) && <LockedNote reason={distinct(stopReason)!} className="-mt-1" />}
            <HonestButton className="btn min-h-11" reason={calibrateReason}
              onExplain={explain}
              onClick={() => act(async () => {
                await api.post("/api/guide/calibrate");
                armGuideLane();
                showToast("info",
                  "Recalibrating — the mount walks a fresh calibration, then "
                  + "guiding starts on its own");
              })}>
              Force Recalibrate
            </HonestButton>
            {distinct(calibrateReason) && <LockedNote reason={distinct(calibrateReason)!} className="-mt-1" />}
            <div className="grid grid-cols-[1fr_auto] gap-2 items-end mt-2">
              <label className="flex flex-col gap-1">
                <span className="label">Dither (px)</span>
                <input className="field" value={ditherPx}
                  readOnly={!canGuide} aria-readonly={!canGuide || undefined}
                  onChange={(e) => setDitherPx(e.target.value)} />
              </label>
              <HonestButton className="btn min-h-11" reason={ditherReason}
                onExplain={explain}
                onClick={() => act(() => {
                  // UX-24: send only the settle fields the user set; blanks keep
                  // the guider's default. (Bridge honors all; native → timeout.)
                  const opt = (v: string) => {
                    const n = Number(v);
                    return v.trim() !== "" && Number.isFinite(n) ? n : undefined;
                  };
                  return api.post("/api/guide/dither", {
                    pixels: Number.isFinite(ditherNum) ? ditherNum : 3,
                    settle_pixels: opt(settlePixels),
                    settle_time_s: opt(settleTime),
                    settle_timeout_s: opt(settleTimeout),
                  });
                })}>
                Dither
              </HonestButton>
            </div>
            {distinct(ditherReason) && <LockedNote reason={distinct(ditherReason)!} />}
            <div className="grid grid-cols-3 gap-2 mt-1">
              <label className="flex flex-col gap-1">
                <span className="label !text-[9px]">settle px</span>
                <input className="field !py-1" placeholder="1.5" value={settlePixels}
                  readOnly={!canGuide} aria-readonly={!canGuide || undefined} inputMode="decimal"
                  onChange={(e) => setSettlePixels(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="label !text-[9px]">settle s</span>
                <input className="field !py-1" placeholder="8" value={settleTime}
                  readOnly={!canGuide} aria-readonly={!canGuide || undefined} inputMode="decimal"
                  onChange={(e) => setSettleTime(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="label !text-[9px]">timeout s</span>
                <input className="field !py-1" placeholder="60" value={settleTimeout}
                  readOnly={!canGuide} aria-readonly={!canGuide || undefined} inputMode="decimal"
                  onChange={(e) => setSettleTimeout(e.target.value)} />
              </label>
            </div>
          </div>
        </Panel>

        {calReport && (
          <Panel title="Calibration">
            {(() => {
              // Orthogonality > ~10° means the RA/Dec axes aren't square — a
              // classic sign of a poor or wrong-declination calibration.
              const bad = !calReport.is_valid;
              const warn = calReport.is_valid && calReport.ortho_error_deg > 10;
              return (
                <div className="flex items-center gap-2 mb-3">
                  <Led state={bad ? "bad" : warn ? "warn" : "on"}
                    label={bad ? "not calibrated" : warn ? "check calibration" : "calibrated"} />
                  <span className={`text-sm font-medium ${bad ? "text-bad" : warn ? "text-warn" : "text-good"}`}>
                    {bad ? "No valid calibration" : warn ? "Non-orthogonal — verify" : "Good calibration"}
                  </span>
                </div>
              );
            })()}
            <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-xs">
              <div className="flex justify-between"><span className="label">orthogonality</span>
                <span className="mono tabular-nums">{calReport.ortho_error_deg.toFixed(1)}°</span></div>
              <div className="flex justify-between"><span className="label">binning</span>
                <span className="mono tabular-nums">{calReport.binning}×</span></div>
              {calReport.declination_deg != null && (
                <div className="flex justify-between"><span className="label">dec</span>
                  <span className="mono tabular-nums">{calReport.declination_deg.toFixed(0)}°</span></div>
              )}
              {calReport.pier_side && calReport.pier_side !== "unknown" && (
                <div className="flex justify-between"><span className="label">pier</span>
                  <span className="mono">{calReport.pier_side}</span></div>
              )}
            </div>
            {savedCalCleared && (
              /* The half of "Clear Calibration" this panel is evidence of. The
                 numbers above are the guider's own, and they did not change —
                 saying which copy went is the difference between a control that
                 looks broken and one the user can plan around. */
              <p className="text-[11px] text-warn leading-snug mt-3 border-t border-line pt-2">
                {stats?.guiding
                  ? "The saved copy was cleared. These numbers are the "
                    + "calibration guiding is using right now — it keeps them "
                    + "until it stops, then the next start calibrates from "
                    + "scratch. Force Recalibrate replaces them now."
                  : "The saved copy was cleared. These numbers are what the "
                    + "last run measured, still held in memory; nothing is "
                    + "stored, so the next start calibrates from scratch."}
              </p>
            )}
            {calReport.advisories.length > 0 && (
              <ul className="mt-3 flex flex-col gap-1 border-t border-line pt-2">
                {calReport.advisories.map((a, i) => (
                  <li key={i} className="text-[11px] text-warn flex items-start gap-1.5">
                    <span aria-hidden>⚠</span><span>{a}</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        )}

        <GuideAssistantPanel canGuide={canGuide} connected={connected}
          onToast={showToast} onOpenInTuning={setTuningSeed}
          onCalibrationChanged={refreshCalibration} />

        <GuideProviderPanel />

        <GuideSettingsDrawer canGuide={canGuide} connected={connected}
          onToast={showToast} seed={tuningSeed}
          onCalibrationChanged={refreshCalibration} />
      </div>
    </div>
    </div>
  );
}

// ------------------------------------------------------------- settings drawer
// Per-axis guide-ALGORITHM selection (native guider, spec §3.5). Loads the
// persisted selection lazily on first expand (GET /api/guide/settings), and PUTs
// the validated pick. Clearing the persisted calibration lives here too — an
// algorithm change usually wants a fresh calibration. Only the algorithm KIND is
// editable server-side today; each algorithm's dossier §15 params are shown
// read-only for reference (see lib/guideSettings.ts).
type ToastFn = (level: "success" | "info" | "warning" | "error", msg: string) => void;

// ------------------------------------------------------------ provider switch
// Per-profile guide-provider override (P5-T1, spec §6 P5) + a same-night
// head-to-head RMS comparison.
//
// The CONTROL is no longer written here. It moved into the shared
// components/GuideProviderControl.tsx so that Equipment's Tasks section can
// render the SAME control as a fourth task row (UX-02) — the fourth pinnable
// capability used to be reachable only from this screen, which is the one place
// a user looking for "who runs each task" does not think to look. A second copy
// of it on Equipment would have been worse than the omission: the layer-aware
// write-back rule (#132 — a profile pin is edited IN the profile, not in the
// global block the profile shadows) has to be identical on both screens, and two
// copies of that decision is exactly how the original bug survived.
//
// What stays here is the part that is genuinely Guide-view-only: the same-night
// head-to-head. It reads store.ts's `guideRmsByKind` (tagged at bus-ingest time
// from `status.providers.guide.kind`, since the "guide" bus channel itself
// carries no provider field) and hands the native-family window ("astrodeck",
// or "sim" on a sim rig — both run the SAME NativeGuider engine, see
// providers.py::_resolve_guide) and the PHD2/NINA-family window ("backend") to
// the pure lib/rmsCompare.ts helper.
function GuideProviderPanel() {
  const rmsByKind = useGuideRmsByKind();
  const { native: nativeWindow, backend: phd2Window } = selectGuideWindows(rmsByKind);
  const cmp = compareRmsWindows(nativeWindow, phd2Window);
  const cmpTone =
    cmp.verdict === "insufficient-data" ? "text-dim" : cmp.verdict === "comparable" ? "text-dim" : "text-good";

  return (
    <Panel title="Guide Provider" right={<ProviderBadge cap="guide" />}>
      <div className="flex flex-col gap-2.5">
        {/* "stacked": this panel lives in a 300px column, where an inline label
            plus a chip group wraps into nonsense. Everything else about the
            control — eligibility, reasons, where the save lands — is identical
            to the Equipment row. */}
        <GuideProviderControl label="Provider override" layout="stacked" />

        <div className="border-t border-line pt-2.5 mt-0.5">
          <span className="label">Same-night RMS: native vs. PHD2</span>
          <p className={`text-[11px] mt-1 leading-snug ${cmpTone}`}>{cmp.message}</p>
        </div>
      </div>
    </Panel>
  );
}

/** snake_case → camelCase for ONE param key: the inverse of guideSettings'
 *  `toSnake`, applied at the read boundary only (the client's own state stays
 *  camelCase throughout). */
const toCamelKey = (k: string) =>
  k.replace(/_([a-z])/g, (_m, c: string) => c.toUpperCase());

/** The algorithm's dossier §15 defaults with the axis's PERSISTED overrides laid
 *  on top.
 *
 *  Server-side every `GuideAxisParams` field is optional-null, and null means
 *  "not pinned — the engine's own default applies", so a null must leave the
 *  default it stands for alone rather than blanking the field. Keys the chosen
 *  algorithm does not have are dropped for the same reason a swap resets the
 *  editors: a `hysteresis` left over from a previous algorithm is not a
 *  parameter of this one, and showing it would only invite Save to persist it. */
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

function GuideSettingsDrawer({ canGuide, connected, onToast, seed, onCalibrationChanged }: {
  canGuide: boolean;
  connected: boolean;
  onToast: ToastFn;
  seed?: GuideSettingsPutBody | null;
  /** Tell the Calibration panel above to re-read. `clearedSaved` is the
   *  server's own answer to "was a persisted calibration actually removed" —
   *  the panel says so, because the report it renders comes from the engine and
   *  will NOT change. */
  onCalibrationChanged?: (clearedSaved: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  // Why the GET's failure is kept, not just toasted: `loaded` now gates Save
  // (see `saveReason`), so the difference between "still reading" and "could
  // not read" is the difference between waiting and doing something about it.
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [ra, setRa] = useState<GuideAlgorithmKind>(defaultGuideSettings().ra.algorithm);
  const [dec, setDec] = useState<GuideAlgorithmKind>(defaultGuideSettings().dec.algorithm);
  // PRO-12 Tier 2 (T7): per-axis PARAMETER edits, lifted here alongside the
  // algorithm-kind state above. Seeded from GUIDE_ALGORITHM_DEFAULTS[kind]
  // whenever the kind changes (algorithm swap in the select, or the initial
  // load) — the engine's own dossier §15 default for a param not explicitly
  // edited, matching the T6 "None -> engine default" contract on the Python
  // side (GuideAxisParams / set_guide).
  const [raParams, setRaParams] = useState<GuideAlgorithmParamDefaults>(
    defaultGuideSettings().ra.params);
  const [decParams, setDecParams] = useState<GuideAlgorithmParamDefaults>(
    defaultGuideSettings().dec.params);
  // PRO-12 Tier 1: Dec guide direction + static BLC seed pulse. Both are
  // ALREADY forwarded end-to-end by the engine (_build_engine_config); this
  // drawer is the last missing layer. blcMs is a text field (parsed on save,
  // like the dither settle fields above) so a blank/in-progress edit never
  // fights the numeric coercion.
  const [decMode, setDecMode] = useState<DecGuideMode>(defaultGuideSettings().decGuideMode);
  const [blcMs, setBlcMs] = useState<string>("0");

  // "Open in tuning editor" lands here, but this panel sits two panels BELOW
  // the fold on a tablet — seeding it silently looked like the button did
  // nothing. `jump` is armed by the seed effect and consumed by the effect
  // after it (one commit later, so the drawer's controls are mounted by then):
  // scroll the panel into view AND move real keyboard focus into it, so
  // keyboard and screen-reader users make the same trip sighted users do.
  const panelRef = useRef<HTMLDivElement | null>(null);
  const firstControlRef = useRef<HTMLSelectElement | null>(null);
  const [jump, setJump] = useState(false);

  const chooseRa = (kind: GuideAlgorithmKind) => {
    setRa(kind);
    setRaParams({ ...GUIDE_ALGORITHM_DEFAULTS[kind] });
  };
  const chooseDec = (kind: GuideAlgorithmKind) => {
    setDec(kind);
    setDecParams({ ...GUIDE_ALGORITHM_DEFAULTS[kind] });
  };

  const load = async () => {
    try {
      const s = await api.get<{
        ra_algorithm: string; dec_algorithm: string;
        dec_guide_mode?: string; blc_pulse_ms?: number;
        ra_params?: Record<string, number | null> | null;
        dec_params?: Record<string, number | null> | null;
      }>("/api/guide/settings");
      // The PERSISTED per-axis params were never read here, and `chooseRa` /
      // `chooseDec` reset the editors to the dossier §15 factory defaults — so
      // the drawer opened on defaults whatever was on the rig, and Save (which
      // PUTs ra_params/dec_params unconditionally) overwrote a hand-tuned axis
      // on every open-and-save, not only when this GET failed. Set the kind and
      // its saved overrides together instead.
      const raKind = isValidRaAlgorithm(s.ra_algorithm) ? s.ra_algorithm : ra;
      const decKind = isValidDecAlgorithm(s.dec_algorithm) ? s.dec_algorithm : dec;
      setRa(raKind);
      setRaParams(withSavedParams(raKind, s.ra_params));
      setDec(decKind);
      setDecParams(withSavedParams(decKind, s.dec_params));
      if (isValidDecGuideMode(s.dec_guide_mode ?? "")) setDecMode(s.dec_guide_mode as DecGuideMode);
      if (typeof s.blc_pulse_ms === "number") setBlcMs(String(s.blc_pulse_ms));
      setLoadError(null);
      setLoaded(true);
    } catch (e) {
      setLoadError((e as Error).message);
      onToast("error", (e as Error).message);
    }
  };

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !loaded) void load();
  };

  // "Open in tuning editor" hand-off (design §4.2): when the Guiding Assistant
  // hands us a recommended settings body, open the drawer and seed the editors
  // with it (snake→camel for the param sub-dicts) so the expert can hand-tune
  // the recommendation before saving. Reuses the SAME AlgoParams editor below.
  useEffect(() => {
    if (!seed) return;
    const camel = (p: Record<string, number>): GuideAlgorithmParamDefaults => {
      const out = {} as GuideAlgorithmParamDefaults;
      for (const [k, v] of Object.entries(p)) out[toCamelKey(k)] = v;
      return out;
    };
    if (isValidRaAlgorithm(seed.ra_algorithm)) setRa(seed.ra_algorithm);
    if (isValidDecAlgorithm(seed.dec_algorithm)) setDec(seed.dec_algorithm);
    setRaParams(camel(seed.ra_params));
    setDecParams(camel(seed.dec_params));
    if (isValidDecGuideMode(seed.dec_guide_mode)) setDecMode(seed.dec_guide_mode);
    setBlcMs(String(seed.blc_pulse_ms));
    setLoaded(true);
    setOpen(true); // the drawer must be OPEN before there is anything to focus
    setJump(true);
  }, [seed]);

  useEffect(() => {
    if (!jump || !open) return;
    setJump(false);
    panelRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    // preventScroll: the smooth scroll above owns the movement — focus()'s own
    // instant jump would fight it. A viewer's selects are natively disabled and
    // so unfocusable; fall back to the panel itself (tabIndex={-1}) so the jump
    // is never silent for a screen reader.
    const first = firstControlRef.current;
    if (first && !first.disabled) first.focus({ preventScroll: true });
    else panelRef.current?.focus({ preventScroll: true });
  }, [jump, open]);

  // UX #24: same treatment for the tuning drawer's own gates. The <select>s
  // below keep the native attribute (there is no `readOnly` for a select and
  // the substitute belongs in ui.tsx as a primitive), so the drawer states its
  // read-only reason ONCE, as text, at the top of the open editor.
  const tuningReadOnlyReason = canGuide
    ? null
    : `Read-only session — ${accessPhrase("control.guide")} required to change guide tuning`;
  // Save PUTs every field this drawer holds, including the per-axis params. Until
  // the GET has landed those fields are FACTORY DEFAULTS, not the rig's — so a
  // Save before (or instead of) a successful load silently replaces a tuned axis
  // with the dossier defaults. Hold it until we know what we would be replacing.
  const saveReason =
    tuningReadOnlyReason
    ?? (!loaded
      ? (loadError
        ? `Couldn't read the saved tuning (${loadError}) — saving now would replace it with factory defaults`
        : "Reading the saved tuning…")
      : busy ? "Saving the last change…" : null);
  const clearCalReason =
    tuningReadOnlyReason
    ?? (!connected ? "No guider is connected"
      : busy ? "Saving the last change…" : null);

  const save = async () => {
    setBusy(true);
    try {
      // build + validate the full settings (clamps params, rejects a bad axis,
      // clamps the BLC pulse, snaps an invalid Dec direction to auto) before
      // PUTting the persisted fields.
      const v = validateGuideSettings({
        ra: { algorithm: ra, params: raParams },
        dec: { algorithm: dec, params: decParams },
        decGuideMode: decMode,
        blcPulseMs: Number(blcMs), // NaN/blank -> clampBlcPulse floors to 0
      });
      await api.put("/api/guide/settings", {
        ra_algorithm: v.ra.algorithm,
        dec_algorithm: v.dec.algorithm,
        ra_params: toSnake(v.ra.params),
        dec_params: toSnake(v.dec.params),
        dec_guide_mode: v.decGuideMode,
        blc_pulse_ms: v.blcPulseMs,
      });
      // reflect any clamp validateGuideSettings applied back into the inputs
      setRaParams(v.ra.params);
      setDecParams(v.dec.params);
      onToast("success", "Guide tuning saved — applies on the next start");
    } catch (e) {
      onToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={panelRef} tabIndex={-1} className="outline-none">
    <Panel title="Guide Tuning"
      right={
        <button className="btn text-[11px] !py-0.5 !px-2" onClick={toggle}
          aria-expanded={open}>
          {open ? "Hide" : "Edit"}
        </button>
      }>
      {!open ? (
        <p className="text-xs text-dim">
          Per-axis guide algorithm, Dec direction, and backlash pulse (native
          guider). Tap Edit to change.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          {tuningReadOnlyReason && <LockedNote reason={tuningReadOnlyReason} />}
          {/* Until the GET lands, every field below is a FACTORY DEFAULT, not
              what the rig is running — say so where the user is about to read
              them, not only under a Save they may never press. */}
          {!loaded && (
            loadError ? (
              <div className="flex flex-wrap items-center gap-2">
                <p className="text-[11px] text-warn leading-snug flex-1 min-w-[200px]">
                  Couldn&rsquo;t read the saved tuning ({loadError}). These are
                  factory defaults, not your rig&rsquo;s settings — Save is held
                  back so it can&rsquo;t replace them.
                </p>
                <button className="btn tap min-h-[44px] !px-3 text-[11px]"
                  onClick={() => void load()}>
                  Try again
                </button>
              </div>
            ) : (
              <p className="text-[11px] text-dim leading-snug">
                Reading the saved tuning from the rig…
              </p>
            )
          )}
          <label className="flex flex-col gap-1">
            <span className="label">RA algorithm</span>
            {/* Same rule as the provider override: no `readOnly` exists for a
                <select>, so the locked state shows its value through the house
                stand-in instead of a greyed control with no reachable reason.
                `firstControlRef` is then null and the focus-jump effect falls
                back to the panel, which is what its comment already expects. */}
            {tuningReadOnlyReason ? (
              <LockedChip reason={tuningReadOnlyReason} className="btn w-full">
                {RA_GUIDE_ALGORITHMS.find((o) => o.value === ra)?.label ?? ra}
              </LockedChip>
            ) : (
              <select ref={firstControlRef} className="field" value={ra}
                disabled={busy}
                onChange={(e) => chooseRa(e.target.value as GuideAlgorithmKind)}>
                {RA_GUIDE_ALGORITHMS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            )}
            <AlgoParams kind={ra} params={raParams} onChange={setRaParams} disabled={!canGuide} />
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Dec algorithm</span>
            {tuningReadOnlyReason ? (
              <LockedChip reason={tuningReadOnlyReason} className="btn w-full">
                {DEC_GUIDE_ALGORITHMS.find((o) => o.value === dec)?.label ?? dec}
              </LockedChip>
            ) : (
              <select className="field" value={dec} disabled={busy}
                onChange={(e) => chooseDec(e.target.value as GuideAlgorithmKind)}>
                {DEC_GUIDE_ALGORITHMS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            )}
            <AlgoParams kind={dec} params={decParams} onChange={setDecParams} disabled={!canGuide} />
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Dec guide direction</span>
            {tuningReadOnlyReason ? (
              <LockedChip reason={tuningReadOnlyReason} className="btn w-full">
                {DEC_GUIDE_MODES.find((o) => o.value === decMode)?.label ?? decMode}
              </LockedChip>
            ) : (
              <select className="field" value={decMode} disabled={busy}
                onChange={(e) => setDecMode(e.target.value as DecGuideMode)}>
                {DEC_GUIDE_MODES.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            )}
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Dec backlash pulse (ms)</span>
            {/* The one text input in this drawer still carrying the native
                attribute. Same rule as every sibling field: `readOnly` blocks
                the edit just as hard but keeps the box focusable and announced
                ("read only"), instead of deleting it — and the reason stated at
                the top of the editor — from the accessibility tree (UX #24). */}
            <input className="field" value={blcMs}
              readOnly={!canGuide || busy} aria-readonly={!canGuide || busy || undefined}
              inputMode="numeric" placeholder="0"
              onChange={(e) => setBlcMs(e.target.value)} />
          </label>

          <div className="flex gap-2 mt-1">
            <HonestButton className="btn btn-accent flex-1 min-h-11" reason={saveReason}
              onExplain={(r) => onToast("info", r)}
              onClick={() => void save()}>
              Save
            </HonestButton>
            <HonestButton className="btn min-h-11" reason={clearCalReason}
              onExplain={(r) => onToast("info", r)}
              onClick={() => void (async () => {
                try {
                  // The route reports whether a file was actually removed, and
                  // the two outcomes are different facts: a guider with no
                  // profile key (or a profile that has never calibrated) has
                  // nothing to delete, and toasting "Cleared" at it claimed an
                  // effect that did not happen.
                  const r = await api.del<{ cleared?: boolean }>("/api/guide/calibration");
                  // The Calibration panel above describes the ENGINE's
                  // calibration, which this delete does not touch — hand it the
                  // outcome so it can say which copy went instead of leaving a
                  // green LED to be read as "nothing happened".
                  onCalibrationChanged?.(!!r?.cleared);
                  onToast("info", r?.cleared
                    ? "Cleared the saved calibration — the guider keeps the one "
                      + "it is holding until guiding stops"
                    : "Nothing to clear — this profile has no saved calibration");
                } catch (e) {
                  onToast("error", (e as Error).message);
                }
              })()}>
              Clear Calibration
            </HonestButton>
          </div>
          {/* Was `title=` on the button — moved to text, which a tablet can read. */}
          <p className="text-[11px] text-dim leading-snug">
            Clear Calibration discards the saved calibration so the next start
            recalibrates.
          </p>
          {/* The drawer already states the read-only blocker once at the top; a
              second identical line here would be noise (see `distinct` above). */}
          {clearCalReason && clearCalReason !== tuningReadOnlyReason && (
            <LockedNote reason={clearCalReason} />
          )}
          <p className="text-[11px] text-faint leading-snug">
            Per-axis parameters start at the PHD2-default set (dossier §15) and
            are editable above — swapping an algorithm resets its params back
            to that default. Dec guide direction and the backlash pulse are
            saved here too and apply to the native guider on its next start.
          </p>
        </div>
      )}
    </Panel>
    </div>
  );
}

// PRO-12 Tier 2 (T7): per-axis algorithm-tunable editor. `params` is the
// caller-owned draft (seeded from GUIDE_ALGORITHM_DEFAULTS[kind] — see
// GuideSettingsDrawer's chooseRa/chooseDec); `onChange` merges a single
// edited key back in, mirroring the Dither/BLC numeric-field idiom above
// (Number(value) || 0 — a non-numeric/blank edit-in-progress falls back to 0
// rather than propagating NaN into state). `validateGuideSettings` (called in
// `save()`) is the single source of clamp truth for every param here — this
// component does not re-implement aggression/hysteresis/min-move bounds.
//
// `disabled` (viewer without `control.guide`) uses the honest-disabled idiom
// (spec §11.8): the reason stays visible and announced instead of a plain
// greyed-out input, so NEVER the native `disabled` attribute — it strips the
// element from the a11y tree along with the reason.
//
// The inputs are `readOnly`, not merely wrapped. `pointer-events-none` on the
// wrapper is a MOUSE-only guard: a keyboard user could Tab straight into these
// fields and type, `onChange` fired, and the draft mutated. Save is unreachable
// for a viewer so nothing ever persisted — which is the worst shape of all, an
// editor that accepts your edits and silently discards them. `readOnly` is the
// right primitive and does not conflict with §11.8: unlike `disabled` it keeps
// the field focusable and in the a11y tree (announced "read only"), which is
// the exact property that rule exists to protect. Pointer events stay ON so the
// field can be focused and its reason heard.
function AlgoParams({ kind, params, onChange, disabled }: {
  kind: GuideAlgorithmKind;
  params: GuideAlgorithmParamDefaults;
  onChange: (next: GuideAlgorithmParamDefaults) => void;
  disabled: boolean;
}) {
  const reason = disabled
    ? `${accessPhrase("control.guide")} required to edit ${kind} parameters`
    : null;
  return (
    <>
      <div
        className={`flex flex-wrap gap-x-3 gap-y-1 mt-1 ${disabled ? "opacity-50" : ""}`}
        aria-disabled={disabled || undefined}
      >
        {Object.entries(params).map(([k, v]) => (
          <label key={k} className="flex flex-col gap-0.5">
            <span className="label !text-[9px]">{k}</span>
            <input
              className="field !py-1 !text-[11px] w-20"
              inputMode="decimal"
              value={v}
              readOnly={disabled}
              aria-readonly={disabled || undefined}
              aria-label={reason ? `${kind} ${k} — ${reason}` : undefined}
              onChange={(e) => onChange({ ...params, [k]: Number(e.target.value) || 0 })}
            />
          </label>
        ))}
      </div>
      {reason && <LockedNote reason={reason} className="mt-1" />}
    </>
  );
}

// ----------------------------------------------------------- guiding assistant
// The Guiding Assistant panel (design 2026-07-24 §4). A THIN render shell: all
// copy + the apply-payload builder + the selective-apply merge live in the pure
// lib/guideAssistant.ts. Novice = one Run button → progress → a plain-language
// summary card + one "Apply recommended settings" button. Advanced = a collapsed
// <details> with raw curves (reusing GuideScatter/GuideGraph), numeric
// measurements, a per-recommendation before/after table with a per-field
// checkbox for selective apply, and "Open in tuning editor" (hands the params to
// the existing GuideSettingsDrawer). Honest-disabled (§11.8) for no-guider /
// non-native / already-guiding / viewer.
function GuideAssistantPanel({ canGuide, connected, onToast, onOpenInTuning,
  onCalibrationChanged }: {
  canGuide: boolean;
  connected: boolean;
  onToast: ToastFn;
  onOpenInTuning: (body: GuideSettingsPutBody) => void;
  /** Tell the Calibration panel above to re-read; `clearedSaved` says whether a
   *  persisted calibration was actually removed (see the drawer's copy). */
  onCalibrationChanged?: (clearedSaved: boolean) => void;
}) {
  const providers = useProviders();
  const status = useStatus();
  const guide = useGuide();
  const progress = useGuideAssistant();
  const [report, setReport] = useState<AssistantReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [includeBacklash, setIncludeBacklash] = useState(true);
  const clearProgress = useStore((s) => s.clearGuideAssistant);

  // The RUN outlives this component. It is a 2–4 minute background task on the
  // rig, and `running` used to be a local boolean set beside the POST — so a
  // reload or a tab switch mid-run came back to the Run button (which then
  // 409'd), with no bar, no Stop, and no way to reach the report the run was
  // about to produce. The server publishes the `guide_assistant` lane on every
  // status frame; ask it instead. `arm()` only covers the ≤2s before the first
  // frame carries the lane.
  const { busy: running, arm: armRun } = useBusyOrPending("guide_assistant");

  const kind = providers?.guide?.kind;
  const isNative = kind === "astrodeck" || kind === "sim";
  const guiding = !!(guide?.guiding ?? status?.guider?.guiding);
  // A start in flight holds the guider's start lock for its whole star-search +
  // calibration walk, with `guiding` still false. The assistant needs that same
  // lock, so a Run pressed here does not refuse — it QUEUES behind the walk and
  // then fails minutes later with "stop guiding before running the Guiding
  // Assistant", long after the user has stopped watching.
  const guideStarting = useBusy("guide");

  // Honest-disabled reason (§11.8) — first blocking condition wins.
  const reason = !canGuide
    ? `${accessPhrase("control.guide")} required to run the Guiding Assistant`
    : !connected
      ? "connect a guider first"
      : !isNative
        ? "the Guiding Assistant works with the AstroDeck native guider"
        : guiding
          ? "stop guiding first"
          : guideStarting
            ? "guiding is starting — the assistant needs the mount to itself"
            : null;
  const blocked = reason !== null;

  // The "done" tick is what says a report EXISTS to fetch, so it is honoured
  // whatever this component believes about who started the run — the mid-run
  // reload above comes back with no local state at all, and its report arrives
  // on exactly this tick. Deduped by tick IDENTITY (the store hands out a fresh
  // object per tick) rather than by a flag, so a retained "done" cannot re-fetch
  // on every unrelated re-render, and a second RUN's "done" is not mistaken for
  // the first's. `run()` also clears the retained tick before its POST, so a
  // stale "done" can never paint last run's numbers as if they were fresh.
  const fetchedTick = useRef<unknown>(null);
  useEffect(() => {
    if (progress?.phase !== "done" || fetchedTick.current === progress) return;
    fetchedTick.current = progress;
    api.get<{ report: AssistantReport | null }>("/api/guide/assistant/report")
      .then((r) => {
        setReport(r.report);
        if (r.report) {
          setSelected(new Set(
            r.report.recommendations.filter((x) => !x.advanced).map((x) => x.key)));
        }
      })
      .catch((e) => onToast("error", (e as Error).message));
  }, [progress, onToast]);

  const run = async () => {
    if (blocked || running) return;
    setReport(null);
    // Drop any retained tick from a PREVIOUS run (done or error) first — see
    // the effect above.
    clearProgress();
    try {
      await api.post("/api/guide/assistant/start",
        { include_backlash: includeBacklash });
      // Only after the POST is accepted: arming on a 409/403 would show a
      // progress bar for a run that never started.
      armRun();
    } catch (e) {
      onToast("error", (e as Error).message);
    }
  };

  // `stopping` is dropped by the RIG, not by the request: the POST resolves in
  // ~40ms (it only sets a cancel flag), while the run keeps pulsing the mount
  // until it reaches its next cancellation check. Clearing it in a `finally`
  // made "Stopping…" and the "checks between pulses" note underneath describe
  // the round trip — they blinked for one frame and the button went back to
  // reading "Stop" over a run that was still going, which is the exact defect
  // the rest of this wave is fixing. The lane going quiet is the run actually
  // being over, and it cannot latch: when the lane clears the whole block
  // unmounts anyway, and this resets it for the next run.
  useEffect(() => { if (!running) setStopping(false); }, [running]);

  const stop = async () => {
    if (stopping) return;
    setStopping(true);
    try {
      await api.post("/api/guide/assistant/stop");
      // Deliberately NOT setting anything to "idle" here. The rig is still
      // pulsing the mount until the run reaches its next cancellation check, and
      // it publishes a terminal tick when it gets there. A stop that never
      // landed (403, dropped link) used to be swallowed whole: the panel went
      // back to idle over a live run, and dropping `running` also cancelled the
      // effect above — so the report that run went on to produce was discarded.
    } catch (e) {
      // The stop never reached the rig, so nothing is stopping: say so and give
      // the button back rather than leaving "Stopping…" over a live run.
      setStopping(false);
      onToast("error", (e as Error).message);
    }
  };

  const apply = async (keys?: string[]) => {
    if (!report || busy) return;
    setBusy(true);
    try {
      const body = buildApplyBody(report, keys);
      const changesAlgo = applyChangesAlgorithm(report, keys);
      // D4 (reworked after review): applying NEVER discards the saved
      // calibration on its own — losing it mid-night costs a full calibration
      // walk on the next Start Guiding, and the old code did it silently from
      // the one-tap accent button. The novice path states the consequence above
      // the button and leaves the calibration alone; the advanced selective
      // path offers it through the app's own themed, focus-trapped confirm,
      // whose dismiss/unavailable answer is KEEP (the old native confirm()
      // fell back to `?? true` — destroying the calibration by default).
      let clearCal = false;
      if (changesAlgo && keys) {
        clearCal = await confirmDialog({
          title: "Clear the saved calibration?",
          body: "You changed a guiding algorithm. You can keep the calibration "
            + "you already have, or clear it so the mount re-learns which way is "
            + "which — that adds about 2 minutes the next time guiding starts.",
          confirmLabel: "Clear it",
          cancelLabel: "Keep it",
          tone: "warn",
        });
      }
      await api.put("/api/guide/settings", body);
      if (clearCal) {
        // The route answers whether a file was actually removed, and an
        // undeletable one still comes back 200 — so "cleared" was a claim, not
        // an observation. Report what happened, the way the drawer's own
        // handler does, and refresh the Calibration panel either way.
        let cleared = false;
        let clearError: string | null = null;
        try {
          const r = await api.del<{ cleared?: boolean }>("/api/guide/calibration");
          cleared = !!r?.cleared;
        } catch (e) {
          clearError = (e as Error).message;
        }
        onCalibrationChanged?.(cleared);
        if (clearError) {
          onToast("warning",
            "Recommended settings applied, but the saved calibration could not "
            + `be cleared (${clearError}) — guiding will reuse it. Try Clear `
            + "Calibration in Guide Tuning below.");
        } else if (cleared) {
          onToast("info",
            "Recommended settings applied — saved calibration cleared; it " +
            "recalibrates on the next guiding start");
        } else {
          onToast("info",
            "Recommended settings applied. There was no saved calibration to " +
            "clear, so the next start calibrates from scratch anyway.");
        }
      } else {
        onToast("success",
          "Recommended guide settings applied — they take effect on the next " +
          "guiding start");
      }
    } catch (e) {
      onToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  // Same-field recommendations are mutually exclusive (the default vs its
  // advanced alternative) — ticking one unticks the other instead of letting
  // apply-order silently pick the winner.
  const toggleKey = (key: string) => setSelected((prev) =>
    report ? toggleRecommendationKey(report, prev, key) : prev);

  const summary = report ? summarize(report) : null;
  const rows = report ? formatRecommendations(report) : [];
  const toneClass = { good: "text-good", warn: "text-warn", bad: "text-bad" };
  // The report is the handover out of the progress bar: a run short enough to
  // finish between two status frames never puts its lane on one, so `running`
  // would otherwise sit on the local latch until it expires, with a full bar
  // over a result that is already in hand.
  // A run that FAILED (no star, cancelled, device error) publishes a terminal
  // {phase:"error", message} tick, and that tick is the other handover out of
  // the bar. A refusal that lands inside the first two seconds — parked mount,
  // no star — arrives while `running` is still the local `arm()` latch, so
  // without this the panel showed a full progress bar captioned with the error
  // text, over a live Stop, for the whole 6s grace before the failure card
  // appeared. `run()` clears the retained tick before its POST, so this can
  // only ever be THIS run's error.
  const showRunning = running && !report && progress?.phase !== "error";
  const failed = !showRunning && !report && progress?.phase === "error";
  // The server message is a DeviceError string prefixed with the driver name —
  // strip that so the card reads as a sentence to the user.
  const failMessage = (progress?.message ?? "")
    .replace(/^native guider:\s*/i, "")
    .replace(/^Guiding Assistant\s*/i, "");
  const changesAlgoAll = report ? applyChangesAlgorithm(report) : false;

  // Honest-disabled (§11.8) reasons for the RESULT-CARD actions — same rule the
  // Run button above already follows. These three used the native `disabled`
  // attribute, which drops the control out of the accessibility tree together
  // with the reason, and on the tablet left a grey rectangle that did nothing
  // under a fingertip and said nothing about why. Precedence lives in the pure
  // helper; the permission phrase is the only view-side input.
  const noPermission = !canGuide
    ? `${accessPhrase("control.guide")} required to apply guide settings`
    : null;
  const explain = (msg: string) => onToast("info", msg);
  const applyReason = applyActionReason({ noPermissionReason: noPermission, busy });
  // Start over only discards the local result — no permission needed for that.
  const startOverReason = applyActionReason({ busy });
  const applySelectedReason = applyActionReason({
    noPermissionReason: noPermission, busy, selectedCount: selected.size,
  });

  return (
    <Panel title="Guiding Assistant" right={<ProviderBadge cap="guide" />}>
      {/* Novice: one Run button (honest-disabled), progress, then a summary. */}
      {showRunning ? (
        <div className="flex flex-col gap-2">
          <div className="h-2 rounded bg-line overflow-hidden">
            <div className="h-full bg-accent transition-all"
              style={{ width: `${progress?.pct ?? 0}%` }} />
          </div>
          <p className="text-xs text-dim">
            {/* Between a reload and the next progress tick there is no message
                to show, and "Working…" over an empty bar is the least a user
                needs to know the rig is still on it. */}
            {progress?.message ?? "Working… (started before this page loaded)"}
          </p>
          <button className="btn" onClick={() => void stop()} aria-busy={stopping}>
            {stopping ? "Stopping…" : "Stop"}
          </button>
          {stopping && (
            <p className="text-[11px] text-dim leading-snug">
              The run checks for this between pulses, so it can take a few
              seconds to come to a halt.
            </p>
          )}
        </div>
      ) : failed ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-bad leading-snug">
            The Guiding Assistant stopped: {failMessage || "something went wrong."}
          </p>
          <p className="text-[11px] text-dim leading-snug">
            Nothing was changed. Check that a star is visible in the guide camera
            and that the mount is tracking, then try again.
          </p>
          <button className="btn btn-accent w-full" onClick={() => void run()}>
            Try again
          </button>
        </div>
      ) : !report ? (
        <div className="flex flex-col gap-2">
          {/* Same honest-disabled rule, via the shared token now that this file
              uses it below — the reason is stated in the paragraph underneath,
              which is why the redundant (and touch-invisible) title= is gone. */}
          <div {...lockedProps(reason)}>
            <button className="btn btn-accent w-full" onClick={() => void run()}>
              <Icon name="guide" size={14} className="inline -mt-0.5 mr-1" />
              Run Guiding Assistant
            </button>
          </div>
          <p className="text-[11px] text-dim leading-snug">
            {blocked
              ? reason
              : includeBacklash
                ? "This takes 2–4 minutes. AstroDeck watches a guide star, then "
                  + "deliberately nudges the mount up and down a few times to "
                  + "measure its slack. Make sure the scope can move freely."
                : "This takes about 2 minutes. AstroDeck watches a guide star "
                  + "drift and recommends guide settings. The mount keeps "
                  + "tracking and is not moved."}
          </p>
          <label className="flex items-center gap-2 text-[11px] text-dim">
            <Toggle checked={includeBacklash} onChange={setIncludeBacklash}
              label="Also measure mount slack (moves the scope)" />
            <span>Also measure mount slack (moves the scope)</span>
          </label>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {summary && (
            <p className={`text-sm leading-snug ${toneClass[summary.tone]}`}>
              {summary.headline}
            </p>
          )}
          {/* The consequence goes ABOVE the button, and applying never discards
              the saved calibration on its own (review fix). */}
          {changesAlgoAll && (
            <p className="text-[11px] text-dim leading-snug">
              This changes the guiding algorithm. Your saved calibration is kept,
              so guiding still starts straight away — if it behaves oddly
              afterwards, clear the calibration and let the mount re-learn its
              directions (about 2 minutes).
            </p>
          )}
          <div className="flex gap-2">
            <HonestButton className="btn btn-accent flex-1" reason={applyReason}
              onExplain={explain} onClick={() => void apply()}>
              {busy ? "Applying…" : "Apply recommended settings"}
            </HonestButton>
            {/* "Redo" was a lie: this discards the measurements, it does not
                re-measure. Pressing it returns to the Run screen. */}
            <HonestButton className="btn" reason={startOverReason}
              onExplain={explain} onClick={() => setReport(null)}>
              Start over
            </HonestButton>
          </div>
          {noPermission && <LockedNote reason={noPermission} />}

          {/* Advanced disclosure — collapsed by default, zero novice clutter.
              The house aria-expanded/44px/caret row (ui.tsx Disclosure), not a
              native <details>: that rendered a different caret and ignored the
              44px tap floor. */}
          <Disclosure className="border-t border-line pt-2"
            summary="Advanced · measurements and per-setting apply"
            label="the advanced measurements, raw curves, and per-setting apply">
            <div className="flex flex-col gap-3 mt-2">
              <div className="flex justify-center">
                <GuideScatter samples={report.samples} />
              </div>
              <GuideGraph samples={report.samples} />

              <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                <Meas label="RMS RA" px={report.measurements.rms_ra_px}
                  as={report.measurements.rms_ra_arcsec} />
                <Meas label="RMS Dec" px={report.measurements.rms_dec_px}
                  as={report.measurements.rms_dec_arcsec} />
                <Meas label="RMS total" px={report.measurements.rms_total_px}
                  as={report.measurements.rms_total_arcsec} />
                <Meas label="drift/min" px={report.measurements.drift_per_min_px}
                  as={report.measurements.drift_per_min_arcsec} />
                <div className="flex justify-between"><span className="label">PE p-p</span>
                  <span className="mono tabular-nums">
                    {(report.measurements.pe_amplitude_px * 2).toFixed(2)} px
                    {report.measurements.pe_period_s != null
                      ? ` · ~${report.measurements.pe_period_s.toFixed(0)}s` : ""}
                  </span></div>
                <div className="flex justify-between"><span className="label">jitter</span>
                  <span className="mono tabular-nums">{report.measurements.jitter_px.toFixed(2)} px</span></div>
                <div className="flex justify-between col-span-2 gap-2">
                  <span className="label">backlash</span>
                  <span className="mono tabular-nums text-right">
                    {report.measurements.backlash.bl_ms} ± {report.measurements.backlash.sigma_ms.toFixed(0)} ms
                    {/* a sentence, not the raw BL_* enum — and y_rate_source is
                        internal provenance the user cannot act on. */}
                    <span className="block text-faint text-[10px] font-sans">
                      {backlashResultSentence(report.measurements.backlash.result_code)}
                    </span>
                  </span></div>
              </div>

              {/* Per-recommendation before→after with a checkbox for selective apply. */}
              <div className="flex flex-col gap-1 border-t border-line pt-2">
                {rows.map((r) => (
                  // Toggle, not a bare <input type=checkbox>: the UA checkbox is
                  // a white box that turns system-blue, and on :root.night that
                  // is the only non-red, high-luminance thing on the screen.
                  <div key={r.key} className="flex items-start gap-2 text-[11px]">
                    <Toggle
                      checked={selected.has(r.key)}
                      onChange={() => toggleKey(r.key)}
                      label={`Include: ${r.label}`}
                    />
                    <span className="flex-1">
                      <span className="font-medium">{r.label}</span>
                      {r.advanced && <span className="text-warn"> · advanced</span>}
                      {r.confidence === "low" && (
                        <span className="text-warn"> · low confidence</span>
                      )}
                      <span className="text-dim"> · {String(r.current)} → </span>
                      <span className="mono">{String(r.recommended)}{r.unit}</span>
                      <span className="block text-faint">{r.rationale}</span>
                      {r.conflicts.length > 0 && (
                        <span className="block text-faint">
                          Either/or with the other {FIELD_WORD[r.field] ?? r.field}
                          {" "}suggestion — ticking this one unticks it.
                        </span>
                      )}
                    </span>
                  </div>
                ))}
              </div>

              <div className="flex flex-col gap-1.5">
                <div className="flex gap-2">
                  <HonestButton className="btn flex-1" reason={applySelectedReason}
                    onExplain={explain} onClick={() => void apply([...selected])}>
                    Apply selected
                  </HonestButton>
                  <button type="button" className="btn"
                    onClick={() => onOpenInTuning(buildApplyBody(report,
                      selected.size ? [...selected] : undefined))}>
                    Open in tuning editor
                  </button>
                </div>
                {/* The empty-selection case had NO stated reason at all — the
                    button was simply grey. Say it in words, on screen, so it
                    reaches touch as well as keyboard. */}
                {!noPermission && !busy && selected.size === 0 && (
                  <p className="flex items-start gap-1.5 text-[11px] text-dim leading-snug">
                    <Icon name="info" size={12} className="mt-0.5 shrink-0" aria-hidden />
                    <span>{EMPTY_SELECTION_REASON}</span>
                  </p>
                )}
                <p className="text-[11px] text-faint leading-snug">
                  Open in tuning editor jumps to Guide Tuning below and fills it
                  in — nothing is saved until you press Save there.
                </p>
              </div>
            </div>
          </Disclosure>
        </div>
      )}
    </Panel>
  );
}

/** A button that is honest about being inert (house rule §11.8). The native
 *  `disabled` attribute is never used for a control the user could plausibly
 *  want to press: it removes the element from the accessibility tree, taking
 *  the reason with it, and leaves a grey rectangle that does nothing on tap and
 *  cannot even be focused to ask why. Instead this dims (the one shared
 *  LOCKED_CLASS token), carries `aria-disabled`, stays focusable AND tappable
 *  (`!pointer-events-auto`, the same escape LockedChip uses), and its press
 *  STATES the reason instead of acting. Callers pair it with a visible reason
 *  line so the reason also reaches a sighted user who never presses it. */
function HonestButton({ reason, onClick, onExplain, className = "btn", children }: {
  reason: string | null;
  onClick: () => void;
  onExplain: (reason: string) => void;
  className?: string;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className={`${className} ${reason ? `${LOCKED_CLASS} !pointer-events-auto` : ""}`}
      aria-disabled={reason ? true : undefined}
      onClick={() => (reason ? onExplain(reason) : onClick())}
    >
      {children}
    </button>
  );
}

/** Plain-language name for a guide field, used by the either/or note on rows
 *  that share a field (RA algorithm: Hysteresis vs Predictive PEC). */
const FIELD_WORD: Record<string, string> = {
  ra_algorithm: "RA algorithm",
  dec_algorithm: "Dec algorithm",
};

/** One numeric measurement row: shows arcsec when the image scale is known,
 *  else raw pixels (UX-15 — never label pixels as arcsec). */
function Meas({ label, px, as }: { label: string; px: number; as: number | null }) {
  return (
    <div className="flex justify-between">
      <span className="label">{label}</span>
      <span className="mono tabular-nums">
        {as != null ? `${as.toFixed(2)}″` : `${px.toFixed(2)} px`}
      </span>
    </div>
  );
}
