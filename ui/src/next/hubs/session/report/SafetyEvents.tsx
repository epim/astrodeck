// SafetyEvents.tsx - what the safety monitor did during the run.
//
// The legacy view rendered this panel ONLY when there was at least one event,
// so a clean night and a night whose events never reached the report looked
// identical: nothing on screen. The rebuild always renders the card and says
// which it is. "Nothing tripped the safety monitor during this run" is a claim
// worth making - it is the answer to the question the operator opened the
// report with.

import type { JSX } from "react";
import { Card, EmptyCard, Label, Mono, Pill } from "../../../ui";
import type { SessionReport } from "../../../../types";
import { fmtEventClock, REPORT_COPY } from "./reportModel";

export function SafetyEvents({ events }: { events: SessionReport["safety_events"] }): JSX.Element {
  return (
    <Card data-testid="report-safety">
      <div className="nx-report-block">
        <div className="nx-report-head">
          <Label size={11}>SAFETY EVENTS</Label>
          <Mono size={10} tone="dim" data-testid="report-safety-count">
            {events.length === 0 ? "none" : `${events.length} logged`}
          </Mono>
        </div>
        {events.length === 0 ? (
          <EmptyCard title={REPORT_COPY.safetyNone} data-testid="report-safety-empty" />
        ) : (
          events.map((ev, i) => (
            <div className="nx-report-event" key={i} data-testid={`report-safety-${i}`}>
              <Mono size={10.5} tone="dim">{fmtEventClock(ev.ts)}</Mono>
              <span className="nx-report-event-text">{ev.reason}</span>
              <Pill tone="warn">{ev.action}</Pill>
            </div>
          ))
        )}
      </div>
    </Card>
  );
}
