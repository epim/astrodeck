import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import {
  useStore, useAtlasBannerPending, useLastReportId, defaultSchedule,
  usePhotometry, usePreview,
} from "../store";
import { HoldButton, IconButton, InfoDot, Panel, Toggle } from "../components/ui";
import SchedulePanel from "../components/sequence/SchedulePanel";
import SessionsPanel from "../components/sequence/SessionsPanel";
import PlanLibraryPanel from "../components/sequence/PlanLibraryPanel";
import InstructionsPanel from "../components/sequence/InstructionsPanel";
import TargetSpark from "../components/sequence/TargetSpark";
import { Icon } from "../components/icons";
import type { IconName } from "../components/icons";
import { diagnoseFailure } from "../lib/troubleshoot";
import { uid } from "../lib/ids";
import { applyStepsToGroup } from "../lib/planGroups";
import { SEQUENCE_TEMPLATES, templateSteps, type SequenceTemplate } from "../lib/sequenceTemplates";
import {
  FLAT_ADU_TARGET, armedQualityGates, countShrinksSilently, frameTypeDefaults, nextStep,
} from "../components/sequence/stepDefaults";
import { HELP } from "../help";
import { PreflightStrip, usePreflight } from "../components/PreflightStrip";
import { PreflightModal } from "../components/PreflightModal";
import { confirmDialog } from "../components/ConfirmDialog";
import { accessPhrase, useCanControlMount } from "../lib/caps";
import { EXPOSURE_MAX_S, isExposureValueInvalid } from "../lib/exposure";
import {
  integrationByFilter, skyElectronsPerSub, skyRateEPerSec, skyLimitedSubSeconds,
  subLengthVerdict, type SubVerdict,
} from "../lib/photometry";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import { formatScheduleStatus } from "../lib/scheduleStatus";
import type {
  CatalogEntry, ExposureStep, SequencePlan, SequenceState, Target, VisibilityNight,
} from "../types";

// UX-22: frame types the step editor can script (backend IMAGETYP + shutter
// wiring already honor these — hub.capture / imaging/fitsio.py).
const FRAME_TYPES = ["Light", "Dark", "Flat", "Bias"] as const;

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

/** Runtime autorun-schedule chip (wave-3 §2/§4). Pure render of the engine's
 *  `sequence.schedule` block — `waiting` is a dim in-progress note (with the
 *  resolved start time), `window_closed`/`never_rises` are a warn-tone stop
 *  reason; `ready` renders nothing (no chip-worthy state to report). */
function ScheduleChip({ schedule }: { schedule: NonNullable<SequenceState["schedule"]> }) {
  const status = formatScheduleStatus(schedule, undefined, Date.now() / 1000);
  if (!status) return null;
  const warn = status.tone === "warn";
  return (
    <span className={`text-[11px] inline-flex items-center gap-1 ${warn ? "text-warn" : "text-dim"}`}>
      <Icon name={warn ? "alert" : "clock"} size={13} /> {status.text}
    </span>
  );
}

