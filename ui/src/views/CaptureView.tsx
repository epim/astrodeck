import { useState } from "react";
import { api } from "../api";
import { useStore, useStatus } from "../store";
import { LivePreview } from "../components/preview/LivePreview";
import { Field, Panel, Stat, Toggle } from "../components/ui";
import { HELP } from "../help";

export default function CaptureView() {
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const [exposure, setExposure] = useState("2");
  const [gain, setGain] = useState("120");
  const [offset, setOffset] = useState("30");
  const [binning, setBinning] = useState("1");
  const [save, setSave] = useState(false);
  const [target, setTarget] = useState("");
  const [coolerTarget, setCoolerTarget] = useState("-10");
  const [dew, setDew] = useState(0);

  const cam = status?.camera;
  const looping = !!status?.looping;
  const body = {
    exposure_s: Number(exposure) || 1,
    gain: Number(gain) || 0,
    offset: Number(offset) || 0,
    binning: Number(binning) || 1,
    save,
    target,
  };

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_320px] xl:grid-cols-[1fr_360px]">
      {/* live-preview overhaul: stage + zoom/pan + stretch + overlays + filmstrip */}
      <LivePreview />

      <div className="flex flex-col gap-4">
        {/* ------------------------------------------------- exposure ctl */}
        <Panel title="Exposure">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Exposure (s)">
              <input className="field" value={exposure} onChange={(e) => setExposure(e.target.value)} />
            </Field>
            <Field label={`Gain${cam?.max_gain ? ` (max ${cam.max_gain})` : ""}`}>
              <input className="field" value={gain} onChange={(e) => setGain(e.target.value)} />
            </Field>
            <Field label="Offset" hint={HELP.offset}>
              <input className="field" value={offset} onChange={(e) => setOffset(e.target.value)} />
            </Field>
            <Field label="Binning" hint={HELP.binning}>
              <select className="field" value={binning} onChange={(e) => setBinning(e.target.value)}>
                {[1, 2, 4].map((b) => <option key={b} value={b}>{b}×{b}</option>)}
              </select>
            </Field>
          </div>
          <div className="flex items-center gap-3 mt-3">
            <Toggle checked={save} onChange={setSave} />
            <span className="text-xs text-dim">save FITS to library</span>
          </div>
          {save && (
            <div className="mt-2">
              <Field label="Target name">
                <input className="field" placeholder="M42" value={target}
                  onChange={(e) => setTarget(e.target.value)} />
              </Field>
            </div>
          )}
          <div className="grid grid-cols-3 gap-2 mt-4">
            <button className="btn btn-accent tap-lg min-h-[56px]" disabled={looping}
              onClick={() => act(() => api.post("/api/capture", body))}>
              Single
            </button>
            <button className="btn tap-lg min-h-[56px]" disabled={looping}
              onClick={() => act(() => api.post("/api/capture/loop", body))}>
              Loop
            </button>
            {/* Stop is urgent -> stays 1-tap (R9), enlarged for touch. */}
            <button className="btn btn-danger tap-lg min-h-[56px]"
              onClick={() => act(() => api.post("/api/capture/stop"))}>
              Stop
            </button>
          </div>
          {looping && (
            <p className="text-[11px] text-accent mt-2 blink tracking-widest uppercase">● looping</p>
          )}
        </Panel>

        {/* histogram + stretch now live inside <LivePreview/> (the stretch is
            coupled to the image, so a decoupled read-only histogram would lie) */}

        {/* ------------------------------------------------------- filter */}
        {status?.filterwheel && (
          <Panel title="Filter Wheel">
            <div className="flex flex-wrap gap-2">
              {status.filterwheel.names.map((name, i) => (
                <button key={name}
                  className={`btn tap min-h-[44px] !px-3 min-w-[56px] ${i === status.filterwheel!.position ? "btn-accent" : ""}`}
                  onClick={() => act(() => api.post("/api/filterwheel/position", { position: i }))}>
                  {name}
                </button>
              ))}
            </div>
          </Panel>
        )}

        {/* ------------------------------------------------------- cooler */}
        {cam?.can_cool && (
          <Panel title="Cooler">
            <div className="flex items-end flex-wrap gap-3">
              <Stat label="sensor" value={cam.temperature?.toFixed(1) ?? "—"} unit="°C"
                tone={cam.temperature != null && cam.temperature < 0 ? "good" : undefined} />
              <Field label="Target °C" hint={HELP.coolTo}>
                <input className="field !w-20" value={coolerTarget}
                  onChange={(e) => setCoolerTarget(e.target.value)} />
              </Field>
              <button className="btn tap min-h-[44px]"
                onClick={() => act(() => api.post("/api/camera/cooler", { on: true, target_c: Number(coolerTarget) }))}>
                Cool
              </button>
              <button className="btn tap min-h-[44px]"
                onClick={() => act(() => api.post("/api/camera/cooler", { on: false }))}>
                Warm
              </button>
            </div>
            {cam.has_dew_heater && (
              <div className="mt-4 border-t border-line pt-3">
                <div className="flex justify-between mb-1.5">
                  <span className="label">dew heater</span>
                  <span className="mono text-xs text-accent">{dew}%</span>
                </div>
                <input type="range" min={0} max={100} value={dew}
                  aria-label="dew heater power"
                  className="w-full h-11 accent-(--accent) cursor-pointer touch-none"
                  onChange={(e) => { setDew(Number(e.target.value)); }}
                  onMouseUp={() => act(() => api.post("/api/camera/dew-heater", { power: dew }))}
                  onTouchEnd={() => act(() => api.post("/api/camera/dew-heater", { power: dew }))} />
              </div>
            )}
          </Panel>
        )}
      </div>
    </div>
  );
}
