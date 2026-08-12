// FlowPhoneMonitor.tsx — the phone MONITOR tab. README §5, ref
// `11-phone-monitor-390px.png`.
//
// Four readouts, a scrolling log, and a full-width 56px RUN/STOP.
//
// ⚠ §G-1 GOVERNS THIS WHOLE FILE AND IT IS NOT PAPERED OVER. `run.phase`,
// `run.etaS`, `run.curStage` and `run.frames` are all written by nothing: there
// is no `flow.node` topic, no `flow.log` topic, and no run-state GET
// (contract's finding 5). So this panel renders the store's values honestly and
// they will read IDLE / — / — / 0 during a real run. A client-side countdown
// from an assumed total, or a stage name inferred from the graph, would look
// identical to one the rig computed — which is the exact green-while-wrong
// defect this project keeps paying for. The reference capture shows the same
// idle values for the same reason.
//
// The RUN button shares `useFlowRunControls` with the header: one abort route,
// one timed-out-is-not-failed rule, one honest-disabled sentence.
import type { JSX } from "react";

import { useStore } from "../../store";
import { HonestButton } from "../ui";
import { useFlowRunControls } from "./flowRunControls";
import { formatEta } from "./FlowHeader";
import { LOG_TONE_CLASS, logTail, logTime, IDLE_LOG_TEXT } from "./FlowLogStrip";

/** One label-over-value cell. `value` is mono so a changing number does not
 *  reflow the row under a thumb. */
function Readout({ label, value }: { label: string; value: string }): JSX.Element {
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <span className="font-display font-semibold text-[9px] tracking-[0.2em] text-faint">
        {label}
      </span>
      <span className="font-mono text-[14px] text-ink truncate">{value}</span>
    </div>
  );
}

export default function FlowPhoneMonitor(): JSX.Element {
  // Four separate primitive selectors rather than one object: a frame counter
  // ticking must not re-render the log list beside it.
  const phase = useStore((s) => s.flows.run.phase);
  const etaS = useStore((s) => s.flows.run.etaS);
  const curStage = useStore((s) => s.flows.run.curStage);
  const frames = useStore((s) => s.flows.run.frames);
  const frameGoal = useStore((s) => s.flows.run.frameGoal);
  const logs = useStore((s) => s.flows.logs);

  const { running, reason, explain, act } = useFlowRunControls();
  const lines = logTail(logs);

  return (
    <div
      data-flows-phone-tab="monitor"
      className="flex-1 min-w-0 overflow-y-auto flex flex-col gap-3.5 px-3.5 py-3.5"
    >
      <div className="panel !p-3.5 grid grid-cols-2 gap-y-3.5 gap-x-3">
        {/* The STATE word is upper-cased for the readout but it IS the phase,
            not a second vocabulary — nothing here can say RUNNING while
            `data-flows-run` says idle. */}
        <Readout label="STATE" value={phase.toUpperCase()} />
        <Readout label="ETA" value={formatEta(etaS)} />
        <Readout label="STAGE" value={curStage} />
        <Readout
          label="FRAMES"
          // A goal the server has not stated is left off entirely rather than
          // printed as "/ 0", which reads as a finished run of nothing.
          value={frameGoal == null ? String(frames) : `${frames} / ${frameGoal}`}
        />
      </div>

      <div
        role="log"
        className="panel !p-3 flex-1 min-h-[160px] overflow-y-auto flex flex-col gap-[3px]"
      >
        {lines.length === 0 ? (
          <span className="font-mono text-[10.5px] text-faint">{IDLE_LOG_TEXT}</span>
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
          motion stops never go through HoldButton. */}
      <HonestButton
        className={`btn w-full !min-h-[56px] !text-[13px] !tracking-[0.14em] ${running
          ? "!border-bad !text-bad !bg-[color-mix(in_srgb,var(--bad)_12%,transparent)]"
          : "!border-accent2 !bg-accent-fill !text-accent"}`}
        reason={reason}
        onExplain={explain}
        onClick={act}
      >
        <span aria-hidden="true">{running ? "■" : "▶"}</span>
        {running ? " STOP" : " RUN"}
      </HonestButton>
    </div>
  );
}