export default function SequenceView() {
  const status = useStore((s) => s.status);
  const sequence = useStore((s) => s.sequence);
  const showToast = useStore((s) => s.showToast);
  const openLog = useStore((s) => s.openLog);
  const openHelp = useStore((s) => s.openHelp);
  const setView = useStore((s) => s.setView);
  // Deep-link target for the finished-run "View session report →" action
  // (report viewer spec §3 Task 4) — the store stashes the id of the most
  // recently finalized SessionReport on the `report` bus event.
  const lastReportId = useLastReportId();
  // SSOT: the plan lives in the store (single writer of `astrodeck-plan`; setPlan
  // persists). No private useState / localStorage effect here.
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  // Frame-in-Atlas (wave-3 §5): openFraming switches the view to "atlas" and
  // seeds the framing session's center from the CatalogEntry; the follow-up
  // setFraming lands the PA synchronously on that fresh session (MountView
  // precedent — see IconButton icon="frame" usage below).
  const openFraming = useStore((s) => s.openFraming);
  const setFraming = useStore((s) => s.setFraming);
  // Horizon limit for the per-target altitude sparkline + tonight ordering — the
  // server-owned site setting (falls back to 30° when the site is unknown, same
  // default as the /api/visibility route + VisibilityPanel).
  const site = useStore((s) => s.site);
  const altLimit = site?.horizon_min_deg ?? 30;
  // PRO-6 sky-limited step advisory (photometry/SNR design §3 Task 5): read once
  // here (not per-step) — the tested photometry.ts core does the math, this view
  // only renders it. Additive display only; never touches run/plan totals.
  const photometryProfile = usePhotometry();
  const livePreview = usePreview();
  const stepSkyLimitedS = (livePreview && livePreview.data_is_linear &&
      photometryProfile.egain > 0 && photometryProfile.readNoiseE > 0)
    ? skyLimitedSubSeconds(
        photometryProfile.readNoiseE,
        skyRateEPerSec(
          skyElectronsPerSub(livePreview.stats.median, photometryProfile.biasAdu, photometryProfile.egain),
          livePreview.exposure_s,
        ),
      )
    : null;
  // Atlas hand-off: the store holds `atlasBannerPending` (the panel count of the
  // latest Send) so the one-shot "N panels added from Atlas" banner survives this
  // view's remount-on-nav. Dismiss clears the store flag. Store-held (not a useRef
  // seeded from an already-bumped counter) is what lets the freshly-mounted view
  // see the signal at all.
  const atlasBannerPending = useAtlasBannerPending();
  const dismissAtlasBanner = useStore((s) => s.dismissAtlasBanner);
  // VIEWER-READ-ONLY (W2.5): a sequence run SLEWS the mount to each target, so
  // the server's /api/sequence/{start,pause,resume,abort,recover} routes all
  // require control.mount, NOT control.capture (an operator can run a single
  // capture/loop but cannot start a slewing sequence — server
  // auth/capabilities.py ROLES_CAP["operator"]). The plan BUILDER stays usable
  // for everyone (it's local state + localStorage, not a device write) — only
  // the run-control buttons gate.
  const canRun = useCanControlMount();
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
  const [searchErr, setSearchErr] = useState<string | null>(null);
  // In-flight guard for the quick-add visibility check (wave-3 §4): blocks a
  // second addTarget() while the first's /api/visibility fetch (or its confirm
  // dialog) is still pending, so a double-tap on a search result can't double-add.
  const [pendingAdd, setPendingAdd] = useState(false);
  const [recoverable, setRecoverable] =
    useState<{ name: string; frames_done: number; frames_total: number } | null>(null);

  // Reversible plan edits (deletes AND the mosaic "apply to all panels" copy)
  // use instant-apply + a 5s undo affordance, NOT a hold (R28). Local to this
  // view per touch §2.2 (no global store field required for v1).
  const [pendingUndo, setPendingUndo] =
    useState<{ label: string; prev: SequencePlan } | null>(null);
  const undoTimer = useRef<number | null>(null);

  // Apply `next` to the plan but stash the pre-edit plan so a 5s toast can
  // restore it. A second reversible edit supersedes the first (its snapshot is
  // the latest). Generalized from a delete-only helper — delete behavior is
  // unchanged, just called through this shared name.
  const setPlanWithUndo = (label: string, next: SequencePlan) => {
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
    if (!search) { setResults([]); setSearchErr(null); return; }
    const t = setTimeout(async () => {
      // UX-18: surface a fetch failure instead of swallowing it into "no results".
      try {
        setResults((await api.get<CatalogEntry[]>(`/api/catalog?q=${encodeURIComponent(search)}`)).slice(0, 6));
        setSearchErr(null);
      } catch (e) { setResults([]); setSearchErr(e instanceof Error ? e.message : "search unavailable"); }
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
        // The indices index the REQUEST array; a wrong length, an out-of-range
        // index, or a repeated index would silently drop/duplicate targets, so
        // verify a full valid permutation (length + integer + bounds + uniqueness)
        // before applying and bail loudly otherwise.
        const valid =
          Array.isArray(order) &&
          order.length === plan.targets.length &&
          order.every((i) => Number.isInteger(i) && i >= 0 && i < plan.targets.length) &&
          new Set(order).size === order.length;
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
  // UX-2026-07-26 #23: one diagnosis for the whole panel — the banner renders
  // it, and the panel border must not go red-alarm for a run the operator
  // stopped on purpose.
  const endDiag = diagnoseFailure(sequence.detail, {
    state: sequence.state,
    framesDone: sequence.progress?.frames_done,
    framesTotal: sequence.progress?.frames_total,
  });
  const stoppedByUser = !!endDiag.userInitiated;

  // Resume-from-N is offered whenever the BACKEND says the run is recoverable
  // (session_store.recoverable() = the most recent dormant session that actually
  // has frames); a pre-first-frame failure has no resume file, so the button is
  // simply absent.
  //
  // REVIEW #10: this used to additionally require `sequence.state === "error"`,
  // which silently excluded the far more common `aborted` — so on night 2 the
  // banner read "NGC7000 SHO — ABORTED · 15/18 frames" and offered RE-RUN PLAN /
  // EDIT PLAN / VIEW LOG with no resume anywhere, while the working RESUME sat
  // at y=1413 of a 2027px page. On 300s narrowband, re-shooting those 15 subs is
  // 75 minutes of clear sky. The backend flag is the authority for BOTH terminal
  // states; the client gate was the bug.
  const resumable = !!recoverable && failed;

  useEffect(() => {
    if (running) return;
    api.get<{ recoverable: boolean; name?: string; frames_done?: number; frames_total?: number }>(
      "/api/sequence/recoverable")
      .then((r) => setRecoverable(r.recoverable
        ? { name: r.name!, frames_done: r.frames_done!, frames_total: r.frames_total! } : null))
      .catch(() => { /* server not up */ });
  }, [running]);

  const filters = status?.filterwheel?.names ?? [];
  // Where the wheel is physically parked — the filter a NEW step should start
  // on when there is no previous step to inherit from (#1's editor half).
  const wheelPosition = status?.filterwheel?.position;
  // Per-target frame/minute rollup (the same reducer the plan totals use, scoped
  // to one target) — reused for mosaic-group headers.
  const targetFrames = (t: Target) => t.steps.reduce((b, s) => b + s.count, 0);
  const targetSeconds = (t: Target) => t.steps.reduce((b, s) => b + s.count * s.exposure_s, 0);
  const totalFrames = plan.targets.reduce((a, t) => a + targetFrames(t), 0);

  // Delete every target sharing a mosaic_group (the whole mosaic), reversibly.
  const deleteGroup = (group: string) =>
    setPlanWithUndo(
      `Deleted mosaic ${group}`,
      { ...plan, targets: plan.targets.filter((t) => t.mosaic_group !== group) },
    );

  // Copy one panel's step list onto every panel sharing its mosaic_group,
  // reversibly (spec: mosaic-apply-steps). `sourceTi` is the index of the
  // panel whose steps are the template — any member can be the source.
  const applyGroupSteps = (group: string, sourceTi: number) => {
    const memberCount = plan.targets.filter((t) => t.mosaic_group === group).length;
    setPlanWithUndo(
      `Applied steps to ${memberCount} panel${memberCount === 1 ? "" : "s"}`,
      { ...plan, targets: applyStepsToGroup(plan.targets, group, plan.targets[sourceTi].steps) },
    );
  };

  // Starter templates (NOV-5): replace ONE target's steps with a ready-made
  // recipe from SEQUENCE_TEMPLATES, resolving filter intents against the connected
  // wheel. Reversible (setPlanWithUndo — same 5s-undo path as delete / apply-to-all),
  // so a mis-tap on a built-up target is one Undo away. ids are minted by
  // setPlan.ensurePlanIds (store.ts:760).
  const applyTemplate = (ti: number, tpl: SequenceTemplate) => {
    const target = plan.targets[ti];
    setPlanWithUndo(
      `Applied '${tpl.label}' to ${target.name}`,
      {
        ...plan,
        targets: plan.targets.map((t, i) =>
          i === ti ? { ...t, steps: templateSteps(tpl, filters) } : t),
      },
    );
  };

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

  // Quick-add below-horizon confirm (wave-3 §4): a catalog-search add now checks
  // tonight's visibility first and gates on a confirm dialog when the target
  // never clears the site's alt limit — mirrors AtlasView.sendToPlan's gate
  // verbatim. A visibility-fetch FAILURE must never block the add (catch below
  // falls through to the unconditional add), and both the "fine, no dialog"
  // and "confirmed" paths funnel into the same setPlan/setSearch call.
  const addTarget = async (e: CatalogEntry) => {
    if (pendingAdd) return; // in-flight guard — no double-add on a rapid double-tap
    setPendingAdd(true);
    try {
      let ok = true;
      try {
        const night = await api.get<VisibilityNight>(
          `/api/visibility?ra=${e.ra_hours}&dec=${e.dec_deg}&alt_limit=${altLimit}`,
        );
        if (night.never_rises_above_limit) {
          ok = await confirmDialog({
            title: "Below tonight's limit",
            body: `${e.name || e.id} doesn't rise above ${Math.round(night.alt_limit_deg)}° tonight (peaks ${night.transit_alt.toFixed(0)}°). Add anyway?`,
            tone: "warn",
            mode: "confirm",
            confirmLabel: "Add anyway",
            confirmPrimary: true, // PLAN-01-gemini: proceeding is the intended action
          });
        }
      } catch {
        /* visibility fetch failed — proceed without a confirm gate */
      }
      if (!ok) return;
      setPlan({
        ...plan,
        targets: [...plan.targets, {
          id: uid(),
          name: e.id, ra_hours: e.ra_hours, dec_deg: e.dec_deg,
          center: true, autofocus_first: true, calibration: false,
          // A brand-new target's first step starts on the filter the wheel is
          // physically parked on, not "no filter" — the plan-editor half of #1.
          steps: [{ ...nextStep([], filters, wheelPosition), id: uid() }],
          schedule: defaultSchedule(),
        }],
      });
      setSearch("");
    } finally {
      setPendingAdd(false);
    }
  };

  // Frame-in-Atlas (wave-3 §5): builds a pseudo-CatalogEntry (zeros for the
  // fields Atlas doesn't need to seed a framing session) and hands it to the
  // store's openFraming/setFraming pair — same call shape for a single target
  // card and a mosaic-group header (which passes its first member + group name).
  const frameInAtlas = (
    id: string, name: string,
    t: { ra_hours: number; dec_deg: number; rotation_deg?: number },
  ) => {
    const entry: CatalogEntry = {
      id, name, type: "", ra_hours: t.ra_hours, dec_deg: t.dec_deg,
      mag: 0, size_arcmin: 0, alt: 0, az: 0,
    };
    openFraming(entry);
    setFraming({ rotation_deg: t.rotation_deg ?? 0 });
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

  // REVIEW #7 (systemic S2 — tablet portrait is the primary field device):
  // `md:grid-cols-[1fr_300px]` split at 768, so an 820 tablet got a 416px plan
  // editor beside a 300px sidebar. A bare `1fr` track floors at its item's
  // MIN-CONTENT, and the step editor below carried a fixed 454px column
  // template, so the grid rendered 916px inside a 732px `main` that is
  // `overflow-x: hidden` — with the document unable to scroll horizontally,
  // "meridian flip (German mount)" sat at x=957→993 in an 820px window and
  // dither / refocus cadence / cool-to-temp / count=accepted were unsettable by
  // any finger gesture. Empty plan was fine; a loaded target was the trigger.
  //
  // Two changes, both required: `minmax(0,1fr)` so the track may shrink at all,
  // and the split moved to `lg` so tablet portrait gets ONE full-width column
  // (700px for the step editor rather than 416px). `min-w-0` on both children
  // stops a grid item's automatic minimum size re-inflating the track.
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="flex flex-col gap-4 min-w-0">
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
            className={failed && !stoppedByUser ? "!border-bad/60" : ""}
            right={<SeqStateBadge state={sequence.state} />}>

            {/* Failure banner — decoupled from progress so an early (pre-first-frame)
                failure still shows a clear, human reason.
                UX-2026-07-26 #23: a run the operator held ABORT to stop is not a
                failure. It reads as itself ("You stopped the run at 4/9 frames"),
                on a neutral card, with no advisory and no raw `sequence aborted`
                echo underneath — that echo was the engine's own wording for the
                very action the user just took. */}
            {failed && (() => {
              const diag = endDiag;
              return (
              <div className={`flex items-start gap-2 border px-3 py-2 mb-3 ${
                stoppedByUser ? "border-line bg-line2/20" : "border-bad/50 bg-bad/5"}`}>
                <Icon name={sequence.state === "error" ? "x" : "stop"} size={16}
                  className={`mt-0.5 shrink-0 ${stoppedByUser ? "text-dim" : "text-bad"}`} />
                <div className="min-w-0">
                  <p className="text-sm text-ink leading-snug">
                    {stoppedByUser
                      ? diag.title
                      : sequence.state === "error" ? "Sequence failed" : "Sequence aborted"}
                  </p>
                  <p className="text-xs text-ink/85 leading-snug mt-0.5">
                    {diag.cause}{diag.fix ? ` ${diag.fix}` : ""}
                  </p>
                  {diag.topic && (
                    <button type="button" onClick={() => openHelp(diag.topic!)}
                      className="text-[11px] text-accent hover:underline mt-1">How to fix →</button>
                  )}
                  {sequence.detail && !stoppedByUser && (
                    <p className="text-[10px] mono text-dim leading-snug mt-1 break-words">
                      {sequence.detail}
                    </p>
                  )}
                </div>
              </div>
              );
            })()}

            {/* Runtime autorun-schedule chip (wave-3 §2/§4) — pure render of the
                engine's sequence.schedule block; absent entirely when the engine
                hasn't attached one (e.g. no windowed target is active). */}
            {sequence.schedule && (
              <div className="mb-2">
                <ScheduleChip schedule={sequence.schedule} />
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
                      {/* Persistent hold affordance (r1 backlog): a tap-and-release with
                          nothing visibly happening got filed as a Blocker by an external
                          reviewer — "Abort" alone never said this needs a HOLD. */}
                      <span className="relative flex flex-col items-center leading-tight">
                        <span>{bind.armed ? bind.hintLabel : "Abort"}</span>
                        {!bind.armed && (
                          <span className="text-[9px] tracking-wider normal-case opacity-75">hold to confirm</span>
                        )}
                      </span>
                    </button>
                  )}
                </HoldButton>
              )}
              {failed && (
                <>
                  {/* REVIEW #10 — hierarchy. When the run IS resumable, Resume is
                      the primary and Re-run is demoted, because the two differ by
                      an hour of clear sky and only one of them was prominent.
                      Both are always present; only the emphasis moves. */}
                  {canRun && resumable && recoverable && (
                    <button className="btn btn-accent tap min-h-[44px]" onClick={() =>
                      act(async () => { await api.post("/api/sequence/recover"); setRecoverable(null); })}>
                      <Icon name="play" size={12} className="inline -mt-0.5 mr-1" />
                      Resume from frame {recoverable.frames_done}/{recoverable.frames_total}
                    </button>
                  )}
                  {canRun && (
                    <button
                      className={`${resumable ? "btn" : "btn btn-accent"} tap min-h-[44px]`}
                      disabled={totalFrames === 0}
                      title={resumable
                        ? "Starts over at frame 1 — the frames already on disk are not reused"
                        : undefined}
                      onClick={() => setPreflightOpen(true)}>
                      <Icon name="play" size={12} className="inline -mt-0.5 mr-1" /> Re-run plan
                    </button>
                  )}
                  <button className="btn tap min-h-[44px]" onClick={() => {
                    document.getElementById("seq-targets")?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}>
                    {canRun ? "Edit plan" : "View plan"}
                  </button>
                  <button className="btn tap min-h-[44px]" onClick={openLog}>View log</button>
                </>
              )}
              {/* Said in words, not by button order alone — the distinction that
                  costs 75 minutes has to survive a glance in the dark. */}
              {canRun && failed && resumable && recoverable && (
                <p className="basis-full text-[11px] text-dim leading-snug">
                  Resume picks up at frame {recoverable.frames_done} of{" "}
                  {recoverable.frames_total}. Re-run starts over from frame 1 and
                  re-shoots what you already have.
                </p>
              )}
              {/* Deep-link to the end-of-night report (report viewer spec §3
                  Task 4) — the engine publishes `report` on every terminal path
                  (complete/aborted/error/…), so this shows alongside the failed
                  actions above too, not just a clean "complete". */}
              {finished && lastReportId && (
                <button className="btn tap min-h-[44px]" onClick={() => setView("report")}>
                  <Icon name="check" size={12} className="inline -mt-0.5 mr-1" />
                  View session report →
                </button>
              )}
            </div>
          </Panel>
        )}

        {/* ----------------------------------------------------- targets */}
        <span id="seq-targets" className="block scroll-mt-4" aria-hidden="true" />
        <Panel title="Targets"
          right={
            <div className="flex flex-wrap items-center gap-2 min-w-0">
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
              <div className="relative w-56 max-w-full min-w-0">
                {/* A placeholder is NOT an accessible name: it is not exposed by
                    the accname algorithm in every AT, and it vanishes the moment
                    the user types. This was one of the two controls still
                    reported unnamed after the UX-review sweep (#26). */}
                <input className="field" placeholder="+ add target — search catalog"
                  aria-label="Search the catalog to add a target"
                  value={search} onChange={(e) => setSearch(e.target.value)} />
                {results.length > 0 && (
                <div className="absolute right-0 top-full mt-1 w-72 panel z-10 max-h-60 overflow-y-auto">
                  {results.map((r) => (
                    <button key={r.id} onClick={() => addTarget(r)} disabled={pendingAdd}
                      className="w-full text-left px-3 py-2 text-xs hover:bg-raise transition-colors flex justify-between cursor-pointer disabled:opacity-40 disabled:cursor-default">
                      <span><span className="mono text-accent">{r.id}</span> {r.name}</span>
                      <span className={`mono ${r.alt > 40 ? "text-good" : r.alt < 20 ? "text-warn" : "text-dim"}`}>
                        {r.alt.toFixed(0)}°
                      </span>
                    </button>
                  ))}
                </div>
              )}
                {searchErr && results.length === 0 && (
                  <div className="absolute right-0 top-full mt-1 w-72 panel z-10 px-3 py-2 text-xs text-bad">
                    Search failed: {searchErr}
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
              <div key={t.id ?? ti} className="border border-line bg-bg/50 p-3">
                <div className="flex items-center gap-3 flex-wrap">
                  <span className="font-display font-semibold text-accent tracking-wider">{t.name}</span>
                  <span className="mono text-[11px] text-dim">
                    {t.ra_hours.toFixed(3)}h {t.dec_deg >= 0 ? "+" : ""}{t.dec_deg.toFixed(2)}°
                  </span>
                  {/* Tonight's altitude at a glance (wave-3 §3) — lazy, cached,
                      shared across mosaic panels with the same rounded center. */}
                  <TargetSpark ra_hours={t.ra_hours} dec_deg={t.dec_deg} altLimit={altLimit} />
                  {/* Runtime schedule chip (wave-3 §2/§4) inline on the active
                      target card — same chip content as the run panel above. */}
                  {sequence.target_index === ti && running && sequence.schedule && (
                    <ScheduleChip schedule={sequence.schedule} />
                  )}
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
                    <Toggle checked={t.center} onChange={(v) => patchTarget(ti, { center: v })}
                      label={`Center on ${t.name}`} /> center
                  </label>
                  <label className="flex items-center gap-1.5 text-[11px] text-dim">
                    <Toggle checked={t.autofocus_first} onChange={(v) => patchTarget(ti, { autofocus_first: v })}
                      label={`Autofocus first for ${t.name}`} /> AF
                  </label>
                  <label className="flex items-center gap-1.5 text-[11px] text-dim"
                    title="calibration frames (darks/bias) — no slew, focus or guiding">
                    <Toggle checked={t.calibration} onChange={(v) => patchTarget(ti, { calibration: v })}
                      label={`Calibration frames for ${t.name}`} /> Cal
                  </label>
                  {/* The trailing actions wrap as ONE right-aligned unit. With a
                      `flex-1` spacer instead, a narrow card put `+ step` alone on
                      line 1 and the frame/delete pair alone on line 2 flush LEFT,
                      which read as a broken layout. */}
                  <div className="ml-auto flex flex-wrap items-center justify-end gap-1.5">
                  {/* Mosaic "apply to all panels" (spec: mosaic-apply-steps): visible
                      on EVERY member (any panel can be the source), only once the
                      group actually has >1 panel — a lone panel has nothing to fan
                      out to, so the button would be a no-op. */}
                  {t.mosaic_group != null &&
                    plan.targets.filter((x) => x.mosaic_group === t.mosaic_group).length > 1 && (
                    <button
                      className="tap min-h-[44px] inline-flex items-center justify-center gap-1 !px-3 !text-[11px]
                        border border-line2 text-dim hover:text-accent hover:border-accent/50 disabled:opacity-40"
                      disabled={running}
                      aria-label={`Apply this panel's steps to all ${t.mosaic_group} panels`}
                      title={`Apply this panel's steps to all ${t.mosaic_group} panels`}
                      onClick={() => applyGroupSteps(t.mosaic_group!, ti)}
                    >
                      <Icon name="grid" size={14} /> apply to all panels
                    </button>
                  )}
                  {/* REVIEW #29: `+ step` used to append a hard-coded
                      Light/no-filter/120s/g100/1×/×10 row, so building Ha+OIII+SII
                      at 300s×20 meant re-typing five fields three times — ~15 edits
                      on a tablet in the dark. It now CLONES the last step, which
                      makes the common case ("same again, different filter") one
                      interaction. */}
                  <button className="btn tap min-h-[44px] !px-3 !text-[11px]" disabled={running}
                    title={t.steps.length > 0
                      ? "Add a step — copies the last step's exposure, gain, binning and count"
                      : "Add a step"}
                    onClick={() => patchTarget(ti, {
                      steps: [...t.steps, { ...nextStep(t.steps, filters, wheelPosition), id: uid() }],
                    })}>
                    + step
                  </button>
                  <div className="inline-flex items-center gap-1.5">
                    {/* Frame this target in the Sky Atlas (wave-3 §5). Works
                        offline (no device needed to seed a framing session), so
                        it is never disabled — MountView precedent. */}
                    <IconButton
                      icon="frame"
                      label={`Frame ${t.name} in the Sky Atlas`}
                      onClick={() => frameInAtlas(t.name, t.name, t)}
                    />
                    {/* Target delete: trash-style affordance with a ring (R28), >=44px,
                        instant + 5s undo (reversible -> NOT a hold). */}
                    <button
                      className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                        border border-bad/60 text-bad hover:bg-bad/10 disabled:opacity-40"
                      disabled={running}
                      aria-label={`Delete target ${t.name}`}
                      title={`Delete ${t.name}`}
                      onClick={() => setPlanWithUndo(
                        `Deleted ${t.name}`,
                        { ...plan, targets: plan.targets.filter((_, i) => i !== ti) },
                      )}>
                      <Icon name="x" size={18} />
                    </button>
                  </div>
                  </div>
                </div>
                {/* PRO-6 per-filter projected integration (photometry/SNR design §3
                    Task 5): a read-only rollup of this target's own steps via the
                    tested integrationByFilter core. Hidden when the target has no
                    steps yet. */}
                {t.steps.length > 0 && (
                  <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 mono text-[10px] text-dim">
                    {integrationByFilter(t.steps).map((f) => {
                      const min = f.seconds / 60;
                      return (
                        <span key={f.filter}>
                          {f.filter} {Math.floor(min / 60)}h {Math.round(min % 60)}m
                        </span>
                      );
                    })}
                  </div>
                )}
                {/* Starter templates (NOV-5): one-tap exposure recipes for first-timers.
                    Always visible on each target card; run-locked like +step/delete. */}
                <div className="mt-2 flex flex-wrap items-center gap-1.5">
                  <span className="label !text-[9px] text-dim">starter</span>
                  {SEQUENCE_TEMPLATES.map((tpl) => (
                    <button
                      key={tpl.id}
                      type="button"
                      className="tap min-h-[44px] !px-2.5 !text-[11px] inline-flex items-center gap-1
                        border border-line2 text-dim hover:text-accent hover:border-accent/50 disabled:opacity-40"
                      disabled={running}
                      title={tpl.blurb}
                      aria-label={`Apply starter template ${tpl.label} to ${t.name}`}
                      onClick={() => applyTemplate(ti, tpl)}
                    >
                      <Icon name="plan" size={12} /> {tpl.label}
                    </button>
                  ))}
                </div>
                <div className="mt-2 flex flex-col gap-1.5">
                  {t.steps.map((s, si) => {
                    // 0/negative/absurd exposure sailed through with zero validation
                    // and only 422'd at Run (CaptureView's manual field has had this
                    // guard since CAP-02-gemini/R3-CAP-02; the step editor didn't).
                    // Same bounds as CaptureView, via the shared lib/exposure.ts helper.
                    const stepExposureInvalid = isExposureValueInvalid(s.exposure_s);
                    // PRO-6 sky-limited advisory (photometry/SNR design §3 Task 5):
                    // compares THIS step's own exposure_s against the single
                    // preview-derived sky-limited length (stepSkyLimitedS, computed
                    // once above from the live preview + profile) — same rollup the
                    // whole card shares, not a per-step sky measurement. Gated to
                    // Light frames only (a "sky-limited" verdict is meaningless for
                    // Dark/Flat/Bias). "unknown" (no preview/profile) renders nothing
                    // — never a misleading chip.
                    const isLightStep = (s.frame_type ?? "Light") === "Light";
                    const stepVerdict: SubVerdict = isLightStep
                      ? subLengthVerdict(s.exposure_s, stepSkyLimitedS)
                      : "unknown";
                    return (
                    <div key={s.id ?? si} className="flex flex-col gap-0.5">
                    {/* REVIEW #26 + S2. This row was a `grid-cols-[76px_90px_70px_
                        60px_50px_60px_auto]` — 454px of FIXED tracks, which is
                        what floored the whole left column's min-content and
                        pushed Plan's right column off an 820px tablet. Its six
                        controls also returned `aria-label ''`, with the column
                        captions rendered as one row UNDERNEATH the last step, so
                        both screen-reader users and sighted users on a narrow
                        width lost the association (mono counted columns to find
                        COUNT).
                        A wrapping row of individually-labelled fields fixes all
                        of it at once: every control carries its own visible
                        caption AND an aria-label naming the step and target, the
                        caption can no longer scroll away from its input, and the
                        row reflows instead of overflowing at any width. */}
                    <div className="flex flex-wrap items-end gap-2">
                      <label className="flex flex-col gap-0.5 w-[86px]">
                        <span className="label">type</span>
                        <select className="field !py-1"
                          aria-label={`Frame type — step ${si + 1} of ${t.name}`}
                          value={s.frame_type ?? "Light"}
                          onChange={(e) => patchStep(ti, si, frameTypeDefaults(s, e.target.value))}>
                          {FRAME_TYPES.map((ft) => <option key={ft} value={ft}>{ft}</option>)}
                        </select>
                      </label>
                      <label className="flex flex-col gap-0.5 w-[96px]">
                        <span className="label">filter</span>
                        <select className="field !py-1"
                          aria-label={`Filter — step ${si + 1} of ${t.name}`}
                          value={s.filter ?? ""}
                          onChange={(e) => patchStep(ti, si, { filter: e.target.value || null })}>
                          <option value="">no filter</option>
                          {filters.map((f) => <option key={f} value={f}>{f}</option>)}
                        </select>
                      </label>
                      <label className="flex flex-col gap-0.5 w-[78px]">
                        <span className="label">exp s</span>
                        <input className={`field !py-1 ${stepExposureInvalid ? "border-bad" : ""}`}
                          inputMode="decimal"
                          aria-label={`Exposure seconds — step ${si + 1} of ${t.name}`}
                          title={stepExposureInvalid ? `Exposure must be 0–${EXPOSURE_MAX_S}s` : undefined}
                          aria-invalid={stepExposureInvalid} value={s.exposure_s}
                          onChange={(e) => patchStep(ti, si, { exposure_s: num(e.target.value, s.exposure_s) })} />
                      </label>
                      <label className="flex flex-col gap-0.5 w-[70px]">
                        <span className="label">gain</span>
                        <input className="field !py-1" inputMode="numeric"
                          aria-label={`Gain — step ${si + 1} of ${t.name}`} value={s.gain}
                          onChange={(e) => patchStep(ti, si, { gain: num(e.target.value, s.gain) })} />
                      </label>
                      <label className="flex flex-col gap-0.5 w-[64px]">
                        <span className="label">bin</span>
                        <select className="field !py-1"
                          aria-label={`Binning — step ${si + 1} of ${t.name}`} value={s.binning}
                          onChange={(e) => patchStep(ti, si, { binning: Number(e.target.value) })}>
                          {[1, 2, 4].map((b) => <option key={b} value={b}>{b}×</option>)}
                        </select>
                      </label>
                      <label className="flex flex-col gap-0.5 w-[70px]">
                        <span className="label">count</span>
                        <input className="field !py-1" inputMode="numeric"
                          aria-label={`Frame count — step ${si + 1} of ${t.name}`} value={s.count}
                          onChange={(e) => patchStep(ti, si, { count: Math.max(1, Math.round(num(e.target.value, s.count))) })} />
                      </label>
                      <div className="flex items-center gap-2 self-end pb-0.5">
                        <span className="mono text-[10px] text-dim whitespace-nowrap">
                          {((s.count * s.exposure_s) / 60).toFixed(0)}m
                        </span>
                        {/* Step remove: >=44px minus-icon, instant + 5s undo (R28). */}
                        <button
                          className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                            text-dim hover:text-bad disabled:opacity-40"
                          disabled={running}
                          aria-label={`Remove step ${si + 1} of ${t.name}`}
                          title="Remove step"
                          onClick={() => setPlanWithUndo(
                            "Removed step",
                            { ...plan, targets: plan.targets.map((tt, i) =>
                              i === ti ? { ...tt, steps: tt.steps.filter((_, j) => j !== si) } : tt) },
                          )}>
                          <span aria-hidden className="text-xl leading-none">−</span>
                        </button>
                      </div>
                    </div>
                    {/* PRO-5 flat auto-exposure: a target-ADU field, Flat steps
                        only. 0 = today's fixed-exposure behavior; >0 solves the
                        exposure to that median ADU against the flat panel.
                        Honest-disabled while running (§11.8): dim + lock +
                        aria-disabled + title, never the native disabled attr.

                        REVIEW #40: this read "target ADU [0]" with no unit, no
                        placeholder and no statement of what 0 meant — on the one
                        field that decides whether auto-exposure runs at all, next
                        to a 120s exposure that saturates against a panel. The
                        exposure clamp + the 25000 ADU default now live in
                        frameTypeDefaults(); the reason is stated here in words. */}
                    {(s.frame_type === "Flat") && (
                      <div className="flex flex-wrap items-end gap-2">
                        {/* Width lives on the LABEL, not the input: `.field` sets
                            width:100% from an unlayered rule that a Tailwind width
                            utility on the input does not reliably beat (the same
                            trap `!w-16` hits in the Automation panel). Sizing the
                            wrapper makes the 100% resolve to the size we want. */}
                        <label className="flex flex-col gap-0.5 w-[110px]">
                          <span className="label">target ADU</span>
                          <input
                            className={`field !py-1 ${running ? "opacity-50 cursor-not-allowed" : ""}`}
                            inputMode="numeric"
                            placeholder={String(FLAT_ADU_TARGET)}
                            aria-label={`Target ADU for flat auto-exposure — step ${si + 1} of ${t.name}`}
                            aria-disabled={running || undefined}
                            value={s.adu_target ?? 0}
                            onChange={(e) => !running && patchStep(ti, si, { adu_target: num(e.target.value, s.adu_target ?? 0) })} />
                        </label>
                        <span className="text-[11px] text-dim pb-1.5 min-w-0">
                          {(s.adu_target ?? 0) > 0
                            ? `ADU — the exposure above is solved to hit this level (${FLAT_ADU_TARGET} ≈ 38% of a 16-bit well)`
                            : `ADU — 0 means auto-exposure is OFF: the ${s.exposure_s}s above is used as-is`}
                        </span>
                      </div>
                    )}
                    {stepVerdict === "too_short" && (
                      <p className="text-[10px] text-warn pl-1">
                        read-noise limited · sky-limited ≈ {Math.round(stepSkyLimitedS!)}s
                      </p>
                    )}
                    {stepVerdict === "good" && (
                      <p className="text-[10px] text-dim pl-1">✓ sky-limited</p>
                    )}
                    {stepVerdict === "long" && (
                      <p className="text-[10px] text-dim pl-1">longer than needed</p>
                    )}
                    </div>
                    );
                  })}
                  {/* (the column-caption row that used to live HERE — below the
                      last step — is gone: every field now carries its own
                      caption inline, which is what fixes #26 at narrow widths.) */}
                  {t.steps.some((s) => isExposureValueInvalid(s.exposure_s)) && (
                    <p className="text-[11px] text-bad">
                      Exposure must be 0–{EXPOSURE_MAX_S}s — fix the highlighted step(s) before running.
                    </p>
                  )}
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
                      <div className="inline-flex items-center gap-1.5">
                        {/* Frame the group in the Sky Atlas (wave-3 §5), seeded
                            from the first panel's coords; group name as id.
                            Never disabled — framing works offline. */}
                        <IconButton
                          icon="frame"
                          label={`Frame ${blk.group} in the Sky Atlas`}
                          onClick={() => frameInAtlas(blk.group, blk.group, members[0])}
                        />
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
      <div className="flex flex-col gap-4 min-w-0">
        {/* Unified Plan panel (G2): plan identity (name + saved/unsaved cue +
            frames/integration) merged with the plan library (save/load/export/
            import) into one harmonious surface. Sessions stays its own region
            below. */}
        <PlanLibraryPanel />

        <Panel title="Automation">
          <div className="flex flex-col gap-3 text-xs">
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">guide during sequence</span>
              <Toggle checked={plan.guide} onChange={(v) => setPlan({ ...plan, guide: v })}
                label="Guide during sequence" />
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
              <Toggle checked={plan.apply_filter_offsets} onChange={(v) => setPlan({ ...plan, apply_filter_offsets: v })}
                label="Apply filter focus offsets" />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim inline-flex items-center gap-1">
                meridian flip (German mount)
                <InfoDot content={HELP.meridianFlip} label="About meridian flip" />
              </span>
              <Toggle checked={plan.meridian_flip} onChange={(v) => setPlan({ ...plan, meridian_flip: v })}
                label="Meridian flip (German mount)" />
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
              <Toggle checked={plan.safety_check ?? true} onChange={(v) => setPlan({ ...plan, safety_check: v })}
                label="Safety monitor gate" />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">recover guiding if lost</span>
              <Toggle checked={plan.recover_guiding} onChange={(v) => setPlan({ ...plan, recover_guiding: v })}
                label="Recover guiding if lost" />
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
              <Toggle checked={plan.park_when_done} onChange={(v) => setPlan({ ...plan, park_when_done: v })}
                label="Park mount when done" />
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="text-dim">warm camera when done</span>
              <Toggle checked={plan.warm_cooler_when_done} onChange={(v) => setPlan({ ...plan, warm_cooler_when_done: v })}
                label="Warm camera when done" />
            </label>
            {/* --- multi-night quota + reject guards (sessions spec §3/§7).
                Each numeric guard is individually disable-able; the 0 state is
                labeled "off" EXPLICITLY (user requirement). --- */}
            <div className="border-t border-line pt-3 flex flex-col gap-3">
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim inline-flex items-center gap-1">
                  count = accepted frames
                  <InfoDot
                    label="About accepted-frame counting"
                    content="Each step's count becomes a quota of ACCEPTED frames: rejected frames don't count and the step keeps shooting — across nights if needed — until the quota is met. Rejected frames are kept on disk for regrading. Off = classic attempt counting."
                  />
                </span>
                <Toggle checked={(plan.count_mode ?? "attempts") === "accepted"}
                  onChange={(v) => setPlan({ ...plan, count_mode: v ? "accepted" : "attempts" })}
                  label="Count accepted frames instead of attempts" />
              </label>
              {/* REVIEW #30. `count_mode: "attempts"` is the default, and with
                  every gate off it is harmless — an attempt IS a keeper. The
                  moment any gate is armed it stops being harmless: you ask for
                  20 × 300s and you get 20 ATTEMPTS, of which an unknown number
                  are rejected, and nothing on the screen ever says so. So the
                  warning is gate-driven, states which gates made it true, and
                  carries the one-tap fix rather than pointing at a toggle. */}
              {countShrinksSilently(plan) && (
                <div className="flex flex-col gap-2 border border-warn/50 bg-warn/5 px-2.5 py-2">
                  <p className="text-[11px] text-ink leading-snug inline-flex items-start gap-1.5">
                    <Icon name="alert" size={13} className="text-warn mt-px shrink-0" aria-hidden />
                    <span>
                      <span className="text-warn">Counting attempts</span> with{" "}
                      {armedQualityGates(plan).join(" + ")} armed: a rejected frame
                      still uses up its count, so {totalFrames} requested frames can
                      deliver fewer keepers — and nothing reports the shortfall.
                    </span>
                  </p>
                  <button
                    className="btn tap min-h-[44px] !py-1 !text-[11px] self-start"
                    onClick={() => setPlan({ ...plan, count_mode: "accepted" })}
                  >
                    Count accepted frames instead
                  </button>
                </div>
              )}
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim">min stars per frame</span>
                <span className="inline-flex items-center gap-2">
                  {(plan.min_stars ?? 0) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.min_stars ?? 0}
                    onChange={(e) => setPlan({ ...plan, min_stars: Math.max(0, Math.round(num(e.target.value, plan.min_stars ?? 0))) })} />
                </span>
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim">max guide RMS (arcsec)</span>
                <span className="inline-flex items-center gap-2">
                  {(plan.max_guide_rms ?? 0) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.max_guide_rms ?? 0}
                    onChange={(e) => setPlan({ ...plan, max_guide_rms: Math.max(0, num(e.target.value, plan.max_guide_rms ?? 0)) })} />
                </span>
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim inline-flex items-center gap-1">
                  max eccentricity (0–1)
                  <InfoDot
                    label="About the eccentricity gate"
                    content="Reject a frame whose stars are too elongated (trailing / tilt / coma): the median star eccentricity across the frame. 0 = off." />
                </span>
                <span className="inline-flex items-center gap-2">
                  {(plan.max_eccentricity ?? 0) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.max_eccentricity ?? 0}
                    onChange={(e) => setPlan({ ...plan, max_eccentricity: Math.min(1, Math.max(0, num(e.target.value, plan.max_eccentricity ?? 0))) })} />
                </span>
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim inline-flex items-center gap-1">
                  skip step after N rejects
                  <InfoDot
                    label="About the per-step reject guard"
                    content="Accepted-count mode only: after N consecutive rejected frames on one step, skip to the next step/target. The shortfall stays in the session ledger for another night."
                  />
                </span>
                <span className="inline-flex items-center gap-2">
                  {(plan.max_consecutive_rejects ?? 10) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.max_consecutive_rejects ?? 10}
                    onChange={(e) => setPlan({ ...plan, max_consecutive_rejects: Math.max(0, Math.round(num(e.target.value, plan.max_consecutive_rejects ?? 10))) })} />
                </span>
              </label>
              <label className="flex items-center justify-between gap-2">
                <span className="text-dim inline-flex items-center gap-1">
                  end night after N rejects
                  <InfoDot
                    label="About the per-night reject guard"
                    content="Accepted-count mode only: after N consecutive rejects ACROSS targets (counter resets on any accepted frame), end the night early and leave the session resumable — the proto cloud detector."
                  />
                </span>
                <span className="inline-flex items-center gap-2">
                  {(plan.max_consecutive_rejects_night ?? 20) === 0 && (
                    <span className="text-[10px] uppercase tracking-widest text-dim">off</span>
                  )}
                  <input className="field !w-16 !py-1" value={plan.max_consecutive_rejects_night ?? 20}
                    onChange={(e) => setPlan({ ...plan, max_consecutive_rejects_night: Math.max(0, Math.round(num(e.target.value, plan.max_consecutive_rejects_night ?? 20))) })} />
                </span>
              </label>
            </div>
          </div>
        </Panel>

        {/* Conditional sequencer (PRO-3): optional when-trigger-do-action rules
            layered on the fixed plan. Empty === byte-identical run. One more
            optional sub-panel, sits with the plan settings. */}
        <InstructionsPanel plan={plan} setPlan={setPlan} canWrite={canRun} />

        {/* Multi-night sessions (sessions spec §7): resume/manage cards for
            non-abandoned sessions. Self-hides when there are none. Its OWN
            clearly-bounded region — deliberately NOT merged into the Plan panel
            (G2). */}
        <SessionsPanel />

        {/* Pre-flight gate (F-P0.1): the strip is always visible once the plan has
            frames; Review opens the modal. Run is disabled when blocked and only
            ever starts via the modal's onProceed (which threads `force`). */}
        <PreflightStrip plan={plan} onReview={() => setPreflightOpen(true)} />

        {/* Run stays VISIBLE for every role (R4B-PLAN-02: stable screen anatomy —
            permissions change enabled state, not what exists). Non-holders get it
            disabled with a lock note whose copy derives from the SAME capability
            the control enforces (control.mount — accessPhrase, R4B-PLAN-01). */}
        <button className="btn btn-accent !py-3 !text-sm"
          disabled={!canRun || running || totalFrames === 0 || verdict === "blocked"}
          onClick={() => setPreflightOpen(true)}>
          ≡ Run Sequence
        </button>
        {!canRun && (
          <p className="text-[11px] text-warn text-center inline-flex items-center justify-center gap-2 py-2">
            <ReadOnlyBadge label="View only"
              reason={`Running a sequence needs ${accessPhrase("control.mount")}.`} />
            Running a sequence needs {accessPhrase("control.mount")}.
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
