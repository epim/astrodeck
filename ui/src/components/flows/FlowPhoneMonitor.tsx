// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// FlowPhoneMonitor.tsx — the phone MONITOR tab. README §5, ref
// `11-phone-monitor-390px.png`.
//
// Four readouts, a scrolling log, and a full-width 56px RUN/STOP.
//
// WHERE THE READOUTS COME FROM (#189 S5, U-07). While the run the rig is on
// is this flow's (`flowRunLive`: the session the sequence state writes is the
// one the progress route counts), all four are the sequence state's: STATE is
// the engine's own state, ETA its `progress.eta_s` with "hops not yet costed"
// under it while that clock prices hops it has not measured, STAGE the
// target, or for a mosaic the panel and pass (`M31 2-3 · pass 2`, and
// `M31 · waiting for the meridian` across a meridian wait, with nothing
// site-derived), and FRAMES its `frames_done / frames_total`. Otherwise they
// are the store's own `run.phase` / `run.etaS` / `run.curStage` /
// `run.frames`, which nothing on the server writes (§G-1: there is no
// `flow.node` topic, no `flow.log` topic and no run-state GET), so they read
// IDLE / — / — / 0: another flow's run is not this flow's, and its clock on
// this tab would describe the wrong ledger. A client-side countdown from an
// assumed total, or a stage name inferred from the graph, would look identical
// to one the rig computed — which is the exact green-while-wrong defect this
// project keeps paying for. All four come from `useFlowRunReadouts`, which the
// #/next phone stage sheet reads too.
//
// The RUN button shares `useFlowRunControls` with the header: one abort route,
// one timed-out-is-not-failed rule, one honest-disabled sentence, and one copy
// (`CONTINUE M31 MOSAIC (night 3, 412/1890 subs)` with a dormant session),
// with START OVER under it behind a confirm.
import type { JSX } from "react";

import { useStore } from "../../store";
import { HonestButton } from "../ui";
import { START_OVER_LABEL, useFlowRunControls, useFlowRunReadouts } from "./flowRunControls";
import { RunWords, formatEta } from "./FlowHeader";
import { LOG_TONE_CLASS, emptyLogText, logTail, logTime } from "./FlowLogStrip";

/** One label-over-value cell. `value` is mono so a changing number does not
 *  reflow the row under a thumb. `sub` is a qualifier the value needs to be
 *  read right (the ETA's "hops not yet costed"), on its own line so the
 *  number keeps its width. */
function Readout({ label, value, sub, testId }: {
  label: string; value: string; sub?: string | null; testId?: string;
}): JSX.Element {
  return (
    <div className="flex flex-col gap-1 min-w-0" data-testid={testId}>
      <span className="font-display font-semibold text-[9px] tracking-[0.2em] text-faint">
        {label}
      </span>
      <span className="font-mono text-[14px] text-ink truncate">{value}</span>
      {sub && <span className="font-mono text-[10px] text-dim">{sub}</span>}
    </div>
  );
}

export default function FlowPhoneMonitor(): JSX.Element {
  // The readouts arrive as one shallow-compared record of primitives, so a
  // publish that moves none of them re-renders nothing here, and the log list
  // below has its own subscription.
  const r = useFlowRunReadouts();
  const logs = useStore((s) => s.flows.logs);

  const { running, reason, explain, act, copy, startOver } = useFlowRunControls();
  const lines = logTail(logs);

  return (
    <div
      data-flows-phone-tab="monitor"
      className="flex-1 min-w-0 overflow-y-auto flex flex-col gap-3.5 px-3.5 py-3.5"
    >
      <div className="panel !p-3.5 grid grid-cols-2 gap-y-3.5 gap-x-3">
        {/* The STATE word is upper-cased for the readout. Fed from the
            sequence state it is the engine's state in the flow's own
            vocabulary (an abort's wind-down reads STOPPING, the phase word
            for it); otherwise it IS the phase, not a second vocabulary. */}
        <Readout label="STATE" value={r.state} testId="flow-monitor-state" />
        <Readout label="ETA" value={formatEta(r.etaS)} sub={r.etaNote} testId="flow-monitor-eta" />
        <Readout label="STAGE" value={r.stage} testId="flow-monitor-stage" />
        <Readout
          label="FRAMES"
          testId="flow-monitor-frames"
          // A goal the server has not stated is left off entirely rather than
          // printed as "/ 0", which reads as a finished run of nothing.
          value={r.frameGoal == null ? String(r.frames) : `${r.frames} / ${r.frameGoal}`}
        />
      </div>

      <div
        role="log"
        className="panel !p-3 flex-1 min-h-[160px] overflow-y-auto flex flex-col gap-[3px]"
      >
        {/* An empty log says why it is empty, never "Idle" (#529): the
            readouts above say what the run is doing, and under a live run
            of this flow (`r.fed`, their own rule) "Idle" contradicted STATE. */}
        {lines.length === 0 ? (
          <span className="font-mono text-[10.5px] text-faint">{emptyLogText(r.fed)}</span>
        ) : (
          lines.map((l) => (
            <div key={l.id} className={`font-mono text-[10.5px] ${LOG_TONE_CLASS[l.tone]}`}>
              <span className="text-faint">{logTime(l.ts)}</span>
              {"  "}
              {l.msg}
            </div>
          ))
        )}
      </div>

      {/* 56px, full width (README §5). Honest-disabled: it stays pressable and
          says why. ■ STOP is a plain single tap — ui.tsx's own rule: emergency
          motion stops never go through HoldButton. At 390 px a CONTINUE line
          is wider than the button, so `RunWords` cuts the flow's name and
          keeps the parenthetical whole. */}
      <HonestButton
        className={`btn w-full !min-h-[56px] !text-[13px] !tracking-[0.14em] ${running
          ? "!border-bad !text-bad !bg-[color-mix(in_srgb,var(--bad)_12%,transparent)]"
          : "!border-accent2 !bg-accent-fill !text-accent"}`}
        reason={reason}
        onExplain={explain}
        onClick={act}
      >
        <RunWords copy={copy} />
      </HonestButton>

      {/* START OVER, under CONTINUE and only under it (spec 5.9). Smaller than
          the button above it, so the two most consequential presses on this
          tab are not one thumb-width apart at the same weight; the confirm it
          asks is the second guard. */}
      {copy.verb === "CONTINUE" && (
        <HonestButton
          className="btn w-full !min-h-[44px] !text-[11px] !tracking-[0.12em]"
          reason={reason}
          onExplain={explain}
          onClick={startOver}
        >
          <span data-testid="flow-start-over">{START_OVER_LABEL}</span>
        </HonestButton>
      )}
    </div>
  );
}
