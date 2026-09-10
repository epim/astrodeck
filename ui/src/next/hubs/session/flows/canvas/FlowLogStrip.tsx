// FlowLogStrip.tsx - the canvas's bottom log rail (wave R7 parity row A10).
//
// WHAT IT IS ALLOWED TO SHOW, AND WHY THAT MATTERS MORE THAN THE GEOMETRY. This
// strip renders `flows.logs` and nothing else. There is no `flow.log` WS event
// today, so during a real run it shows whatever `flowsAppendLog` was actually
// given and, if nothing arrived, keeps saying so. A scripted run narration here
// would be a sentence about a rig that never spoke.
//
// The ring lives in the store (120 lines). This shows the newest 60, newest
// first - the line you want mid-run is the one that just landed, and a
// chronological panel puts it at the bottom of a box already scrolled to the
// top.
//
// The collapsed bar is 44 px, not the legacy 30: it is a control you press in
// the dark at the scope, and 30 px was already flagged as under the house floor.

import { useMemo, type JSX } from "react";

import { useStore } from "../../../../../store";
import { Label, Mono } from "../../../../ui";
import { IDLE_LOG_TEXT, LOG_TONE, logTail, logTime } from "./canvasModel";

export function FlowLogStrip(): JSX.Element {
  // Three exact selectors. `logs` changes identity only when a line is
  // appended; `logOpen` is a boolean, so the `ui` object being replaced by an
  // unrelated `flowsSetUi` does not re-render the strip.
  const logs = useStore((s) => s.flows.logs);
  const open = useStore((s) => s.flows.ui.logOpen);
  const setUi = useStore((s) => s.flowsSetUi);

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
            {last ? last.msg : IDLE_LOG_TEXT}
          </Mono>
        </span>
        <span className="nx-flow-log-chev" aria-hidden="true" data-open={open ? "true" : "false"} />
      </button>
    </div>
  );
}

export default FlowLogStrip;
