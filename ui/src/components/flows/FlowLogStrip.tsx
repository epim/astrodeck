// FlowLogStrip.tsx — the canvas's bottom log rail. Contract §C.12, refs 02/07.
//
// WHAT IT IS ALLOWED TO SHOW, AND WHY THAT MATTERS MORE THAN THE GEOMETRY.
// This strip renders `flows.logs` and nothing else. The prototype's run
// choreography — the per-frame lines, the thirteen ordered cloud-dodge lines
// fireClouds() emits — is a STORYBOARD FOR THE SERVER'S WORDING (§C.13), not
// something the client may synthesise. There is no `flow.log` WS event today
// (§E.6, §G-1: `git grep bus.publish` finds eleven topics and no `flow`), so
// during a real run this strip shows whatever `flowsAppendLog` was actually
// given and, if nothing arrived, keeps saying so. A scripted animation here
// would be a sentence about a rig that never spoke.
//
// WHAT THE EMPTY RING SAYS IS ABOUT THE LOG, NEVER THE RUN (#529). It said
// "Idle — no events yet" through every live run, under a STATE readout
// reading RUNNING: the "no events yet" half was true and "Idle" was not, and
// nothing on the page said which of the two was wrong. So the run's state is
// left to the STATE readout, and the empty ring says what is true of the log:
// during a live run of this flow (the readouts' own rule, so the two can
// never disagree about whether it is live) that the server publishes no log
// lines for a flow run, and otherwise that there are no events yet. This
// strip and the classic phone MONITOR print `emptyLogText`; the #/next canvas
// strip and phone stage sheet print canvas/FlowLogStrip's restatement of it
// (r7Parity keeps the #/next tree off this presentation module), and
// phoneReadouts.test.tsx case 8 holds all four to these words.
//
// The ring lives in the store (LOG_RING = 120). This shows the newest 60.
import { useMemo } from "react";
import { useStore } from "../../store";
import { flowRunLive, knownSessions } from "./flowRunState";
import type { FlowLogLine, FlowLogTone } from "./flowsTypes";

/** How many of the store's 120-entry ring the expanded panel shows. §C.12. */
export const LOG_TAIL = 60;

/** The empty ring's words while no run of this flow is live: the flow has
 *  not run, has finished, or the rig is running another flow's. No "Idle":
 *  whether a run is idle is the STATE readout's to say, not the log's. */
export const EMPTY_LOG_TEXT = "no events yet";

/** The empty ring's words while this flow's run is live (#529).
 *
 *  It is the literal truth and it must stay the literal truth: no server
 *  topic carries a flow's log lines (there is no `flow.log` event), so the
 *  ring stays empty through a whole run unless this page itself appended a
 *  line, and an empty log beside a live run must say why it is empty, or it
 *  reads as a run that has gone quiet. Once a `flow.log` topic exists this
 *  sentence is false and goes. No em-dash: the #/next surfaces print the same
 *  words, and their copy rule is hyphens. */
export const RUN_EMPTY_LOG_TEXT = "no log lines: the server publishes none for a flow run";

/** What an empty log says. `runLive` is whether this flow's run is live,
 *  as the run readouts decide it (`useFlowRunReadouts().fed`, or
 *  `useFlowRunFed` for a surface that draws no readouts). */
export function emptyLogText(runLive: boolean): string {
  return runLive ? RUN_EMPTY_LOG_TEXT : EMPTY_LOG_TEXT;
}

/** `useFlowRunReadouts().fed`, for a log that draws none of the readouts.
 *
 *  The readouts are fed exactly when `flowRunLive` answers true of the
 *  sessions the slice knows as the flow's (runCopy `runReadouts`, #449), and
 *  this asks the same reader the same question, as the RUN button's `ours`
 *  does, so the log can never call a run live that STATE calls idle, or the
 *  other way round. Read as one boolean, exact under Object.is: a frame
 *  landing on the run moves the readouts' numbers and wakes no log. */
export function useFlowRunFed(): boolean {
  return useStore((s) => flowRunLive(knownSessions(s.flows), s.sequence));
}

