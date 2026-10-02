// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// SendToWizardSheet.tsx - Send to Flow Wizard: ONE stepped sheet for both UIs
// (#196; spec 2026-09-23 flows mosaic, Revision 2 ruling 4, D13, section 8 S6).
//
// A door (the classic Atlas, the #/next Sky FRAME) hands over what it framed
// as a `WizardPrefill`, and this sheet walks the rest: TARGET, FRAMING,
// FILTERS, GUIDING, REVIEW. EACH STEP SHOWS WHAT ARRIVED AND ASKS ONLY WHAT IS
// MISSING (wizardModel `stepReason`, the one rule for the NEXT lock and the
// line under each step): a name and coordinates that came from the framing
// are shown, not asked; a mosaic with no angle is asked for one; a rig with no
// camera field is offered a single target, since the grid cannot be laid out
// without one; filters, counts and guiding are always asked, because a door
// knows none of them. The stop condition and auto-resume steps are not built
// (#191, #195).
//
// ONE SHEET, NO FORK (D13). The classic `SendToWizardHost` and the #/next
// `flowWizard` sheet both mount this file through its lazy door (index.ts);
// each passes only what differs between the UIs: how to close, how to go back
// to its framing (EDIT FRAMING), where the editor opens, and where a started
// run is watched. Every rule, every word and the one request live here and in
// wizardModel.ts.
//
// GENERATE IS ONE REQUEST. `POST /api/flows/wizard` with the body
// `wizardBody` builds from the prefill and the answers; the route generates
// AND saves (server `flows/wizard.py`), so what comes back is a flow in My
// flows with an id. A second press while the first is out is refused, by a
// ref as well as the busy reason, since two presses inside one render would
// both read the old state and save two flows.
//
// THE REVIEW IS THE SERVER'S. After the save the sheet asks `POST
// /api/flows/{id}/compile` for the saved flow and prints its readouts (subs
// and hours, per panel and in all) and the doctor's issues, never a number of
// its own (wizardModel `reviewBlocks`). RUN goes through the one loop every
// RUN button shares, `runAnsweringQuestions` on `POST /api/flows/{id}/run`,
// with every gate that route applies, and is locked with its reason while the
// compile shows a danger or a loss (wizardModel `runLock`). Both RUN and OPEN
// IN EDITOR first open the saved flow in the store (`flowsOpen`), which saves
// the flow that was open there if it holds edits and refuses, saying so, when
// that save does not keep them (#450), and `flowsRun` runs whatever record is
// open, so neither acts unless the saved flow is the one that landed
// (`openSaved`). This sheet kept its own copy of the save-first rule until
// #450 moved it into `flowsOpen` for every caller.
//
// CLOSE WAITS FOR ITS ANSWER. While GENERATE, RUN or OPEN IN EDITOR is out,
// CLOSE, Escape and the scrim refuse and say why (`CLOSE_WHILE_BUSY`): a
// GENERATE closed mid-flight still saves a flow, which the operator would
// then never be shown, and a RUN or OPEN IN EDITOR answered after the close
// would move the host's screen from under whatever the operator did next.

import "./wizard.css";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type JSX, type ReactNode } from "react";
import { Overlay } from "../../Overlay";
import { HonestButton } from "../../ui";
import { useStore } from "../../../store";
import { accessPhrase, useCanControlCapture, useCanControlMount, useRoleConnected } from "../../../lib/caps";
import { effectiveOptics } from "../../../lib/effective";
import { fovFromOptics } from "../../../lib/framing";
import { flowsApi, type FlowCompileResult, type FlowUnmapped } from "../../../lib/flowsApi";
import { cyclePlanRows, resolveWheel, setSlotExposure, toggleSlot } from "../cyclePlanRows";
import { askContinue, isRunPhaseLive, runAnsweringQuestions, runBlockedReason } from "../flowRunControls";
import { FLOW_NOT_OPENED } from "../flowsSlice";
import {
  ANGLE_WORDS, MOSAIC_ANGLES, STEP_TITLE, STEPS, UNGUIDED_CAP_S,
  angleArrived, angleOf, angleWords, fieldChanged, fieldWords, firstGap, initialAnswers,
  isMosaicFraming, overCap, planRows, plansMosaic, reviewBlocks, reviewFindings, runLock,
  savedFlowOf, stepReason, targetOf, wizardBody,
  type SavedFlow, type WizardAnswers, type WizardPrefill, type WizardRig, type WizardStep,
} from "./wizardModel";

