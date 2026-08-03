import { useEffect, useRef, useState, type ReactNode } from "react";
import type { CalibrationReport } from "../types";
import { api, ApiError } from "../api";
import { clearProfileOverrides, setProvidersConfig } from "../api/backends";
import {
  useStore, useStatus, useGuide, useConfig, useProviders, useGuideRmsByKind,
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
import { useCanControlGuide, useCanConfigBackend, accessPhrase } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import ProviderBadge from "../components/ProviderBadge";
import GuideFramePreview from "../components/GuideFramePreview";
import { DEFAULT_PROVIDERS } from "../components/equipment/TasksPanel";
import { OverrideNote, useClearOverride } from "../components/OverrideNote";
import { entryOf, isProfileOverride, providerKey } from "../lib/effective";
import { compareRmsWindows } from "../lib/rmsCompare";
import { selectGuideWindows } from "../lib/guideRms";
import { guideNarration } from "../lib/guideNarration";
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
  // (px vs ″) instead of stamping "arcsec" on raw pixels. Absent flag ⇒ arcsec
  // (older payload / the prior default) — only an explicit false means px.
  const isArcsec = stats?.is_arcsec !== false;
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

  // UX-23: fetch the guider's calibration report so a bad/flipped calibration is
  // visible before it runs the mount away from the star. Refetch when guiding
  // (re)starts — a fresh calibration completes on start / Force Recalibrate.
  const [calReport, setCalReport] = useState<CalibrationReport | null>(null);
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
  const startReason =
    guideReadOnlyReason ?? noGuiderReason
    ?? (stats?.guiding ? "Guiding is already running"
      : acting ? "Still working on the last command" : null);
  const stopReason =
    guideReadOnlyReason ?? noGuiderReason
    ?? (!stats?.guiding ? "Guiding isn't running — there is nothing to stop" : null);
  const calibrateReason =
    guideReadOnlyReason ?? noGuiderReason
    ?? (acting ? "Still working on the last command" : null);
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
  }, [connected, stats?.guiding]);

  return (
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
              onClick={() => act(() => api.post("/api/guide/start"))}>
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
                showToast("info", "Recalibrating — a fresh calibration is running");
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
          onToast={showToast} onOpenInTuning={setTuningSeed} />

        <GuideProviderPanel onToast={showToast} />

        <GuideSettingsDrawer canGuide={canGuide} connected={connected}
          onToast={showToast} seed={tuningSeed} />
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
// head-to-head RMS comparison. Mirrors the Equipment "Tasks" panel's
// provider-override mechanics EXACTLY (components/equipment/TasksPanel.tsx):
// same global config write (`POST /api/config/providers`, `config.backend`-
// gated — not `control.guide`, since it's a backend/connect-shape decision
// like the other three task overrides, not a guide-safety one), same
// optimistic-draft-then-revert-on-error pattern, same "Auto (best available)"
// + ProviderBadge/reason-line idiom. `ProvidersConfig.guide` rides the SAME
// per-profile snapshot the other three overrides already do (Profile.providers,
// EquipmentView's doSaveProfile/doLoadProfile spread the whole object) — no
// new profile plumbing was needed for that half of the brief.
//
// The RMS comparison reads store.ts's `guideRmsByKind` (tagged at bus-ingest
// time from `status.providers.guide.kind`, since the "guide" bus channel
// itself carries no provider field) and hands the native-family window
// ("astrodeck", or "sim" on a sim rig — both run the SAME NativeGuider engine,
// see providers.py::_resolve_guide) and the PHD2/NINA-family window
// ("backend") to the pure lib/rmsCompare.ts helper.
// Friendly labels for the guide-provider override VALUES. The option VOCABULARY
// itself comes from the server (`status.providers.guide.eligible`) so the
// dropdown only ever offers what actually applies to the connected rig — the
// same "only offer what's eligible" rule TasksPanel follows (review I1). A bare
// "sim" pin is no longer offered (it was a no-op; the server never lists it).
const GUIDE_PROVIDER_LABELS: Record<string, string> = {
  auto: "Auto (best available)",
  astrodeck: "AstroDeck native",
  backend: "PHD2 / NINA bridge",
  // Legacy: "sim" was offered pre-fix-round and may persist in an old profile
  // snapshot; the server never lists it as eligible anymore (it behaved like
  // Auto), so it only ever appears as the sticky stored-value option.
  sim: "Simulator (legacy — same as Auto)",
};
const guideProviderLabel = (value: string): string =>
  GUIDE_PROVIDER_LABELS[value] ?? value;

function GuideProviderPanel({ onToast }: { onToast: ToastFn }) {
  const config = useConfig();
  const providers = useProviders();
  const canConfig = useCanConfigBackend();
  const rmsByKind = useGuideRmsByKind();

  // #129: the fourth pinnable capability, and it had the same bug as the other
  // three — this seeded from `config.providers.guide`, the GLOBAL block, which
  // the ACTIVE PROFILE beats inside providers.override_with_layer. Read the
  // WINNING value; keep the global one underneath only as the bootstrap
  // fallback, since the WS `hello` config carries no provenance block.
  const guideEntry = entryOf(config, providerKey("guide"));
  const guidePinned = isProfileOverride(guideEntry);
  const seed =
    (typeof guideEntry?.value === "string" && guideEntry.value) ||
    config?.providers?.guide ||
    "auto";
  const [draft, setDraft] = useState(seed);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const { clear, clearing, error: clearErr } = useClearOverride();

  // Re-seed whenever a fresh config lands (our own save, another client's, or
  // a profile activate/load restoring its snapshot) — same idiom as
  // TasksPanel's persist-and-reseed effect.
  useEffect(() => {
    setDraft(seed);
  }, [seed]);

  const persist = async (value: string) => {
    if (busy) return;
    setErr(null);
    setBusy(true);
    const next = { ...DEFAULT_PROVIDERS, ...(config?.providers ?? {}), guide: value };
    setDraft(value); // optimistic — echoed back by loadConfig() below
    try {
      await setProvidersConfig(next);
      await useStore.getState().loadConfig();
      onToast("success", "Guide provider override saved");
    } catch (e) {
      setDraft(seed); // revert the optimistic edit
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "config.backend required to change the guide provider"
            : e.message
          : e instanceof Error
            ? e.message
            : "couldn't save the guide provider override";
      setErr(msg);
    } finally {
      setBusy(false);
    }
  };

  const choice = providers?.guide;
  // Options the connected rig actually supports (server-resolved); "auto" is
  // always present. The stored draft stays listed even if it's momentarily not
  // eligible (disconnected rig) so it never silently vanishes — TasksPanel's
  // sticky-option idiom.
  const eligible = choice?.eligible ?? ["auto"];
  const options = eligible.includes("auto") ? eligible : ["auto", ...eligible];
  const draftInList = options.includes(draft);
  const { native: nativeWindow, backend: phd2Window } = selectGuideWindows(rmsByKind);
  const cmp = compareRmsWindows(nativeWindow, phd2Window);
  const cmpTone =
    cmp.verdict === "insufficient-data" ? "text-dim" : cmp.verdict === "comparable" ? "text-dim" : "text-good";

  return (
    <Panel title="Guide Provider" right={<ProviderBadge cap="guide" />}>
      <div className="flex flex-col gap-2.5">
        <label className="flex flex-col gap-1">
          <span className="label">Provider override</span>
          {/* UX #24: a <select> has no `readOnly`, and the native `disabled`
              attribute would drop the control and its reason out of the a11y
              tree. Without the capability, show the value through the house
              locked stand-in instead. (`busy` keeps the native attribute: it
              lasts one round-trip and has no reason worth reading.) */}
          {canConfig ? (
            <select
              className="field"
              value={draft}
              // Under a profile pin this select writes the global block, which
              // the profile then beats — a save that succeeds and changes
              // nothing that runs. Report instead of pretending; the note below
              // carries the way out. Same rule as TasksPanel.
              disabled={busy || guidePinned}
              onChange={(e) => void persist(e.target.value)}
              aria-label="Guide provider override"
            >
              {options.map((v) => (
                <option key={v} value={v}>
                  {guideProviderLabel(v)}
                </option>
              ))}
              {/* sticky: a stored value not currently eligible (e.g. a
                  disconnected rig) stays listed rather than vanishing */}
              {!draftInList && (
                <option value={draft}>{guideProviderLabel(draft)}</option>
              )}
            </select>
          ) : (
            <LockedChip
              reason={`Guide provider override — ${accessPhrase("config.backend")} required`}
              className="btn w-full">
              {guideProviderLabel(draft)}
            </LockedChip>
          )}
        </label>
        {choice?.reason && <p className="text-[11px] text-dim leading-snug">{choice.reason}</p>}
        <OverrideNote
          entry={guideEntry}
          format={(v) => guideProviderLabel(String(v))}
          clearLabel="Clear the profile pin"
          clearHint="Clearing it hands this dropdown back."
          clearing={clearing}
          error={clearErr}
          onClear={
            canConfig && guideEntry?.profile_id
              ? () =>
                  void clear(() =>
                    clearProfileOverrides(guideEntry.profile_id as string, {
                      providers: ["guide"],
                    }),
                  )
              : undefined
          }
        />
        <p className="text-[11px] text-dim leading-snug">
          A switch takes effect at the next guiding start (it never swaps a
          running guider).
        </p>
        {!canConfig && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            Read-only — changing the guide provider needs {accessPhrase("config.backend")}.
          </p>
        )}
        {err && <p className="text-[11px] text-bad">{err}</p>}

        <div className="border-t border-line pt-2.5 mt-0.5">
          <span className="label">Same-night RMS: native vs. PHD2</span>
          <p className={`text-[11px] mt-1 leading-snug ${cmpTone}`}>{cmp.message}</p>
        </div>
      </div>
    </Panel>
  );
}

