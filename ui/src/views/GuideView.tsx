import { useEffect, useState } from "react";
import type { CalibrationReport } from "../types";
import { api, ApiError } from "../api";
import { setProvidersConfig } from "../api/backends";
import {
  useStore, useStatus, useGuide, useConfig, useProviders, useGuideRmsByKind,
} from "../store";
import { GuideGraph, GuideScatter } from "../components/graphs";
import { Panel, Stat, Led } from "../components/ui";
import { useCanControlGuide, useCanConfigBackend, accessPhrase } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import ProviderBadge from "../components/ProviderBadge";
import GuideFramePreview from "../components/GuideFramePreview";
import { DEFAULT_PROVIDERS } from "../components/equipment/TasksPanel";
import { compareRmsWindows } from "../lib/rmsCompare";
import { selectGuideWindows } from "../lib/guideRms";
import {
  RA_GUIDE_ALGORITHMS,
  DEC_GUIDE_ALGORITHMS,
  GUIDE_ALGORITHM_DEFAULTS,
  defaultGuideSettings,
  validateGuideSettings,
  isValidRaAlgorithm,
  isValidDecAlgorithm,
  type GuideAlgorithmKind,
} from "../lib/guideSettings";

export default function GuideView() {
  const status = useStatus();
  const guide = useGuide();
  const showToast = useStore((s) => s.showToast);
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
  const unit = isArcsec ? '"' : "px";
  const unitWord = isArcsec ? "arcsec" : "px";

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
            {stats?.guiding && (
              <span className="text-good text-[11px] tracking-widest uppercase">● guiding</span>
            )}
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
        {stats && !isArcsec && (
          /* UX-15: raw pixels, not arcsec — tell the user why and how to fix it. */
          <p className="text-[11px] text-dim mt-2 leading-snug">
            RMS is in guide-camera pixels. Set the guide scope's focal length in
            Optics to report arcsec.
          </p>
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
          <div className="flex flex-col gap-2">
            <button className="btn btn-accent" disabled={!canGuide || !connected || stats?.guiding || acting}
              onClick={() => act(() => api.post("/api/guide/start"))}>
              ❖ Start Guiding
            </button>
            <button className="btn" disabled={!canGuide || !connected || !stats?.guiding}
              onClick={() => act(() => api.post("/api/guide/stop"))}>
              Stop
            </button>
            <button className="btn" disabled={!canGuide || !connected || acting}
              onClick={() => act(async () => {
                await api.post("/api/guide/calibrate");
                showToast("info", "Recalibrating — a fresh calibration is running");
              })}>
              Force Recalibrate
            </button>
            <div className="grid grid-cols-[1fr_auto] gap-2 items-end mt-2">
              <label className="flex flex-col gap-1">
                <span className="label">Dither (px)</span>
                <input className="field" value={ditherPx} disabled={!canGuide}
                  onChange={(e) => setDitherPx(e.target.value)} />
              </label>
              <button className="btn" disabled={!canGuide || !connected || !stats?.guiding}
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
              </button>
            </div>
            <div className="grid grid-cols-3 gap-2 mt-1">
              <label className="flex flex-col gap-1">
                <span className="label !text-[9px]">settle px</span>
                <input className="field !py-1" placeholder="1.5" value={settlePixels}
                  disabled={!canGuide} inputMode="decimal"
                  onChange={(e) => setSettlePixels(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="label !text-[9px]">settle s</span>
                <input className="field !py-1" placeholder="8" value={settleTime}
                  disabled={!canGuide} inputMode="decimal"
                  onChange={(e) => setSettleTime(e.target.value)} />
              </label>
              <label className="flex flex-col gap-1">
                <span className="label !text-[9px]">timeout s</span>
                <input className="field !py-1" placeholder="60" value={settleTimeout}
                  disabled={!canGuide} inputMode="decimal"
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

        <GuideProviderPanel onToast={showToast} />

        <GuideSettingsDrawer canGuide={canGuide} connected={connected} onToast={showToast} />
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

  const seed = config?.providers?.guide ?? "auto";
  const [draft, setDraft] = useState(seed);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

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
          <select
            className="field"
            value={draft}
            disabled={!canConfig || busy}
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
        </label>
        {choice?.reason && <p className="text-[11px] text-dim leading-snug">{choice.reason}</p>}
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

function GuideSettingsDrawer({ canGuide, connected, onToast }: {
  canGuide: boolean;
  connected: boolean;
  onToast: ToastFn;
}) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [ra, setRa] = useState<GuideAlgorithmKind>(defaultGuideSettings().ra.algorithm);
  const [dec, setDec] = useState<GuideAlgorithmKind>(defaultGuideSettings().dec.algorithm);

  const load = async () => {
    try {
      const s = await api.get<{ ra_algorithm: string; dec_algorithm: string }>("/api/guide/settings");
      if (isValidRaAlgorithm(s.ra_algorithm)) setRa(s.ra_algorithm);
      if (isValidDecAlgorithm(s.dec_algorithm)) setDec(s.dec_algorithm);
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

  const save = async () => {
    setBusy(true);
    try {
      // build + validate the full settings (clamps params, rejects a bad axis)
      // before PUTting the two algorithm kinds the server persists.
      const v = validateGuideSettings({
        ra: { algorithm: ra, params: { ...GUIDE_ALGORITHM_DEFAULTS[ra] } },
        dec: { algorithm: dec, params: { ...GUIDE_ALGORITHM_DEFAULTS[dec] } },
        // This drawer edits only the algorithm kinds today; the static BLC
        // pulse keeps its disabled default until a settings editor surfaces it.
        blcPulseMs: 0,
      });
      await api.put("/api/guide/settings", {
        ra_algorithm: v.ra.algorithm,
        dec_algorithm: v.dec.algorithm,
      });
      onToast("success", "Guide algorithm saved — applies on the next start");
    } catch (e) {
      onToast("error", (e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Guide Algorithm"
      right={
        <button className="btn text-[11px] !py-0.5 !px-2" onClick={toggle}
          aria-expanded={open}>
          {open ? "Hide" : "Edit"}
        </button>
      }>
      {!open ? (
        <p className="text-xs text-dim">
          Per-axis guide algorithm (native guider). Tap Edit to change RA / Dec.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          <label className="flex flex-col gap-1">
            <span className="label">RA algorithm</span>
            <select className="field" value={ra} disabled={!canGuide || busy}
              onChange={(e) => setRa(e.target.value as GuideAlgorithmKind)}>
              {RA_GUIDE_ALGORITHMS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <AlgoParams kind={ra} />
          </label>

          <label className="flex flex-col gap-1">
            <span className="label">Dec algorithm</span>
            <select className="field" value={dec} disabled={!canGuide || busy}
              onChange={(e) => setDec(e.target.value as GuideAlgorithmKind)}>
              {DEC_GUIDE_ALGORITHMS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
            <AlgoParams kind={dec} />
          </label>

          <div className="flex gap-2 mt-1">
            <button className="btn btn-accent flex-1" disabled={!canGuide || busy}
              onClick={() => void save()}>
              Save
            </button>
            <button className="btn" disabled={!canGuide || !connected || busy}
              title="Discard the saved calibration so the next start recalibrates"
              onClick={async () => {
                try {
                  await api.del("/api/guide/calibration");
                  onToast("info", "Cleared saved calibration");
                } catch (e) {
                  onToast("error", (e as Error).message);
                }
              }}>
              Clear Calibration
            </button>
          </div>
          <p className="text-[11px] text-faint leading-snug">
            Parameters shown are the PHD2-default set (dossier §15); the algorithm
            selection applies to the native guider on its next start.
          </p>
        </div>
      )}
    </Panel>
  );
}

function AlgoParams({ kind }: { kind: GuideAlgorithmKind }) {
  const params = GUIDE_ALGORITHM_DEFAULTS[kind];
  return (
    <span className="text-[11px] text-dim flex flex-wrap gap-x-3 gap-y-0.5 mt-0.5">
      {Object.entries(params).map(([k, v]) => (
        <span key={k}>{k}: <span className="text-fg">{v}</span></span>
      ))}
    </span>
  );
}
