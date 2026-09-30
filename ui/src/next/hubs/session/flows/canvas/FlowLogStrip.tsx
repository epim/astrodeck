// FlowLogStrip.tsx - the canvas's bottom log rail (wave R7 parity row A10).
//
// WHAT IT IS ALLOWED TO SHOW, AND WHY THAT MATTERS MORE THAN THE GEOMETRY. This
// strip renders `flows.logs` and nothing else. There is no `flow.log` WS event
// today, so during a real run it shows whatever `flowsAppendLog` was actually
// given and, if nothing arrived, keeps saying so. A scripted run narration here
// would be a sentence about a rig that never spoke.
//
// WHAT IT SAYS WHEN NOTHING ARRIVED (#529). Until H4 it said "Idle - no events
// yet" (canvasModel's IDLE_LOG_TEXT) through every live run, while the toolbar
// beside it offered STOP: "Idle" describes the run, and the run was not idle.
// It now says what is true of the log: while this flow's run is live, as the
// run readouts decide it, that the server publishes no log lines for a flow
// run; otherwise "no events yet". The phone stage sheet's LOG prints the same
// `emptyLogText`. Both sentences are the classic strip's, word for word, and
// restated here rather than imported: that file is a presentation module,
// which r7Parity keeps out of the #/next tree, the reason canvasModel restates
// the rest of it too. phoneReadouts.test.tsx case 8 holds all four log
// surfaces to the classic's words, so a restatement that drifts goes red.
//
// The ring lives in the store (120 lines). This shows the newest 60, newest
// first - the line you want mid-run is the one that just landed, and a
// chronological panel puts it at the bottom of a box already scrolled to the
// top.
//
// The collapsed bar is 44 px, not the legacy 30: it is a control you press in
// the dark at the scope, and 30 px was already flagged as under the house floor.

import { useMemo, type JSX } from "react";

import { flowRunLive, knownSessions } from "../../../../../components/flows/flowRunState";
import { useStore } from "../../../../../store";
import { Label, Mono } from "../../../../ui";
import { LOG_TONE, logTail, logTime } from "./canvasModel";

/** The empty log's words while no run of this flow is live. No "Idle":
 *  whether a run is idle is the STATE readout's to say, not the log's. */
export const EMPTY_LOG_TEXT = "no events yet";

/** The empty log's words while this flow's run is live (#529): no server
 *  topic carries a flow's log lines (there is no `flow.log` event), so an
 *  empty log beside a live run says why it is empty, or it reads as a run
 *  that has gone quiet. Once a `flow.log` topic exists it is false and goes. */
export const RUN_EMPTY_LOG_TEXT = "no log lines: the server publishes none for a flow run";

/** What an empty log says. `runLive` is whether this flow's run is live,
 *  as the run readouts decide it (`useFlowRunReadouts().fed`). */
export function emptyLogText(runLive: boolean): string {
  return runLive ? RUN_EMPTY_LOG_TEXT : EMPTY_LOG_TEXT;
}

export function FlowLogStrip(): JSX.Element {
  // Four exact selectors. `logs` changes identity only when a line is
  // appended; `logOpen` and `live` are booleans, so the `ui` object being
  // replaced by an unrelated `flowsSetUi`, or a frame landing on the run, does
  // not re-render the strip.
  const logs = useStore((s) => s.flows.logs);
  const open = useStore((s) => s.flows.ui.logOpen);
  const setUi = useStore((s) => s.flowsSetUi);
  // `useFlowRunReadouts().fed` as one boolean: the readouts are fed exactly
  // when `flowRunLive` answers true of the sessions the slice knows as the
  // flow's (#449), the question the RUN button asks too.
  const live = useStore((s) => flowRunLive(knownSessions(s.flows), s.sequence));

  const lines = useMemo(() => logTail(logs), [logs]);
  const last = logs.length ? logs[logs.length - 1] : null;

  return (
    <div className="nx-flow-log" data-testid="flow-log">
      {open && (
        <div role="log" className="nx-flow-log-body" data-testid="flow-log-body">
          {lines.map((l) => (
            <div key={l.id} className="nx-flow-log-line">
              <Mono size={10} tone="dim">{logTime(l.ts)}</Mono>
              <Mono size={10.5} tone={LOG_TONE[l.tone]}>{l.msg}</Mono>
            </div>
          ))}
        </div>
      )}

      {/* No aria-label: the visible "LOG" and the last line ARE the accessible
          name, which is what keeps a screen reader hearing what a sighted
          operator reads. `aria-expanded` carries the state. */}
      <button
        type="button"
        className="nx-flow-log-bar"
        aria-expanded={open}
        data-testid="flow-log-toggle"
        onClick={() => setUi({ logOpen: !open })}
      >
        <Label size={10}>LOG</Label>
        <span className="nx-flow-log-last">
          <Mono size={10.5} tone={last ? LOG_TONE[last.tone] : "dim"}>
            {last ? last.msg : emptyLogText(live)}
          </Mono>
        </span>
        <span className="nx-flow-log-chev" aria-hidden="true" data-open={open ? "true" : "false"} />
      </button>
    </div>
  );
}

export default FlowLogStrip;