function GuideSettingsDrawer({ canGuide, connected, onToast, seed }: {
  canGuide: boolean;
  connected: boolean;
  onToast: ToastFn;
  seed?: GuideSettingsPutBody | null;
}) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
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
      }>("/api/guide/settings");
      if (isValidRaAlgorithm(s.ra_algorithm)) chooseRa(s.ra_algorithm);
      if (isValidDecAlgorithm(s.dec_algorithm)) chooseDec(s.dec_algorithm);
      if (isValidDecGuideMode(s.dec_guide_mode ?? "")) setDecMode(s.dec_guide_mode as DecGuideMode);
      if (typeof s.blc_pulse_ms === "number") setBlcMs(String(s.blc_pulse_ms));
      setLoaded(true);
    } catch (e) {
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
      for (const [k, v] of Object.entries(p)) {
        out[k.replace(/_([a-z])/g, (_m, c) => c.toUpperCase())] = v;
      }
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
  const saveReason = tuningReadOnlyReason ?? (busy ? "Saving the last change…" : null);
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
                  await api.del("/api/guide/calibration");
                  onToast("info", "Cleared saved calibration");
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
function GuideAssistantPanel({ canGuide, connected, onToast, onOpenInTuning }: {
  canGuide: boolean;
  connected: boolean;
  onToast: ToastFn;
  onOpenInTuning: (body: GuideSettingsPutBody) => void;
}) {
  const providers = useProviders();
  const status = useStatus();
  const guide = useGuide();
  const progress = useGuideAssistant();
  const [report, setReport] = useState<AssistantReport | null>(null);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [includeBacklash, setIncludeBacklash] = useState(true);
  const clearProgress = useStore((s) => s.clearGuideAssistant);

  const kind = providers?.guide?.kind;
  const isNative = kind === "astrodeck" || kind === "sim";
  const guiding = !!(guide?.guiding ?? status?.guider?.guiding);

  // Honest-disabled reason (§11.8) — first blocking condition wins.
  const reason = !canGuide
    ? `${accessPhrase("control.guide")} required to run the Guiding Assistant`
    : !connected
      ? "connect a guider first"
      : !isNative
        ? "the Guiding Assistant works with the AstroDeck native guider"
        : guiding
          ? "stop guiding first"
          : null;
  const blocked = reason !== null;

  // Terminal ticks. "done" -> fetch the cached report and seed the advanced
  // selection with the non-advanced recommendation keys (the novice apply set);
  // "error" -> stop the bar and let the error card render the message.
  // `run()` clears the retained tick BEFORE the POST, so a "done" seen here can
  // only belong to THIS run (the previous run's tick used to latch instantly
  // and paint last run's numbers as if they were fresh).
  useEffect(() => {
    if (!running) return;
    if (progress?.phase === "error") {
      setRunning(false);
      return;
    }
    if (progress?.phase === "done") {
      api.get<{ report: AssistantReport | null }>("/api/guide/assistant/report")
        .then((r) => {
          setReport(r.report);
          if (r.report) {
            setSelected(new Set(
              r.report.recommendations.filter((x) => !x.advanced).map((x) => x.key)));
          }
        })
        .catch((e) => onToast("error", (e as Error).message))
        .finally(() => setRunning(false));
    }
  }, [progress?.phase, running, onToast]);

  const run = async () => {
    if (blocked || running) return;
    setReport(null);
    // Drop any retained tick from a PREVIOUS run (done or error) first — see
    // the effect above.
    clearProgress();
    try {
      await api.post("/api/guide/assistant/start",
        { include_backlash: includeBacklash });
      setRunning(true);
    } catch (e) {
      onToast("error", (e as Error).message);
    }
  };

  const stop = async () => {
    try { await api.post("/api/guide/assistant/stop"); } catch { /* ignore */ }
    setRunning(false);
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
        try { await api.del("/api/guide/calibration"); } catch { /* best effort */ }
        onToast("info",
          "Recommended settings applied — saved calibration cleared; it " +
          "recalibrates on the next guiding start");
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
  // A run that FAILED (no star, cancelled, device error) publishes a terminal
  // {phase:"error", message} tick — render it as a sentence with a way out
  // instead of leaving a half-filled bar reading "Watching…" forever.
  const failed = !running && !report && progress?.phase === "error";
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
      {running ? (
        <div className="flex flex-col gap-2">
          <div className="h-2 rounded bg-line overflow-hidden">
            <div className="h-full bg-accent transition-all"
              style={{ width: `${progress?.pct ?? 0}%` }} />
          </div>
          <p className="text-xs text-dim">{progress?.message ?? "Working…"}</p>
          <button className="btn" onClick={() => void stop()}>Stop</button>
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
