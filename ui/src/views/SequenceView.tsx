import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { Field, Panel, Stat, Toggle } from "../components/ui";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { humanizeSeqError } from "../lib/humanize";
import type { CatalogEntry, ExposureStep, Target } from "../types";

const DEFAULT_STEP: ExposureStep = {
  filter: null, exposure_s: 120, gain: 100, offset: 30, binning: 1, count: 10, frame_type: "Light",
};

/** State-tone badge with a shape glyph + word so it reads in night mode. */
function SeqStateBadge({ state }: { state: string }) {
  const map: Record<string, { icon: IconName; cls: string; word: string }> = {
    running: { icon: "play", cls: "text-good blink", word: "RUNNING" },
    paused: { icon: "pause", cls: "text-warn", word: "PAUSED" },
    complete: { icon: "check", cls: "text-good", word: "COMPLETE" },
    error: { icon: "x", cls: "text-bad", word: "ERROR" },
    aborted: { icon: "stop", cls: "text-bad", word: "ABORTED" },
  };
  const m = map[state] ?? { icon: "info" as IconName, cls: "text-accent", word: state.toUpperCase() };
  return (
    <span className={`flex items-center gap-1 text-[11px] tracking-widest uppercase ${m.cls}`}>
      <Icon name={m.icon} size={13} />
      {m.word}
    </span>
  );
}