/** Tone → text colour. NOT the toast ladder: §C.12 is explicit that the log's
 *  default rung is `--text-dim` where a toast's is `--accent`. good/warn/bad
 *  agree. Colour is redundant here rather than load-bearing — the line itself
 *  is a sentence, so a reader who cannot see the hue still gets the meaning. */
export const LOG_TONE_CLASS: Record<FlowLogTone, string> = {
  info: "text-dim",
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
};

/** The newest `LOG_TAIL` lines, NEWEST FIRST — `logs.slice(-60).reverse()`.
 *
 *  Newest-first is the whole reason the panel is usable without scrolling: the
 *  line you want mid-run is the one that just landed, and a chronological panel
 *  puts it at the bottom of a 170px box that is already scrolled to the top. */
export function logTail(logs: FlowLogLine[]): FlowLogLine[] {
  return logs.slice(-LOG_TAIL).reverse();
}

/** `HH:MM:SS`. en-GB with hour12 false is what gives the 24-hour, zero-padded
 *  form regardless of the viewer's locale — a rig log that reads "2:04:11 pm"
 *  on one machine and "14:04:11" on another cannot be compared to a night log. */
export function logTime(ts: number): string {
  return new Date(ts).toLocaleTimeString("en-GB", { hour12: false });
}

/** The 30px bar, and the 170px scrollback above it when open.
 *
 *  ⚠ 30px is the design's number (§C.12 / README §3) and it is BELOW the house
 *  44px touch floor. §G-20 is the open question — it lists bumping on coarse
 *  pointers and buying the hit area InfoDot-style as candidate answers, and
 *  picking one here would close it. The design's height is what renders. */
export default function FlowLogStrip() {
  // Four exact selectors. `logs` changes identity only when a line is
  // appended; `logOpen` and `live` are booleans, so the `ui` object being
  // replaced by an unrelated flowsSetUi (say, opening TONIGHT), or a frame
  // landing on the run, does not re-render the strip.
  const logs = useStore((s) => s.flows.logs);
  const open = useStore((s) => s.flows.ui.logOpen);
  const setUi = useStore((s) => s.flowsSetUi);
  const live = useFlowRunFed();

  const lines = useMemo(() => logTail(logs), [logs]);
  const last = logs.length ? logs[logs.length - 1] : null;

  return (
    <div className="absolute inset-x-0 bottom-0 z-[6] flex flex-col">
      {open && (
        <div
          role="log"
          className="max-h-[170px] overflow-y-auto px-3 py-2 flex flex-col gap-[3px]
            border-t border-line bg-[color-mix(in_srgb,var(--bg)_92%,transparent)]"
        >
          {lines.map((l) => (
            <div key={l.id} className={`font-mono text-[10.5px] ${LOG_TONE_CLASS[l.tone]}`}>
              <span className="text-faint">{logTime(l.ts)}</span>{"  "}{l.msg}
            </div>
          ))}
        </div>
      )}

      {/* No aria-label: the visible "LOG" and the last line ARE the accessible
          name, which is what keeps WCAG 2.5.3 satisfied (the same trap §F.4
          flags for the ADD NODE relabelling). aria-expanded carries the state. */}
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setUi({ logOpen: !open })}
        className="w-full h-[30px] px-3 flex items-center gap-2 text-left
          border-t border-line bg-[color-mix(in_srgb,var(--bg)_85%,transparent)]
          text-dim hover:text-ink transition-colors cursor-pointer"
      >
        <span className="flex-none font-display font-semibold text-[9px] tracking-[0.2em] text-faint">
          LOG
        </span>
        <span
          className={`flex-1 min-w-0 truncate font-mono text-[10.5px] ${
            last ? LOG_TONE_CLASS[last.tone] : "text-faint"
          }`}
        >
          {last ? last.msg : emptyLogText(live)}
        </span>
        <span aria-hidden className="flex-none font-mono text-[10px] text-faint">
          {open ? "▾" : "▴"}
        </span>
      </button>
    </div>
  );
}
