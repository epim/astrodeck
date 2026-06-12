import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { Field, Panel, Stat, Toggle } from "../components/ui";
import type { CatalogEntry, ExposureStep, SequencePlan, Target } from "../types";

const DEFAULT_STEP: ExposureStep = {
  filter: null, exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 10, frame_type: "Light",
};

const DEFAULT_PLAN: SequencePlan = {
  name: "Tonight", targets: [], guide: true, dither_every: 3, dither_pixels: 3,
  autofocus_every: 0, park_when_done: false, warm_cooler_when_done: false,
};

function loadPlan(): SequencePlan {
  try {
    const raw = localStorage.getItem("astrodeck-plan");
    if (raw) return JSON.parse(raw) as SequencePlan;
  } catch { /* fall through */ }
  return DEFAULT_PLAN;
}

export default function SequenceView() {
  const { status, sequence, showToast } = useStore();
  const [plan, setPlan] = useState<SequencePlan>(loadPlan);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);

  useEffect(() => {
    localStorage.setItem("astrodeck-plan", JSON.stringify(plan));
  }, [plan]);

  useEffect(() => {
    if (!search) { setResults([]); return; }
    const t = setTimeout(async () => {
      try { setResults((await api.get<CatalogEntry[]>(`/api/catalog?q=${encodeURIComponent(search)}`)).slice(0, 6)); }
      catch { /* ignore */ }
    }, 250);
    return () => clearTimeout(t);
  }, [search]);

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };

  const filters = status?.filterwheel?.names ?? [];
  const running = sequence.state === "running" || sequence.state === "paused";
  const totalFrames = plan.targets.reduce((a, t) => a + t.steps.reduce((b, s) => b + s.count, 0), 0);
  const totalMinutes = plan.targets.reduce(
    (a, t) => a + t.steps.reduce((b, s) => b + s.count * s.exposure_s, 0), 0) / 60;

  const addTarget = (e: CatalogEntry) => {
    setPlan({
      ...plan,
      targets: [...plan.targets, {
        name: e.id, ra_hours: e.ra_hours, dec_deg: e.dec_deg,
        center: true, autofocus_first: true, steps: [{ ...DEFAULT_STEP }],
      }],
    });
    setSearch("");
  };

  const patchTarget = (ti: number, patch: Partial<Target>) =>
    setPlan({ ...plan, targets: plan.targets.map((t, i) => (i === ti ? { ...t, ...patch } : t)) });

  const patchStep = (ti: number, si: number, patch: Partial<ExposureStep>) =>
    patchTarget(ti, {
      steps: plan.targets[ti].steps.map((s, i) => (i === si ? { ...s, ...patch } : s)),
    });

  const num = (v: string, fallback: number) => {
    const n = Number(v);
    return Number.isFinite(n) && v !== "" ? n : fallback;
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[1fr_320px]">
      <div className="flex flex-col gap-4">
        {/* --------------------------------------------------- progress */}
        {(running || sequence.state === "complete") && sequence.progress && (
          <Panel title={`Sequence · ${sequence.plan_name ?? ""}`}
            right={<span className={`text-[11px] tracking-widest uppercase ${
              sequence.state === "running" ? "text-good blink" :
              sequence.state === "paused" ? "text-warn" : "text-accent"}`}>
              {sequence.state}
            </span>}>
            <div className="progress-track mb-2">
              <div className="progress-fill" style={{ width: `${sequence.progress.percent}%` }} />
            </div>
            <div className="flex justify-between text-xs mono text-dim">
              <span>{sequence.detail}</span>
              <span>
                {sequence.progress.frames_done}/{sequence.progress.frames_total} frames
                · {Math.floor(sequence.progress.elapsed_s / 60)}m elapsed
              </span>
            </div>
            <div className="flex gap-2 mt-3">
              {sequence.state === "running" && (
                <button className="btn" onClick={() => act(() => api.post("/api/sequence/pause"))}>Pause</button>
              )}
              {sequence.state === "paused" && (
                <button className="btn btn-accent" onClick={() => act(() => api.post("/api/sequence/resume"))}>Resume</button>
              )}
              {running && (
                <button className="btn btn-danger" onClick={() => act(() => api.post("/api/sequence/abort"))}>Abort</button>
              )}
            </div>
          </Panel>
        )}

        {/* ----------------------------------------------------- targets */}
        <Panel title="Targets"
          right={
            <div className="relative">
              <input className="field !w-56" placeholder="+ add target — search catalog"
                value={search} onChange={(e) => setSearch(e.target.value)} />
              {results.length > 0 && (
                <div className="absolute right-0 top-full mt-1 w-72 panel z-10 max-h-60 overflow-y-auto">
                  {results.map((r) => (
                    <button key={r.id} onClick={() => addTarget(r)}
                      className="w-full text-left px-3 py-2 text-xs hover:bg-raise transition-colors flex justify-between cursor-pointer">
                      <span><span className="mono text-accent">{r.id}</span> {r.name}</span>
                      <span className={`mono ${r.alt > 40 ? "text-good" : r.alt < 20 ? "text-warn" : "text-dim"}`}>
                        {r.alt.toFixed(0)}°
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          }>
          {plan.targets.length === 0 && (
            <p className="text-dim text-xs py-6 text-center tracking-widest uppercase">
              empty plan — search the catalog above to add targets
            </p>
          )}
          <div className="flex flex-col gap-4">
            {plan.targets.map((t, ti) => (
              <div key={ti} className="border border-line bg-bg/50 p-3">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="font-display font-semibold text-accent tracking-wider">{t.name}</span>
                  <span className="mono text-[11px] text-dim">
                    {t.ra_hours.toFixed(3)}h {t.dec_deg >= 0 ? "+" : ""}{t.dec_deg.toFixed(2)}°
                  </span>
                  <label className="flex items-center gap-1.5 text-[11px] text-dim">
                    <Toggle checked={t.center} onChange={(v) => patchTarget(ti, { center: v })} /> center
                  </label>
                  <label className="flex items-center gap-1.5 text-[11px] text-dim">
                    <Toggle checked={t.autofocus_first} onChange={(v) => patchTarget(ti, { autofocus_first: v })} /> AF
                  </label>
                  <div className="flex-1" />
                  <button className="btn !py-0.5 !px-2 !text-[10px]" disabled={running}
                    onClick={() => patchTarget(ti, { steps: [...t.steps, { ...DEFAULT_STEP }] })}>
                    + step
                  </button>
                  <button className="btn btn-danger !py-0.5 !px-2 !text-[10px]" disabled={running}
                    onClick={() => setPlan({ ...plan, targets: plan.targets.filter((_, i) => i !== ti) })}>
                    ✕
                  </button>
                </div>
                <div className="mt-2 flex flex-col gap-1.5">
                  {t.steps.map((s, si) => (
                    <div key={si} className="grid grid-cols-[90px_70px_60px_50px_60px_auto] gap-2 items-center">
                      <select className="field !py-1" value={s.filter ?? ""}
                        onChange={(e) => patchStep(ti, si, { filter: e.target.value || null })}>
                        <option value="">no filter</option>
                        {filters.map((f) => <option key={f} value={f}>{f}</option>)}
                      </select>
                      <input className="field !py-1" title="exposure seconds" value={s.exposure_s}
                        onChange={(e) => patchStep(ti, si, { exposure_s: num(e.target.value, s.exposure_s) })} />
                      <input className="field !py-1" title="gain" value={s.gain}
                        onChange={(e) => patchStep(ti, si, { gain: num(e.target.value, s.gain) })} />
                      <select className="field !py-1" title="binning" value={s.binning}
                        onChange={(e) => patchStep(ti, si, { binning: Number(e.target.value) })}>
                        {[1, 2, 4].map((b) => <option key={b} value={b}>{b}×</option>)}
                      </select>
                      <input className="field !py-1" title="frame count" value={s.count}
                        onChange={(e) => patchStep(ti, si, { count: Math.max(1, Math.round(num(e.target.value, s.count))) })} />
                      <div className="flex items-center gap-2">
                        <span className="mono text-[10px] text-dim whitespace-nowrap">
                          {((s.count * s.exposure_s) / 60).toFixed(0)}m
                        </span>
                        <button className="text-dim hover:text-bad text-xs cursor-pointer" disabled={running}
                          onClick={() => patchTarget(ti, { steps: t.steps.filter((_, i) => i !== si) })}>
                          ✕
                        </button>
                      </div>
                    </div>
                  ))}
                  <div className="grid grid-cols-[90px_70px_60px_50px_60px_auto] gap-2 label !text-[9px]">
                    <span>filter</span><span>exp s</span><span>gain</span><span>bin</span><span>count</span><span />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {/* ------------------------------------------------------ options */}
      <div className="flex flex-col gap-4">
        <Panel title="Plan">
          <Field label="Plan name">
            <input className="field" value={plan.name}
              onChange={(e) => setPlan({ ...plan, name: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3 mt-4">
            <Stat label="frames" value={totalFrames} />
            <Stat label="integration" value={`${Math.floor(totalMinutes / 60)}h ${Math.round(totalMinutes % 60)}m`} />
          </div>
        </Panel>

        <Panel title="Automation">
          <div className="flex flex-col gap-3 text-xs">
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">guide during sequence</span>
              <Toggle checked={plan.guide} onChange={(v) => setPlan({ ...plan, guide: v })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">dither every N frames</span>
              <input className="field !w-16 !py-1" value={plan.dither_every}
                onChange={(e) => setPlan({ ...plan, dither_every: Math.max(0, Math.round(num(e.target.value, plan.dither_every))) })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">refocus every N frames</span>
              <input className="field !w-16 !py-1" value={plan.autofocus_every}
                onChange={(e) => setPlan({ ...plan, autofocus_every: Math.max(0, Math.round(num(e.target.value, plan.autofocus_every))) })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">park mount when done</span>
              <Toggle checked={plan.park_when_done} onChange={(v) => setPlan({ ...plan, park_when_done: v })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">warm camera when done</span>
              <Toggle checked={plan.warm_cooler_when_done} onChange={(v) => setPlan({ ...plan, warm_cooler_when_done: v })} />
            </label>
          </div>
        </Panel>

        <button className="btn btn-accent !py-3 !text-sm" disabled={running || totalFrames === 0}
          onClick={() => act(() => api.post("/api/sequence/start", plan))}>
          ≡ Run Sequence
        </button>
        {totalFrames === 0 && (
          <p className="text-[11px] text-dim text-center">add targets and steps first</p>
        )}
      </div>
    </div>
  );
}