// ------------------------------------------------------------------ words

export const WIZARD_TITLE = "SEND TO FLOW WIZARD";
/** The tag beside a value the door sent, so the operator can tell what the
 *  framing decided from what this sheet is asking. */
export const FROM_FRAMING = "FROM THE FRAMING";
export const GENERATE_FAILED = "Could not generate the flow";
export const BUSY = "Already working on it - one moment.";
export const RUN_DID_NOT_START = "The flow did not start";
/** The store's own title for an open that did not land (flowsSlice
 *  `FLOW_NOT_OPENED`), so this sheet's report of a failed open and the
 *  store's refusal to open over unsaved edits coalesce into one card. */
export const OPEN_FAILED = FLOW_NOT_OPENED;
export const NO_WHEEL_NOTE =
  "No filter wheel reports its slots, so these are the seven a FILTER CYCLE assumes.";
export const ONE_TARGET_ANGLE =
  "A single target is planned at any angle: set its camera angle on the TARGET in the editor.";
/** ONE_TARGET_ANGLE, led by the PA the door framed at when one arrived (#460):
 *  the one-target body carries no angle, so the number is said here or not at
 *  all. */
export function oneTargetAngleNote(paDeg: number | null): string {
  return paDeg !== null && Number.isFinite(paDeg)
    ? `Framed at PA ${paDeg.toFixed(1)} deg. ${ONE_TARGET_ANGLE}`
    : ONE_TARGET_ANGLE;
}
export const PLANNED_AS_ONE =
  "Planned as one target at the framing's centre: the grid, its skipped panels and its angle are not sent.";
export const DOCTOR_CLEAR = "The doctor raised nothing.";
/** Why CLOSE, Escape and the scrim refuse while a request of this sheet is
 *  out: GENERATE saves a flow the operator would then never be shown, and RUN
 *  or OPEN IN EDITOR would move a host's screen after the sheet had gone. */
export const CLOSE_WHILE_BUSY =
  "Waiting for the server: this closes once it has answered, so what it did is shown here first.";
export const GENERATE_SAYS =
  "GENERATE saves the flow to My flows. The numbers on this step then come from the server's compile of what it saved.";

/** THE TITLE WRAPS rather than cutting the target's name (#189 S6, found by
 *  the real-page probe, routes_s5_s6.json). `.swz-title` is one line with an
 *  ellipsis, and "SEND TO FLOW WIZARD - " leaves a 390 px phone about ten
 *  characters of name: the Sky walk's framing of the Wild Duck Cluster read
 *  "SEND TO FLOW WIZARD - Wild Duck Clust...", on every step, the one line
 *  naming what the flow will be built for. The header's own `min-height`
 *  lets a second line in, so a long name takes one. */
const TITLE_WRAPS: CSSProperties = { whiteSpace: "normal", overflowWrap: "anywhere" };

/** THE RAIL'S WORDS SHARE ONE LINE (#189 S6, the same probe). A step behind
 *  the current one is a 32 px button (`.swz-rail-btn`) and the rest are plain
 *  words, and `.swz-steps` is a flex row stretching each item to the line:
 *  the buttons centred their words and the plain steps sat at the top, so on
 *  every step after the first the steps behind it read lower than the rest
 *  (on FILTERS, "1 TARGET 2 FRAMING" 8.5 px below "3 FILTERS 4 GUIDING
 *  5 REVIEW"). Centred, the rail reads as one line. */
const RAIL_ALIGNS: CSSProperties = { alignItems: "center" };

// ------------------------------------------------------------------ props

export interface SendToWizardSheetProps {
  /** What the door framed. */
  prefill: WizardPrefill;
  onClose: () => void;
  /** EDIT FRAMING: back to the door's framing. Absent when the sheet was
   *  opened with no framing to go back to; there is then no button. */
  onEditFraming?: () => void;
  /** Where this UI's editor opens the saved flow. Called once the flow is
   *  open in the store, so a host only navigates. */
  onOpenInEditor: (flowId: string) => void;
  /** The run started: take the operator to where a run is watched. */
  onStarted: () => void;
}

