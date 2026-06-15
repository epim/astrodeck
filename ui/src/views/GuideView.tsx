import { useState } from "react";
import { api } from "../api";
import { useStore, useStatus, useGuide } from "../store";
import { GuideGraph, GuideScatter } from "../components/graphs";
import { Panel, Stat } from "../components/ui";

export default function GuideView() {
  const status = useStatus();
  const guide = useGuide();
  const showToast = useStore((s) => s.showToast);
  const [ditherPx, setDitherPx] = useState("3");
  const stats = guide ?? status?.guider ?? null;
  const connected = !!status?.guider || !!guide;

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_300px]">
      <Panel title="Guide Error · arcsec"
        right={stats?.guiding && (
          <span className="text-good text-[11px] tracking-widest uppercase">● guiding</span>
        )}>
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

        <Panel title="Control">
          {!connected && (
            <p className="text-xs text-warn mb-3">
              no guider — connect PHD2 or the simulator rig on the Rig page
            </p>
          )}
          <div className="flex flex-col gap-2">
            <button className="btn btn-accent" disabled={!connected || stats?.guiding}
              onClick={() => act(() => api.post("/api/guide/start"))}>
              ❖ Start Guiding
            </button>
            <button className="btn" disabled={!connected || !stats?.guiding}
              onClick={() => act(() => api.post("/api/guide/stop"))}>
              Stop
            </button>
            <div className="grid grid-cols-[1fr_auto] gap-2 items-end mt-2">
              <label className="flex flex-col gap-1">
                <span className="label">Dither (px)</span>
                <input className="field" value={ditherPx} onChange={(e) => setDitherPx(e.target.value)} />
              </label>
              <button className="btn" disabled={!connected || !stats?.guiding}
                onClick={() => act(() => api.post("/api/guide/dither", { pixels: Number(ditherPx) || 3 }))}>
                Dither
              </button>
            </div>
          </div>
        </Panel>
      </div>
    </div>
  );
}
