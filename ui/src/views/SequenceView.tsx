import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore, useAtlasBannerPending, defaultSchedule } from "../store";
import { Field, HoldButton, InfoDot, Panel, Stat, Toggle } from "../components/ui";
import SchedulePanel from "../components/sequence/SchedulePanel";
import TargetSpark from "../components/sequence/TargetSpark";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { humanizeSeqError } from "../lib/humanize";
import { HELP } from "../help";
import { PreflightStrip, usePreflight } from "../components/PreflightStrip";
import { PreflightModal } from "../components/PreflightModal";
import { useCanControlCapture } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import type { CatalogEntry, ExposureStep, SequencePlan, Target } from "../types";

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
  // Horizon limit for the per-target altitude sparkline + tonight ordering — the
  // server-owned site setting (falls back to 30° when the site is unknown, same
  // default as the /api/visibility route + VisibilityPanel).
  const site = useStore((s) => s.site);
  const altLimit = site?.horizon_min_deg ?? 30;
  // Atlas hand-off: the store holds `atlasBannerPending` (the panel count of the
  // latest Send) so the one-shot "N panels added from Atlas" banner survives this
  // view's remount-on-nav. Dismiss clears the store flag. Store-held (not a useRef
  // seeded from an already-bumped counter) is what lets the freshly-mounted view
  // see the signal at all.
  const atlasBannerPending = useAtlasBannerPending();
  const dismissAtlasBanner = useStore((s) => s.dismissAtlasBanner);
  // VIEWER-READ-ONLY (W2.5): running/aborting a sequence drives devices, so it
  // needs control.capture. The plan BUILDER stays usable for everyone (it's local
  // state + localStorage, not a device write) — only the run-control buttons gate.
  const canRun = useCanControlCapture();
  // Pre-flight gate (F-P0.1): one shared verdict drives BOTH the strip and the
  // Run button. `verdict==='blocked'` disables Run and routes it through the
  // modal (Review) instead of starting. `force` is threaded into the start body
  // for the accepted low-horizon path (FIX-A reads it server-side).
  const { items: preflightItems, verdict } = usePreflight(plan);
  // `force` is needed only when a low-horizon warning is being accepted at Run:
  // the server's horizon guard would otherwise re-block (409) an accepted low run.
  // Mirrors MountView.doGoto's `force: pf.verdict === 'low'` convention.
  const forceLowHorizon = preflightItems.some(
    (i) => i.id === "horizon" && i.status === "warn",
  );
  const [preflightOpen, setPreflightOpen] = useState(false);
  const [ordering, setOrdering] = useState(false);
  const [search, setSearch] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);
  const [recoverable, setRecoverable] =
    useState<{ name: string; frames_done: number; frames_total: number } | null>(null);

  // Reversible deletes use instant-remove + a 5s undo affordance, NOT a hold (R28).
  // Local to this view per touch §2.2 (no global store field required for v1).
  const [pendingUndo, setPendingUndo] =
    useState<{ label: string; prev: SequencePlan } | null>(null);
  const undoTimer = useRef<number | null>(null);

  // Remove `next` from the plan but stash the pre-delete plan so a 5s toast can
  // restore it. A second delete supersedes the first (its snapshot is the latest).
  const deleteWithUndo = (label: string, next: SequencePlan) => {
    if (undoTimer.current != null) clearTimeout(undoTimer.current);
    setPendingUndo({ label, prev: plan });
    setPlan(next);
    undoTimer.current = window.setTimeout(() => setPendingUndo(null), 5000);
  };
  const doUndo = () => {
    if (undoTimer.current != null) { clearTimeout(undoTimer.current); undoTimer.current = null; }
    if (pendingUndo) setPlan(pendingUndo.prev);
    setPendingUndo(null);
  };
  useEffect(() => () => { if (undoTimer.current != null) clearTimeout(undoTimer.current); }, []);

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

  // Order-by-tonight (wave-3 §3): POST the flat target list; the server returns a
  // group-atomic `recommended_order` of indices INTO THE REQUEST LIST, so mosaic
  // groups stay contiguous with no client grouping logic. Reordering the flat
  // array is enough — the blocks partition below re-derives groups from adjacency.
  const orderByTonight = () =>
    act(async () => {
      if (plan.targets.length < 2) return;
      setOrdering(true);
      try {
        const body = await api.post<{ recommended_order: number[] }>(
          "/api/visibility/order",
          {
            targets: plan.targets.map((t) => ({
              name: t.name,
              ra_hours: t.ra_hours,
              dec_deg: t.dec_deg,
              mosaic_group: t.mosaic_group,
            })),
          },
        );
        const order = body.recommended_order;
        // The indices index the REQUEST array; a wrong length or an out-of-range
        // index would silently drop/duplicate targets, so verify a full valid
        // permutation before applying and bail loudly otherwise.
        const valid =
          Array.isArray(order) &&
          order.length === plan.targets.length &&
          order.every((i) => Number.isInteger(i) && i >= 0 && i < plan.targets.length);
        if (!valid) {
          showToast("error", "Couldn't reorder — unexpected response from the server");
          return;
        }
        setPlan({ ...plan, targets: order.map((i) => plan.targets[i]) });
        showToast("success", "Ordered by tonight's transits");
      } finally {
        setOrdering(false);
      }
    });

  // Single writer of POST /api/sequence/start. `force` is threaded into the BODY
  // (FIX-A makes the server read body.force) so an accepted low/below-horizon run
  // isn't re-409'd. Closes the modal on success.
  const startSequence = (force: boolean) => {
    setPreflightOpen(false);
    return act(() => api.post("/api/sequence/start", { ...plan, force }));
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
  // Per-target frame/minute rollup (the same reducer the plan totals use, scoped
  // to one target) — reused for mosaic-group headers.
  const targetFrames = (t: Target) => t.steps.reduce((b, s) => b + s.count, 0);
  const targetSeconds = (t: Target) => t.steps.reduce((b, s) => b + s.count * s.exposure_s, 0);
  const totalFrames = plan.targets.reduce((a, t) => a + targetFrames(t), 0);
  const totalMinutes = plan.targets.reduce((a, t) => a + targetSeconds(t), 0) / 60;

  // Delete every target sharing a mosaic_group (the whole mosaic), reversibly.
  const deleteGroup = (group: string) =>
    deleteWithUndo(
      `Deleted mosaic ${group}`,
      { ...plan, targets: plan.targets.filter((t) => t.mosaic_group !== group) },
    );

  // Partition the (ordered) plan into render blocks: each block is either a single
  // ungrouped target or a run of CONSECUTIVE targets sharing one mosaic_group.
  // Original indices are carried so the existing per-target editors stay correct.
  type Block =
    | { kind: "single"; ti: number }
    | { kind: "group"; group: string; members: number[] };
  const blocks: Block[] = [];
  plan.targets.forEach((t, ti) => {
    const g = t.mosaic_group;
    const prev = blocks[blocks.length - 1];
    if (g && prev && prev.kind === "group" && prev.group === g) {
      prev.members.push(ti);
    } else if (g) {
      blocks.push({ kind: "group", group: g, members: [ti] });
    } else {
      blocks.push({ kind: "single", ti });
    }
  });

  const addTarget = (e: CatalogEntry) => {
    setPlan({
      ...plan,
      targets: [...plan.targets, {
        name: e.id, ra_hours: e.ra_hours, dec_deg: e.dec_deg,
        center: true, autofocus_first: true, calibration: false, steps: [{ ...DEFAULT_STEP }],
        schedule: defaultSchedule(),
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
    <div className="grid gap-4 md:grid-cols-[1fr_300px]">
      <div className="flex flex-col gap-4">
        {/* ----------- one-shot Atlas hand-off banner (panels added from Atlas) */}
        {atlasBannerPending != null && (
          <div
            role="status"
            aria-live="polite"
            className="flex items-center gap-3 border border-accent/50 bg-accent/5 px-3 py-2"
          >
            <Icon name="check" size={16} className="text-good shrink-0" />
            <span className="text-sm text-ink flex-1">
              {atlasBannerPending} {atlasBannerPending === 1 ? "target" : "panels"} added from Atlas
            </span>
            <button
              className="btn tap min-h-[44px] !px-3 !text-[11px]"
              aria-label="Dismiss Atlas banner"
              onClick={dismissAtlasBanner}
            >
              Dismiss
            </button>
          </div>
        )}
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

            {/* Actions by state. Stop-type (Abort) is never disabled for a
                controller; the whole action row is read-only for a viewer (they
                can't have started a run). */}
            <div className="flex flex-wrap gap-2 mt-3">
              {!canRun && running && <ReadOnlyBadge />}
              {canRun && sequence.state === "running" && (
                <button className="btn tap min-h-[44px]" onClick={() => act(() => api.post("/api/sequence/pause"))}>Pause</button>
              )}
              {canRun && sequence.state === "paused" && (
                <button className="btn btn-accent tap min-h-[44px]" onClick={() => act(() => api.post("/api/sequence/resume"))}>Resume</button>
              )}
              {canRun && running && (
                // Abort stops an unattended multi-hour run — non-urgent destructive,
                // so it's a hold-to-confirm (spec §1c). Motion stops (STOP/Halt) stay
                // single-tap; Abort is not a motion stop.
                <HoldButton label="Abort sequence" onConfirm={() => act(() => api.post("/api/sequence/abort"))}>
                  {(bind) => (
                    <button
                      type="button"
                      className="btn btn-danger tap min-h-[44px] relative overflow-hidden select-none"
                      style={{ touchAction: "none" }}
                      aria-label={bind["aria-label"]}
                      onPointerDown={bind.onPointerDown}
                      onPointerUp={bind.onPointerUp}
                      onPointerCancel={bind.onPointerUp}
                      onKeyDown={bind.onKeyDown}
                      onKeyUp={bind.onKeyUp}
                    >
                      <span
                        aria-hidden
                        className="absolute inset-y-0 left-0 confirmhold-fill pointer-events-none"
                        style={{
                          width: `${Math.round(bind.progress * 100)}%`,
                          background: "color-mix(in srgb, var(--text) 70%, transparent)",
                          transition: "width 80ms linear",
                        }}
                      />
                      <span className="relative">{bind.armed ? bind.hintLabel : "Abort"}</span>
                    </button>
                  )}
                </HoldButton>
              )}
              {failed && (
                <>
                  {canRun && (
                    <button className="btn btn-accent" disabled={totalFrames === 0}
                      onClick={() => setPreflightOpen(true)}>
                      <Icon name="play" size={12} className="inline -mt-0.5 mr-1" /> Re-run plan
                    </button>
                  )}
                  <button className="btn" onClick={() => {
                    document.getElementById("seq-targets")?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}>
                    {canRun ? "Edit plan" : "View plan"}
                  </button>
                  <button className="btn" onClick={openLog}>View log</button>
                  {canRun && resumable && recoverable && (
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
            <div className="flex items-center gap-2">
              {/* Reorder the plan by tonight's transit times; mosaic groups stay
                  atomic server-side, so this only reshuffles the flat array.
                  Disabled while running or with <2 targets (nothing to order). */}
              <button
                className="btn tap min-h-[44px] !px-3 !text-[11px] whitespace-nowrap"
                disabled={running || ordering || plan.targets.length < 2}
                title="Reorder targets by tonight's transit times (mosaic groups stay together)"
                onClick={orderByTonight}
              >
                {ordering ? "Ordering…" : "Order by tonight"}
              </button>
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
            </div>
          }>
          {plan.targets.length === 0 && (
            <p className="text-dim text-xs py-6 text-center tracking-widest uppercase">
              empty plan — search the catalog above to add targets
            </p>
          )}
          <div className="flex flex-col gap-4">
            {(() => {
              const renderTarget = (t: Target, ti: number) => (
              <div key={ti} className="border border-line bg-bg/50 p-3">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="font-display font-semibold text-accent tracking-wider">{t.name}</span>
                  <span className="mono text-[11px] text-dim">
                    {t.ra_hours.toFixed(3)}h {t.dec_deg >= 0 ? "+" : ""}{t.dec_deg.toFixed(2)}°
                  </span>
                  {/* Tonight's altitude at a glance (wave-3 §3) — lazy, cached,
                      shared across mosaic panels with the same rounded center. */}
                  <TargetSpark ra_hours={t.ra_hours} dec_deg={t.dec_deg} altLimit={altLimit} />
                  {t.rotation_deg != null && t.rotation_deg > 0.5 && (
                    <span
                      className="mono text-[10px] text-accent border border-line2 px-1.5 py-0.5"
                      title={status?.rotator
                        ? "The rotator will move to this position angle automatically at target start"
                        : "Set your camera to this position angle before the run (no rotator in rig)"}
                    >
                      PA {Math.round(t.rotation_deg)}°{status?.rotator ? " · auto" : ""}
                    </span>
                  )}
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
                  <button className="btn tap min-h-[44px] !px-3 !text-[11px]" disabled={running}
                    onClick={() => patchTarget(ti, { steps: [...t.steps, { ...DEFAULT_STEP }] })}>
                    + step
                  </button>
                  {/* Target delete: trash-style affordance with a ring (R28), >=44px,
                      instant + 5s undo (reversible -> NOT a hold). */}
                  <button
                    className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                      border border-bad/60 text-bad hover:bg-bad/10 disabled:opacity-40"
                    disabled={running}
                    aria-label={`Delete target ${t.name}`}
                    title={`Delete ${t.name}`}
                    onClick={() => deleteWithUndo(
                      `Deleted ${t.name}`,
                      { ...plan, targets: plan.targets.filter((_, i) => i !== ti) },
                    )}>
                    <Icon name="x" size={18} />
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
                        {/* Step remove: >=44px minus-icon, instant + 5s undo (R28). */}
                        <button
                          className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                            text-dim hover:text-bad disabled:opacity-40"
                          disabled={running}
                          aria-label="Remove step"
                          title="Remove step"
                          onClick={() => deleteWithUndo(
                            "Removed step",
                            { ...plan, targets: plan.targets.map((tt, i) =>
                              i === ti ? { ...tt, steps: tt.steps.filter((_, j) => j !== si) } : tt) },
                          )}>
                          <span aria-hidden className="text-xl leading-none">−</span>
                        </button>
                      </div>
                    </div>
                  ))}
                  <div className="grid grid-cols-[90px_70px_60px_50px_60px_auto] gap-2 label !text-[9px]">
                    <span>filter</span><span>exp s</span><span>gain</span><span>bin</span><span>count</span><span />
                  </div>
                </div>
                {/* Per-target autorun schedule (wave-3 §1,6) — collapsed below the
                    steps grid; tolerates a legacy target with no schedule via the
                    defaultSchedule() fallback, and patches through patchTarget. */}
                <SchedulePanel
                  schedule={t.schedule ?? defaultSchedule()}
                  disabled={running}
                  onChange={(patch) =>
                    patchTarget(ti, { schedule: { ...(t.schedule ?? defaultSchedule()), ...patch } })}
                />
              </div>
              );

              return blocks.map((blk, bi) => {
                if (blk.kind === "single") {
                  return renderTarget(plan.targets[blk.ti], blk.ti);
                }
                // mosaic-group block: one header (name · N panels · Σ time) +
                // a delete-group control, with the per-panel editors nested below.
                const members = blk.members.map((i) => plan.targets[i]);
                const gFrames = members.reduce((a, t) => a + targetFrames(t), 0);
                const gMin = members.reduce((a, t) => a + targetSeconds(t), 0) / 60;
                return (
                  <div key={`g-${blk.group}-${bi}`} className="border border-accent/40 bg-accent/[0.03] p-2">
                    <div className="flex items-center gap-3 flex-wrap px-1 pb-2">
                      <span className="font-display font-semibold text-accent tracking-wider">
                        {blk.group}
                      </span>
                      <span className="mono text-[11px] text-dim">
                        {blk.members.length} panel{blk.members.length === 1 ? "" : "s"} ·{" "}
                        {gFrames} frame{gFrames === 1 ? "" : "s"} ·{" "}
                        {Math.floor(gMin / 60)}h {Math.round(gMin % 60)}m
                      </span>
                      <div className="flex-1" />
                      <button
                        className="tap min-h-[44px] inline-flex items-center justify-center gap-1 !px-3 !text-[11px]
                          border border-bad/60 text-bad hover:bg-bad/10 disabled:opacity-40"
                        disabled={running}
                        aria-label={`Delete mosaic group ${blk.group}`}
                        title={`Delete all ${blk.members.length} panels in ${blk.group}`}
                        onClick={() => deleteGroup(blk.group)}
                      >
                        <Icon name="x" size={16} /> delete group
                      </button>
                    </div>
                    <div className="flex flex-col gap-4">
                      {blk.members.map((ti) => renderTarget(plan.targets[ti], ti))}
                    </div>
                  </div>
                );
              });
            })()}
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
              <span className="text-dim inline-flex items-center gap-1">
                dither every N frames
                <InfoDot content={HELP.dither} label="About dither" />
              </span>
              <input className="field !w-16 !py-1" value={plan.dither_every}
                onChange={(e) => setPlan({ ...plan, dither_every: Math.max(0, Math.round(num(e.target.value, plan.dither_every))) })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">dither size (pixels)</span>
              <input className="field !w-16 !py-1" value={plan.dither_pixels}
                onChange={(e) => setPlan({ ...plan, dither_pixels: Math.max(0, Math.round(num(e.target.value, plan.dither_pixels))) })} />
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
              <span className="text-dim inline-flex items-center gap-1">
                apply filter focus offsets
                <InfoDot content={HELP.filterOffset} label="About filter focus offsets" />
              </span>
              <Toggle checked={plan.apply_filter_offsets} onChange={(v) => setPlan({ ...plan, apply_filter_offsets: v })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim inline-flex items-center gap-1">
                meridian flip (German mount)
                <InfoDot content={HELP.meridianFlip} label="About meridian flip" />
              </span>
              <Toggle checked={plan.meridian_flip} onChange={(v) => setPlan({ ...plan, meridian_flip: v })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim inline-flex items-center gap-1">
                meridian warn lead (min)
                <InfoDot
                  label="About the meridian-flip warning lead"
                  content="Lead time before the meridian for the live flip-ETA chip during a run — how far ahead you're warned the mount is about to flip."
                />
              </span>
              <input className="field !w-16 !py-1" value={plan.meridian_flip_warn_min ?? 15}
                onChange={(e) => setPlan({ ...plan, meridian_flip_warn_min: Math.max(0, num(e.target.value, plan.meridian_flip_warn_min ?? 15)) })} />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim inline-flex items-center gap-1">
                safety monitor gate
                <InfoDot
                  label="About the safety monitor gate"
                  content="Honor the configured SafetyMonitor and the global altitude floor during unattended runs — pauses/parks when conditions go unsafe. Off runs without the safety abort."
                />
              </span>
              <Toggle checked={plan.safety_check ?? true} onChange={(v) => setPlan({ ...plan, safety_check: v })} />
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
              <span className="text-dim inline-flex items-center gap-1">
                flag HFR spikes (× median, 0=off)
                <InfoDot content={HELP.hfrReject} label="About HFR spike rejection" />
              </span>
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

        {/* Pre-flight gate (F-P0.1): the strip is always visible once the plan has
            frames; Review opens the modal. Run is disabled when blocked and only
            ever starts via the modal's onProceed (which threads `force`). */}
        <PreflightStrip plan={plan} onReview={() => setPreflightOpen(true)} />

        {canRun ? (
          <button className="btn btn-accent !py-3 !text-sm"
            disabled={running || totalFrames === 0 || verdict === "blocked"}
            onClick={() => setPreflightOpen(true)}>
            ≡ Run Sequence
          </button>
        ) : (
          // Viewer: no run control — a passive read-only note, never a 403-on-tap.
          <p className="text-[11px] text-warn text-center inline-flex items-center justify-center gap-2 py-2">
            <ReadOnlyBadge label="View only" reason="Running a sequence needs operator or admin access." />
            Running a sequence needs operator or admin access.
          </p>
        )}
        {canRun && totalFrames === 0 && (
          <p className="text-[11px] text-dim text-center">add targets and steps first</p>
        )}
        {totalFrames > 0 && verdict === "blocked" && (
          <p className="text-[11px] text-bad text-center">fix the blocked items above to run</p>
        )}
      </div>

      {/* Undo affordance for reversible deletes (R28). Fixed, deterministic,
          auto-dismisses after 5s; tapping Undo restores the pre-delete plan. */}
      {pendingUndo && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-4 left-1/2 -translate-x-1/2 z-50 panel
            flex items-center gap-3 px-4 py-2 shadow-lg"
        >
          <span className="text-sm text-ink">{pendingUndo.label}</span>
          <button className="btn btn-accent tap min-h-[44px] !px-4" onClick={doUndo}>
            Undo
          </button>
        </div>
      )}

      {/* Pre-flight gate (F-P0.1). onProceed threads `force` into the start body.
          Used by both the Run and Re-run paths; `preflightResume` only relaxes the
          strip wording downstream — the modal's blocker gate stays fail-safe. */}
      <PreflightModal
        plan={plan}
        open={preflightOpen}
        force={forceLowHorizon}
        onClose={() => setPreflightOpen(false)}
        onProceed={(force) => { void startSequence(force); }}
      />
    </div>
  );
}