// ------------------------------------------------------------------ bits

function Arrived({ k, label, children }: { k: string; label: string; children: ReactNode }): JSX.Element {
  return (
    <div className="swz-row" data-testid={`wizard-arrived-${k}`}>
      <span className="swz-label">{label}</span>
      <span className="swz-value">{children}</span>
      <span className="swz-tag">{FROM_FRAMING}</span>
    </div>
  );
}

function Ask({ k, label, value, onChange, placeholder, mode }: {
  k: string; label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; mode?: "text" | "decimal" | "numeric";
}): JSX.Element {
  return (
    <label className="swz-row">
      <span className="swz-label">{label}</span>
      <input
        className="field swz-input"
        data-testid={`wizard-ask-${k}`}
        value={value}
        placeholder={placeholder}
        inputMode={mode ?? "text"}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

const levelWord = (level: string) => (level === "danger" ? "DANGER" : level === "warn" ? "WARN" : "NOTE");

// ------------------------------------------------------------------ sheet

export default function SendToWizardSheet(p: SendToWizardSheetProps): JSX.Element {
  const { prefill } = p;

  // ---- the store, read narrowly: one field per selector
  const statusOptics = useStore((s) => s.status?.optics ?? null);
  const config = useStore((s) => s.config);
  const wheelNames = useStore((s) => s.status?.filterwheel?.names);
  const wheelOpaque = useStore((s) => s.status?.filterwheel?.opaque);
  const flowsRun = useStore((s) => s.flowsRun);
  const pushConfirm = useStore((s) => s.pushConfirm);
  const resolveConfirm = useStore((s) => s.resolveConfirm);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const canCapture = useCanControlCapture();
  const canMount = useCanControlMount();
  const camera = useRoleConnected("camera");

  // ---- the live rig: the camera field, from the resolver the Target
  // modal's MATCH CAMERA reads, so both say one field for one rig.
  const rig: WizardRig = useMemo(() => {
    const optics = effectiveOptics(config, config?.optics ?? null, statusOptics ?? config?.optics_computed ?? null);
    const f = optics ? fovFromOptics(optics) : null;
    return { field: f && f.fov_x_deg > 0 && f.fov_y_deg > 0 ? { xDeg: f.fov_x_deg, yDeg: f.fov_y_deg } : null };
  }, [config, statusOptics]);
  const { filters: wheel, fromRig } = resolveWheel(wheelNames, wheelOpaque);

  // ---- the answers, and where the sheet is
  const [answers, setAnswers] = useState<WizardAnswers>(() => initialAnswers(prefill));
  const set = useCallback((over: Partial<WizardAnswers>) => setAnswers((a) => ({ ...a, ...over })), []);
  const [stepIx, setStepIx] = useState(0);
  const step: WizardStep = STEPS[stepIx];

  // ---- the answer and its check
  const [generating, setGenerating] = useState(false);
  const inFlight = useRef(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedFlow | null>(null);
  const [compiled, setCompiled] = useState<FlowCompileResult | null>(null);
  const [compileFailed, setCompileFailed] = useState<string | null>(null);
  const [acting, setActing] = useState<"run" | "open" | null>(null);

  // Whether the sheet is still mounted, for the answers that land after an
  // await. Set in the effect as well as cleared in its cleanup, so a
  // development StrictMode remount leaves it true.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const explain = useCallback(
    (reason: string) => enqueueToast({ level: "warning", title: reason }),
    [enqueueToast],
  );
  const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

  const target = targetOf(prefill, answers);
  const mosaicFraming = isMosaicFraming(prefill);
  const mosaic = plansMosaic(prefill, answers);
  const reason = stepReason(step, prefill, answers, rig);
  const gap = firstGap(prefill, answers, rig);

  // ---- GENERATE: one request, then the server's check of what it saved
  const check = useCallback(async (id: string) => {
    setCompiled(null);
    setCompileFailed(null);
    try {
      const answer = await flowsApi.compile(id);
      if (alive.current) setCompiled(answer);
    } catch (e) {
      if (alive.current) setCompileFailed(errText(e));
    }
  }, []);

  const generateReason = !canCapture
    ? `Generating a flow needs ${accessPhrase("control.capture")}.`
    : generating ? BUSY
      : gap !== null ? `${STEP_TITLE[gap.step]}: ${gap.reason}`
        : null;

  const generate = async (): Promise<void> => {
    if (generateReason !== null || inFlight.current) return;
    inFlight.current = true;
    setGenerating(true);
    setGenError(null);
    try {
      const answer = await flowsApi.generateFromWizard(wizardBody(prefill, answers));
      const read = savedFlowOf(answer);
      if (!read.ok) throw new Error(read.why);
      // The flow is in My flows now, so the library the store holds is out of
      // date: refresh it, or the Flows screen lists nothing new and the
      // first-run guide's "Pick a target" step, which counts saved flows since
      // S6 sent it here, never ticks (#458). A failed read leaves the library
      // as it was (`flowsLoadLibrary` keeps `libraryLoaded` false on an error).
      void useStore.getState().flowsLoadLibrary();
      if (!alive.current) return;
      setSaved(read.flow);
      void check(read.flow.id);
    } catch (e) {
      // The route's 422 names the answer it refused, in the generator's own
      // words; it goes on the review, where the answers are summarised,
      // rather than into a toast that vanishes before it is read.
      if (alive.current) setGenError(errText(e));
    } finally {
      inFlight.current = false;
      if (alive.current) setGenerating(false);
    }
  };

  // ---- the saved flow into the store, for RUN and OPEN IN EDITOR
  //
  // REDUCED TO THE CHECK (#450). The save of a dirty open flow, and the
  // refusal when it does not keep its edits, were this sheet's own; they are
  // `flowsOpen`'s now, for every caller. What stays is what only the caller
  // can do: act on NOTHING unless the flow asked for is the one that landed.
  const openSaved = async (id: string): Promise<boolean> => {
    const before = useStore.getState().flows;
    await useStore.getState().flowsOpen(id);
    const now = useStore.getState().flows;
    if (now.record?.id !== id) {
      // REFUSED OVER UNSAVED EDITS: the flow that was open, not a read-only
      // Example (which `flowsOpen` replaces unsaved), is open still and
      // still dirty, and `flowsOpen` has said so in its own toast, which
      // this one would only repeat.
      const refused = before.record !== null && !before.record.readonly && before.dirty
        && now.record?.id === before.record.id && now.dirty;
      if (refused) return false;
      // `flowsOpen` swallows its failure and leaves the previous record in
      // place, so RUN from here would start THAT flow: act on nothing.
      enqueueToast({
        level: "error", title: OPEN_FAILED,
        detail: now.libraryError && now.libraryError !== before.libraryError
          ? now.libraryError : "The server answered with a different flow, so nothing was done.",
      });
      return false;
    }
    return true;
  };

  const runReason = saved === null ? null
    : runLock(compiled, compileFailed)
      ?? runBlockedReason(canMount, camera.connected, false)
      ?? (acting !== null ? BUSY : null);
  const openReason = acting !== null ? BUSY : null;
  const closeReason = generating || acting !== null ? CLOSE_WHILE_BUSY : null;
  const close = () => { if (closeReason !== null) explain(closeReason); else p.onClose(); };

  const run = async (): Promise<void> => {
    if (!saved || runReason !== null) return;
    setActing("run");
    try {
      if (!(await openSaved(saved.id))) return;
      // THE START IS JUDGED ON WHAT THIS PRESS WROTE, never on the phase as
      // it stands. `flowsRun` writes a NEW `run` with a live phase when the
      // server starts the run and leaves the old one untouched when it
      // refuses; nothing ever writes the phase back (NowEmpty's
      // RUN_PHASE_GRACE_MS), so after one run on this page it reads "running"
      // for the rest of the night, and read alone it would hand a refused
      // start (a run already live, the horizon, a held camera) to the host as
      // a started one, with the refusal only in the flow log.
      const runBefore = useStore.getState().flows.run;
      // The loop every RUN button uses: one question per request, and every
      // re-post carries every answer already given. A decline is silent.
      const { cancelled, answered } = await runAnsweringQuestions(
        flowsRun,
        async (list: FlowUnmapped[]) => {
          if (list.length === 0) return false;
          return pushConfirm({
            title: "Parts of this flow do not survive the compile",
            body: (
              <span style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {list.map((u) => <span key={u.key}>{u.detail}</span>)}
              </span>
            ),
            confirmLabel: "RUN ANYWAY",
            cancelLabel: "CANCEL",
            tone: "warn",
            mode: "confirm",
            confirmPrimary: true,
          });
        },
        (q) => askContinue(q, pushConfirm, resolveConfirm),
      );
      if (cancelled) return;
      const runAfter = useStore.getState().flows.run;
      if (runAfter !== runBefore && isRunPhaseLive(runAfter.phase)) {
        p.onStarted();
        return;
      }
      enqueueToast({
        level: "error",
        title: RUN_DID_NOT_START,
        detail: answered === null
          ? "The engine refused it; the reason is in the flow log."
          : "Answering the question was not what was blocking it; the reason is in the flow log.",
      });
    } finally {
      if (alive.current) setActing(null);
    }
  };

  const openInEditor = async (): Promise<void> => {
    if (!saved || openReason !== null) return;
    setActing("open");
    try {
      if (await openSaved(saved.id)) p.onOpenInEditor(saved.id);
    } finally {
      if (alive.current) setActing(null);
    }
  };

  // ---- navigation
  const next = () => { if (reason === null && stepIx < STEPS.length - 1) setStepIx(stepIx + 1); };
  const back = () => { if (stepIx > 0) setStepIx(stepIx - 1); };

  // ------------------------------------------------------------ the steps

  const targetStep = (
    <>
      {prefill.name.trim() !== ""
        ? <Arrived k="name" label="NAME">{prefill.name.trim()}</Arrived>
        : <Ask k="name" label="NAME" value={answers.name} onChange={(v) => set({ name: v })} placeholder="M31" />}
      {prefill.ra.trim() !== ""
        ? <Arrived k="ra" label="RA">{prefill.ra.trim()}</Arrived>
        : <Ask k="ra" label="RA" value={answers.ra} onChange={(v) => set({ ra: v })} placeholder="00h 42m 44s" />}
      {prefill.dec.trim() !== ""
        ? <Arrived k="dec" label="DEC">{prefill.dec.trim()}</Arrived>
        : <Ask k="dec" label="DEC" value={answers.dec} onChange={(v) => set({ dec: v })} placeholder="+41 16 09" />}
    </>
  );

  const angle = angleOf(prefill, answers);
  const changed = fieldChanged(prefill, rig);
  // A FRAMED PA IS AN ARRIVED ANGLE (#460). Both doors send a framing's
  // commanded PA with no angle mode (a framing holds a rotation, not a mode),
  // and the one-target body carries no angle, since the Deep-sky kind plans
  // none. Keyed on the mode alone, the note never showed for a door's single
  // frame, so a PA dialled on it was dropped without a word.
  const arrivedAngle = (prefill.angleMode !== null && prefill.angleMode !== "Any angle")
    || (prefill.paDeg !== null && Number.isFinite(prefill.paDeg));
  const framingStep = (
    <>
      <div className="swz-row" data-testid="wizard-grid">
        <span className="swz-label">GRID</span>
        <span className="swz-value">
          {mosaicFraming
            ? `${prefill.cols} x ${prefill.rows} panels (columns x rows)`
            : "one panel: a single target"}
        </span>
        <span className="swz-tag">{FROM_FRAMING}</span>
      </div>
      {mosaicFraming && (
        <div className="swz-row" data-testid="wizard-overlap">
          <span className="swz-label">OVERLAP</span>
          <span className="swz-value">
            {prefill.overlapPct !== null ? `${prefill.overlapPct}%` : "the wizard's default"}
          </span>
        </div>
      )}
      {mosaicFraming && prefill.skip.trim() !== "" && (
        <div className="swz-row" data-testid="wizard-skip">
          <span className="swz-label">SKIPPED</span>
          <span className="swz-value swz-mono">{prefill.skip.trim()}</span>
        </div>
      )}
      {prefill.fov && (
        <div className="swz-row" data-testid="wizard-fov">
          <span className="swz-label">FRAMED WITH</span>
          <span className="swz-value">{fieldWords(prefill.fov)}</span>
        </div>
      )}
      {rig.field && (
        <div className="swz-row" data-testid="wizard-rig-field">
          <span className="swz-label">RIG'S FIELD</span>
          <span className="swz-value">{fieldWords(rig.field)}</span>
        </div>
      )}
      {changed && mosaic && <p className="swz-note swz-banner" data-testid="wizard-field-changed">{changed}</p>}

      {mosaicFraming && rig.field === null && !answers.single && (
        <div className="swz-row swz-note" data-testid="wizard-no-optics">
          <button type="button" className="swz-btn" data-testid="wizard-single-target" onClick={() => set({ single: true })}>
            PLAN AS ONE TARGET
          </button>
        </div>
      )}
      {mosaicFraming && answers.single && (
        <div className="swz-row swz-note" data-testid="wizard-planned-single">
          <span className="swz-value">{PLANNED_AS_ONE}</span>
          <button type="button" className="swz-btn" data-testid="wizard-mosaic-again" onClick={() => set({ single: false })}>
            PLAN THE MOSAIC
          </button>
        </div>
      )}

      {mosaic && rig.field !== null && (angleArrived(prefill)
        ? <Arrived k="angle" label="ANGLE">{angleWords(prefill.angleMode as typeof MOSAIC_ANGLES[number], prefill.paDeg as number)}</Arrived>
        : (
          <>
            <div className="swz-row swz-chips" role="radiogroup" aria-label="Camera angle">
              <span className="swz-label">ANGLE</span>
              {MOSAIC_ANGLES.map((m, i) => (
                <button
                  key={m} type="button" role="radio" aria-checked={answers.angleMode === m}
                  data-testid={`wizard-angle-mode-${i}`}
                  className={`swz-btn${answers.angleMode === m ? " swz-on" : ""}`}
                  onClick={() => set({ angleMode: m })}
                >
                  {ANGLE_WORDS[m]}
                </button>
              ))}
            </div>
            <Ask k="pa" label="PA (DEG)" value={answers.pa} onChange={(v) => set({ pa: v })} placeholder="30" mode="decimal" />
          </>
        ))}
      {!mosaic && arrivedAngle && <p className="swz-note" data-testid="wizard-one-target-angle">{oneTargetAngleNote(prefill.paDeg)}</p>}

      {p.onEditFraming && (
        <div className="swz-row">
          <button type="button" className="swz-btn" data-testid="wizard-edit-framing" onClick={p.onEditFraming}>
            EDIT FRAMING
          </button>
        </div>
      )}
    </>
  );

  const rows = cyclePlanRows(wheel, answers.plan);
  const filtersStep = (
    <>
      {!fromRig && <p className="swz-note" data-testid="wizard-no-wheel">{NO_WHEEL_NOTE}</p>}
      {rows.map((r) => (
        <div key={r.filter} className="swz-row">
          <button
            type="button" aria-pressed={r.on}
            data-testid={`wizard-filter-${r.filter}`}
            className={`swz-btn swz-filter${r.on ? " swz-on" : ""}`}
            onClick={() => {
              const drafts = { ...answers.drafts };
              delete drafts[r.filter];
              set({ plan: toggleSlot(wheel, answers.plan, r.filter), drafts });
            }}
          >
            {r.filter}
          </button>
          {r.on && (
            <>
              <input
                className="field swz-input swz-exp"
                data-testid={`wizard-exposure-${r.filter}`}
                inputMode="numeric"
                aria-label={`${r.filter} exposure in seconds`}
                value={answers.drafts[r.filter] ?? r.exposure}
                onChange={(e) => {
                  // The box shows what is typed; the plan takes only whole
                  // seconds, and FILTERS waits while the two disagree
                  // (wizardModel `badDraft`).
                  const v = e.target.value;
                  set({
                    plan: setSlotExposure(wheel, answers.plan, r.filter, v),
                    drafts: { ...answers.drafts, [r.filter]: v },
                  });
                }}
              />
              <span className="swz-unit">s</span>
            </>
          )}
        </div>
      ))}
      <Ask k="cycles" label="SUBS OF EACH" value={answers.cycles} onChange={(v) => set({ cycles: v })} mode="numeric" />
    </>
  );

  const over = overCap(answers.plan);
  const guidingStep = (
    <>
      <div className="swz-row swz-chips" role="radiogroup" aria-label="Guiding">
        <button
          type="button" role="radio" aria-checked={answers.guiding}
          data-testid="wizard-guiding-on"
          className={`swz-btn${answers.guiding ? " swz-on" : ""}`}
          onClick={() => set({ guiding: true })}
        >
          GUIDED
        </button>
        <button
          type="button" role="radio" aria-checked={!answers.guiding}
          data-testid="wizard-guiding-off"
          className={`swz-btn${!answers.guiding ? " swz-on" : ""}`}
          onClick={() => set({ guiding: false })}
        >
          UNGUIDED
        </button>
      </div>
      {!answers.guiding && (
        <p className="swz-note" data-testid="wizard-unguided-cap">
          {`Unguided, a sub is held to ${UNGUIDED_CAP_S} s.`}
          {over.length === 0 ? " Every ticked filter fits." : ""}
        </p>
      )}
    </>
  );

  const planWords = planRows(answers.plan).map(([f, s]) => `${f} ${s} s`).join(", ");
  const findings = reviewFindings(compiled);
  const blocks = saved ? reviewBlocks(saved, compiled) : [];
  const reviewStep = saved === null ? (
    <>
      <div className="swz-row" data-testid="wizard-summary-target">
        <span className="swz-label">TARGET</span>
        <span className="swz-value">{`${target.name} at ${target.ra}, ${target.dec}`}</span>
      </div>
      <div className="swz-row" data-testid="wizard-summary-framing">
        <span className="swz-label">FRAMING</span>
        <span className="swz-value">
          {mosaic
            ? [`mosaic, ${prefill.cols} x ${prefill.rows}`,
              prefill.skip.trim() !== "" ? `${prefill.skip.trim()} skipped` : null,
              angle ? angleWords(angle.mode, angle.paDeg) : null].filter(Boolean).join(", ")
            : "one target"}
        </span>
      </div>
      <div className="swz-row" data-testid="wizard-summary-filters">
        <span className="swz-label">FILTERS</span>
        <span className="swz-value">{planWords ? `${planWords}; ${answers.cycles.trim()} subs of each` : "none ticked"}</span>
      </div>
      <div className="swz-row" data-testid="wizard-summary-guiding">
        <span className="swz-label">GUIDING</span>
        <span className="swz-value">{answers.guiding ? "guided" : "unguided"}</span>
      </div>
      <p className="swz-note">{GENERATE_SAYS}</p>
      {genError && (
        <p className="swz-note swz-banner swz-loss" role="alert" data-testid="wizard-generate-error">
          {`${GENERATE_FAILED}: ${genError}`}
        </p>
      )}
    </>
  ) : (
    <>
      <p className="swz-note" data-testid="wizard-saved">{`Saved as ${saved.name} in My flows.`}</p>
      {saved.notes.map((n, i) => <p key={i} className="swz-note swz-banner" data-testid="wizard-note">{n}</p>)}
      <div data-testid="wizard-review-numbers">
        {compiled === null && compileFailed === null && <p className="swz-note">Waiting for the server's compile of the saved flow.</p>}
        {compiled !== null && blocks.map((b) => (
          <div key={b.id} className="swz-block" data-testid="wizard-review-block" data-node={b.id}>
            <h3 className="swz-section-h">{b.name ? b.name.toUpperCase() : "TARGET"}</h3>
            {b.ok
              ? b.lines.map((l, i) => <p key={i} className="swz-row swz-mono" data-testid="wizard-review-line">{l}</p>)
              : <p className="swz-note">{b.why}</p>}
          </div>
        ))}
      </div>
      {compiled !== null && (
        <div data-testid="wizard-review-issues">
          <h3 className="swz-section-h">DOCTOR</h3>
          {findings.structural.map((s, i) => (
            <p key={`s${i}`} className="swz-note swz-banner swz-loss" data-level="danger">{`BROKEN WIRE  ${s}`}</p>
          ))}
          {findings.issues.map((iss, i) => (
            <p key={`i${i}`} className={`swz-note swz-banner${iss.level === "danger" ? " swz-loss" : ""}`}
              data-testid="wizard-issue" data-level={iss.level}>
              {`${levelWord(iss.level)}  ${iss.text}`}
            </p>
          ))}
          {findings.losses.map((u) => (
            <p key={u.key} className="swz-note swz-banner swz-loss" data-testid="wizard-loss" data-level={u.level}>
              {`WILL NOT RUN  ${u.detail}`}
            </p>
          ))}
          {findings.structural.length + findings.issues.length + findings.losses.length === 0 && (
            <p className="swz-note" data-testid="wizard-doctor-clear">{DOCTOR_CLEAR}</p>
          )}
        </div>
      )}
      {runReason !== null && <p className="swz-note" data-testid="wizard-run-reason">{runReason}</p>}
    </>
  );

  const body: Record<WizardStep, JSX.Element> = {
    target: targetStep, framing: framingStep, filters: filtersStep, guiding: guidingStep, review: reviewStep,
  };

  // ------------------------------------------------------------ the chrome

  const head = (
    <>
      <header className="swz-head">
        <HonestButton reason={closeReason} onClick={close} onExplain={explain} className="swz-btn">
          <span data-testid="wizard-close">CLOSE</span>
        </HonestButton>
        <h2 className="swz-title" style={TITLE_WRAPS}>{target.name ? `${WIZARD_TITLE} - ${target.name}` : WIZARD_TITLE}</h2>
      </header>
      <ol className="swz-steps" aria-label="Steps" style={RAIL_ALIGNS}>
        {STEPS.map((s, i) => (
          <li key={s} className={`swz-step${i === stepIx ? " swz-on" : ""}`}
            aria-current={i === stepIx ? "step" : undefined} data-testid={`wizard-rail-${s}`}>
            {/* An earlier step is one tap away until the flow is saved; after
                that the answers are the saved flow's, changed in the editor. */}
            {i < stepIx && saved === null
              ? <button type="button" className="swz-rail-btn" onClick={() => setStepIx(i)}>{`${i + 1} ${STEP_TITLE[s]}`}</button>
              : <span>{`${i + 1} ${STEP_TITLE[s]}`}</span>}
          </li>
        ))}
      </ol>
    </>
  );

  const foot = (
    <footer className="swz-foot">
      {stepIx > 0 && saved === null && (
        <button type="button" className="swz-btn" data-testid="wizard-back" onClick={back}>BACK</button>
      )}
      <span className="swz-spacer" />
      {step !== "review" && (
        <HonestButton reason={reason} onClick={next} onExplain={explain} className="swz-btn swz-primary">
          <span data-testid="wizard-next">NEXT</span>
        </HonestButton>
      )}
      {step === "review" && saved === null && (
        <HonestButton reason={generateReason} onClick={() => { void generate(); }} onExplain={explain} className="swz-btn swz-primary">
          <span data-testid="wizard-generate">{generating ? "GENERATING..." : "GENERATE"}</span>
        </HonestButton>
      )}
      {step === "review" && saved !== null && (
        <>
          <HonestButton reason={openReason} onClick={() => { void openInEditor(); }} onExplain={explain} className="swz-btn">
            <span data-testid="wizard-open-editor">OPEN IN EDITOR</span>
          </HonestButton>
          <HonestButton reason={runReason} onClick={() => { void run(); }} onExplain={explain} className="swz-btn swz-primary">
            <span data-testid="wizard-run">{acting === "run" ? "STARTING..." : "RUN"}</span>
          </HonestButton>
        </>
      )}
    </footer>
  );

  return (
    <Overlay
      open
      label="Send to Flow Wizard"
      onClose={close}
      variant="center"
      // A FRACTION, not a dvh value (#354, #417): index.css multiplies it by
      // 100dvh, or by 100vh where dvh is unknown, so both rules clamp.
      surfaceStyle={{ "--ov-max-h-frac": "0.9", "--ov-max-h-gap": "0px" } as CSSProperties}
      head={head}
      foot={foot}
      bodyClassName="swz-host"
    >
      <div className="swz" data-testid="send-to-wizard-sheet">
        <section className="swz-body" data-testid="wizard-step" data-step={step} aria-label={STEP_TITLE[step]}>
          <h3 className="swz-section-h">{STEP_TITLE[step]}</h3>
          {body[step]}
          {reason !== null && step !== "review" && (
            <p className="swz-note swz-missing" data-testid="wizard-missing">{reason}</p>
          )}
        </section>
      </div>
    </Overlay>
  );
}