export default function SequenceView() {
  const status = useStore((s) => s.status);
  const sequence = useStore((s) => s.sequence);
  const showToast = useStore((s) => s.showToast);
  const openLog = useStore((s) => s.openLog);
  // SSOT: the plan lives in the store (single writer of `astrodeck-plan`; setPlan
  // persists). No private useState / localStorage effect here.
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);
  const [recoverable, setRecoverable] =
    useState<{ name: string; frames_done: number; frames_total: number } | null>(null);

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

  const running = sequence.state === "running" || sequence.state === "paused";
  const finished = ["complete", "error", "aborted"].includes(sequence.state);
  const failed = sequence.state === "error" || sequence.state === "aborted";
  const showPanel = running || finished;        // NOT gated on progress

  // Resume-from-N is offered only when the backend says the run is recoverable;
  // a pre-first-frame failure has no resume file, so the button is simply absent.
  const resumable = !!recoverable && failed && sequence.state === "error";

  useEffect(() => {
    if (running) return;
    api.get<{ recoverable: boolean; name?: string; frames_done?: number; frames_total?: number }>(
      "/api/sequence/recoverable")
      .then((r) => setRecoverable(r.recoverable
        ? { name: r.name!, frames_done: r.frames_done!, frames_total: r.frames_total! } : null))
      .catch(() => { /* server not up */ });
  }, [running]);

  const filters = status?.filterwheel?.names ?? [];
  const totalFrames = plan.targets.reduce((a, t) => a + t.steps.reduce((b, s) => b + s.count, 0), 0);
  const totalMinutes = plan.targets.reduce(
    (a, t) => a + t.steps.reduce((b, s) => b + s.count * s.exposure_s, 0), 0) / 60;

  const addTarget = (e: CatalogEntry) => {
    setPlan({
      ...plan,
      targets: [...plan.targets, {
        name: e.id, ra_hours: e.ra_hours, dec_deg: e.dec_deg,
        center: true, autofocus_first: true, calibration: false, steps: [{ ...DEFAULT_STEP }],
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
        {/* -------------------------------- recover banner (no live panel) */}
        {recoverable && !showPanel && (
          <Panel title="Resume Interrupted Run">
            <div className="flex items-center gap-3">
              <span className="text-xs text-dim flex-1">
                “{recoverable.name}” stopped at {recoverable.frames_done}/{recoverable.frames_total} frames.
                Resume picks up where it left off.
              </span>
              <button className="btn btn-accent !py-1" onClick={() =>
                act(async () => { await api.post("/api/sequence/recover"); setRecoverable(null); })}>
                <Icon name="play" size={12} className="inline -mt-0.5 mr-1" /> Resume
              </button>
            </div>
          </Panel>
        )}
        {/* ------------------------------------------- run / status panel */}
        {showPanel && (
          <Panel title={`Sequence · ${sequence.plan_name ?? plan.name ?? ""}`}
            className={failed ? "!border-bad/60" : ""}
            right={<SeqStateBadge state={sequence.state} />}>

            {/* Failure banner — decoupled from progress so an early (pre-first-frame)
                failure still shows a clear, human reason. */}
            {failed && (
              <div className="flex items-start gap-2 border border-bad/50 bg-bad/5 px-3 py-2 mb-3">
                <Icon name={sequence.state === "error" ? "x" : "stop"} size={16}
                  className="text-bad mt-0.5 shrink-0" />
                <div className="min-w-0">
                  <p className="text-sm text-ink leading-snug">
                    {sequence.state === "error" ? "Sequence failed" : "Sequence aborted"}
                  </p>
                  <p className="text-xs text-ink/85 leading-snug mt-0.5">
                    {humanizeSeqError(sequence.detail)}
                  </p>
                  {sequence.detail && (
                    <p className="text-[10px] mono text-dim leading-snug mt-1 break-words">
                      {sequence.detail}
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Progress bar — only when a progress block actually exists. */}
            {sequence.progress && (
              <>
                <div className="progress-track mb-2">
                  <div className="progress-fill" style={{ width: `${sequence.progress.percent}%` }} />
                </div>
                <div className="flex justify-between text-xs mono text-dim">
                  <span>{!failed && sequence.detail}</span>
                  <span>
                    {sequence.progress.frames_done}/{sequence.progress.frames_total} frames
                    {sequence.progress.rejected ? ` · ${sequence.progress.rejected} rejected` : ""}
                    · {Math.floor(sequence.progress.elapsed_s / 60)}m elapsed
                  </span>
                </div>
              </>
            )}

            {/* Actions by state. Stop-type (Abort) is never disabled. */}
            <div className="flex flex-wrap gap-2 mt-3">
              {sequence.state === "running" && (
                <button className="btn" onClick={() => act(() => api.post("/api/sequence/pause"))}>Pause</button>
              )}
              {sequence.state === "paused" && (
                <button className="btn btn-accent" onClick={() => act(() => api.post("/api/sequence/resume"))}>Resume</button>
              )}
              {running && (
                <button className="btn btn-danger" onClick={() => act(() => api.post("/api/sequence/abort"))}>Abort</button>
              )}
              {failed && (
                <>
                  <button className="btn btn-accent" disabled={totalFrames === 0}
                    onClick={() => act(() => api.post("/api/sequence/start", plan))}>
                    <Icon name="play" size={12} className="inline -mt-0.5 mr-1" /> Re-run plan
                  </button>
                  <button className="btn" onClick={() => {
                    document.getElementById("seq-targets")?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}>
                    Edit plan
                  </button>
                  <button className="btn" onClick={openLog}>View log</button>
                  {resumable && recoverable && (
                    <button className="btn" onClick={() =>
                      act(async () => { await api.post("/api/sequence/recover"); setRecoverable(null); })}>
                      Resume from frame {recoverable.frames_done}
                    </button>
                  )}
                </>
              )}
            </div>
          </Panel>
        )}

        {/* ----------------------------------------------------- targets */}
        <span id="seq-targets" className="block scroll-mt-4" aria-hidden="true" />
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
                  <label className="flex items-center gap-1.5 text-[11px] text-dim"
                    title="calibration frames (darks/bias) — no slew, focus or guiding">
                    <Toggle checked={t.calibration} onChange={(v) => patchTarget(ti, { calibration: v })} /> Cal
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
              <span className="text-dim">refocus on temp Δ°C (0=off)</span>
              <input className="field !w-16 !py-1" value={plan.refocus_on_temp_delta_c}
                onChange={(e) => setPlan({ ...plan, refocus_on_temp_delta_c: Math.max(0, num(e.target.value, plan.refocus_on_temp_delta_c)) })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">apply filter focus offsets</span>
              <Toggle checked={plan.apply_filter_offsets} onChange={(v) => setPlan({ ...plan, apply_filter_offsets: v })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">meridian flip (German mount)</span>
              <Toggle checked={plan.meridian_flip} onChange={(v) => setPlan({ ...plan, meridian_flip: v })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">recover guiding if lost</span>
              <Toggle checked={plan.recover_guiding} onChange={(v) => setPlan({ ...plan, recover_guiding: v })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">cool sensor to °C (blank=off)</span>
              <input className="field !w-16 !py-1" placeholder="off"
                value={plan.cool_to ?? ""}
                onChange={(e) => setPlan({ ...plan, cool_to: e.target.value === "" ? null : num(e.target.value, plan.cool_to ?? -10) })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">flag HFR spikes (× median, 0=off)</span>
              <input className="field !w-16 !py-1" value={plan.hfr_reject_factor}
                onChange={(e) => setPlan({ ...plan, hfr_reject_factor: Math.max(0, num(e.target.value, plan.hfr_reject_factor)) })} />
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
