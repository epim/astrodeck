import { useState } from "react";
import { api } from "../api";
import { useStore, useStatus, useGuide } from "../store";
import { GuideGraph, GuideScatter } from "../components/graphs";
import { Panel, Stat } from "../components/ui";
import { useCanControlGuide } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import ProviderBadge from "../components/ProviderBadge";
import GuideFramePreview from "../components/GuideFramePreview";
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
  const stats = guide ?? status?.guider ?? null;
  const connected = !!status?.guider || !!guide;

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
      <Panel title="Guide Error · arcsec"
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
          <Stat label='RMS RA' value={stats ? stats.rms_ra.toFixed(2) : "—"} unit='"' />
          <Stat label='RMS Dec' value={stats ? stats.rms_dec.toFixed(2) : "—"} unit='"' />
          <Stat label='RMS Total' value={stats ? stats.rms_total.toFixed(2) : "—"} unit='"'
            tone={stats && stats.rms_total > 0 ? (stats.rms_total < 1 ? "good" : stats.rms_total < 2 ? "warn" : "bad") : undefined} />
          <Stat label="SNR" value={stats ? stats.snr.toFixed(0) : "—"} />
        </div>
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
            <button className="btn btn-accent" disabled={!canGuide || !connected || stats?.guiding}
              onClick={() => act(() => api.post("/api/guide/start"))}>
              ❖ Start Guiding
            </button>
            <button className="btn" disabled={!canGuide || !connected || !stats?.guiding}
              onClick={() => act(() => api.post("/api/guide/stop"))}>
              Stop
            </button>
            <button className="btn" disabled={!canGuide || !connected}
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
                onClick={() => act(() => api.post("/api/guide/dither", { pixels: Number(ditherPx) || 3 }))}>
                Dither
              </button>
            </div>
          </div>
        </Panel>

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
