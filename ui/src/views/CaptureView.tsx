import { useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { Histogram } from "../components/graphs";
import { Field, Panel, Stat, Toggle } from "../components/ui";

export default function CaptureView() {
  const { status, preview, showToast } = useStore();
  const [exposure, setExposure] = useState("2");
  const [gain, setGain] = useState("120");
  const [offset, setOffset] = useState("30");
  const [binning, setBinning] = useState("1");
  const [save, setSave] = useState(false);
  const [target, setTarget] = useState("");
  const [coolerTarget, setCoolerTarget] = useState("-10");

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
    <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
      {/* ------------------------------------------------------- preview */}
      <Panel title="Live Preview" className="min-h-[420px]"
        right={preview && (
          <span className="mono text-[11px] text-dim">
            {preview.width}×{preview.height} · {preview.exposure_s}s · gain {preview.gain} · bin {preview.binning}
          </span>
        )}>
        <div className="relative bg-black/60 border border-line flex items-center justify-center min-h-[380px] overflow-hidden">
          {preview ? (
            <img src={`/api/preview/${preview.id}.png`} alt="latest frame"
              className="astro max-w-full max-h-[62vh] object-contain" />
          ) : (
            <div className="text-dim text-xs tracking-[0.3em] uppercase py-32 text-center">
              <div className="text-3xl mb-3 opacity-40">◉</div>
              no frame yet — take an exposure
            </div>
          )}
          <div className="crosshair" />
        </div>
        {preview && (
          <div className="grid grid-cols-5 gap-3 mt-3">
            <Stat label="min" value={preview.stats.min} />
            <Stat label="median" value={preview.stats.median} />
            <Stat label="mean" value={preview.stats.mean} />
            <Stat label="max" value={preview.stats.max}
              tone={preview.stats.max >= 65535 ? "warn" : undefined} />
            <Stat label="σ" value={preview.stats.std} />
          </div>
        )}
      </Panel>

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
            <Field label="Offset">
              <input className="field" value={offset} onChange={(e) => setOffset(e.target.value)} />
            </Field>
            <Field label="Binning">
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
            <button className="btn btn-accent" disabled={looping}
              onClick={() => act(() => api.post("/api/capture", body))}>
              Single
            </button>
            <button className="btn" disabled={looping}
              onClick={() => act(() => api.post("/api/capture/loop", body))}>
              Loop
            </button>
            <button className="btn btn-danger"
              onClick={() => act(() => api.post("/api/capture/stop"))}>
              Stop
            </button>
          </div>
          {looping && (
            <p className="text-[11px] text-accent mt-2 blink tracking-widest uppercase">● looping</p>
          )}
        </Panel>

        {/* ---------------------------------------------------- histogram */}
        <Panel title="Histogram">
          {preview ? <Histogram data={preview.histogram} /> :
            <p className="text-dim text-xs">awaiting first frame</p>}
        </Panel>

        {/* ------------------------------------------------------- filter */}
        {status?.filterwheel && (
          <Panel title="Filter Wheel">
            <div className="flex flex-wrap gap-1.5">
              {status.filterwheel.names.map((name, i) => (
                <button key={name}
                  className={`btn !px-3 ${i === status.filterwheel!.position ? "btn-accent" : ""}`}
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
            <div className="flex items-end gap-3">
              <Stat label="sensor" value={cam.temperature?.toFixed(1) ?? "—"} unit="°C"
                tone={cam.temperature != null && cam.temperature < 0 ? "good" : undefined} />
              <Field label="Target °C">
                <input className="field !w-20" value={coolerTarget}
                  onChange={(e) => setCoolerTarget(e.target.value)} />
              </Field>
              <button className="btn"
                onClick={() => act(() => api.post("/api/camera/cooler", { on: true, target_c: Number(coolerTarget) }))}>
                Cool
              </button>
              <button className="btn"
                onClick={() => act(() => api.post("/api/camera/cooler", { on: false }))}>
                Warm
              </button>
            </div>
          </Panel>
        )}
      </div>
    </div>
  );
}
