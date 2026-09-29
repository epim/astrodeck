// RunSection.tsx - the Target modal's RUN section (#189 S4 item 1; spec
// 2026-09-23 flows mosaic, 2.4 RUN, 1.4, 1.6; Revision 2 rulings 1 and 2; S4
// orchestrator ruling 8).
//
// Shown only when the block owns a stage (the sheet decides): a block with no
// stages has nothing to rotate, stay on or count.
//
// A ONE-PANEL BLOCK GETS NO VISIT ROWS (#413). It compiles to no group
// (`compile_plan` emits `mosaic: null`), so nothing reads `passes` or
// `minVisit` and there is no other panel to rotate to: the loop, PASSES PER
// VISIT and AT LEAST rows are left out rather than offered as controls that
// change nothing. They come back as soon as the draft is a grid. The flow's
// whenWaiting row stays, since it is the flow's, with a line saying it reads
// nothing while the flow has no mosaic, as the flow overview says.
//
//   - "Rotate panels every pass" is the loop wire, one source of truth
//     (spec 1.4): the toggle is held by the sheet and DONE hands it to
//     `flowsApplyFraming`, which places or lifts the wire from the lane's
//     tail in the same write as the params. It locks with a reason where
//     the lane cannot loop.
//   - Stay on a panel: `passes` (1 to 20) and "at least N minutes" (0 to 180).
//   - While a mosaic waits: the FLOW'S `whenWaiting` setting (ruling 1),
//     labelled "for every mosaic in this flow" because it is not this
//     block's; DONE writes it through `flowsSetSetting`.
//   - Counts: no control (ruling 2). A block that still counts every sub
//     shows the persistent counts line instead (S4 orchestrator ruling 8).
//   - The readouts: every number is the server compile's (`readouts`),
//     spelled by framingModel `runLines`; nothing here computes one. While
//     the compile for the block as framed has not answered, the section
//     says so rather than show the numbers for the layout it had before.
//   - The campaign line, for a holder of the site view only (6.9).

import type { JSX } from "react";
import { HonestButton } from "../../../ui";
import { FLOW_SETTINGS } from "../../flowsTypes";
import { Stepper } from "./GridSection";

export const ROTATE_LABEL = "Rotate panels every pass";
export const WHEN_WAITING_LABEL = "While a mosaic waits (for every mosaic in this flow)";
/** Under the whenWaiting row while no TARGET in the flow, this block's draft
 *  included, has more than one panel. The classic overview's
 *  `WHEN_WAITING_NO_MOSAIC`, copied rather than imported (the overview's
 *  chunk may not reach a framing module, nor this one the inspector's) and
 *  held equal by framingSections.test.tsx. */
export const WHEN_WAITING_NO_MOSAIC =
  "This flow has no mosaic yet, so this changes nothing until a TARGET has more than one panel.";

export interface RunSectionProps {
  /** The draft is one panel: no loop, passes or minimum-visit row. */
  onePanel: boolean;
  /** No TARGET in the flow is a mosaic, this block's draft included. */
  noMosaic: boolean;
  loop: boolean;
  /** Why the loop toggle cannot act, or null. */
  loopLock: string | null;
  passes: number;
  /** Minutes. */
  minVisit: number;
  whenWaiting: string;
  /** The RUN lines (framingModel `runLines`), or null while none are in hand. */
  lines: string[] | null;
  /** Why there are no lines, when there are none. */
  linesNote: string | null;
  countsNotice: string | null;
  campaign: string | null;
  onLoop: (on: boolean) => void;
  onPasses: (n: number) => void;
  onMinVisit: (min: number) => void;
  onWhenWaiting: (v: string) => void;
  explain: (reason: string) => void;
}

export function RunSection(p: RunSectionProps): JSX.Element {
  return (
    <section className="tfs-section" aria-labelledby="tfs-run-h" data-testid="framing-run">
      <h3 id="tfs-run-h" className="tfs-section-h">RUN</h3>
      {!p.onePanel && (
        <>
          <div className="tfs-row">
            <span className="tfs-label">{ROTATE_LABEL}</span>
            <HonestButton className={`tfs-btn ${p.loop ? "tfs-on" : ""}`} reason={p.loopLock}
              onClick={() => p.onLoop(!p.loop)} onExplain={p.explain}>
              <span data-testid="framing-loop" data-on={p.loop ? "true" : "false"}>{p.loop ? "ON" : "OFF"}</span>
            </HonestButton>
          </div>
          <Stepper label="PASSES PER VISIT" value={p.passes} min={1} max={20} lock={null}
            onChange={p.onPasses} explain={p.explain} testId="framing-passes" />
          <Stepper label="AT LEAST" value={p.minVisit} min={0} max={180} step={5} unit=" min" lock={null}
            onChange={p.onMinVisit} explain={p.explain} testId="framing-min-visit" />
        </>
      )}
      <div className="tfs-row">
        <label className="tfs-label" htmlFor="tfs-when-waiting">{WHEN_WAITING_LABEL}</label>
        <select id="tfs-when-waiting" className="field tfs-input" value={p.whenWaiting}
          onChange={(e) => p.onWhenWaiting(e.target.value)}>
          {FLOW_SETTINGS.whenWaiting.options.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
      {p.noMosaic && <div className="tfs-row tfs-note" data-testid="framing-no-mosaic">{WHEN_WAITING_NO_MOSAIC}</div>}
      {p.countsNotice && (
        <div className="tfs-row tfs-banner tfs-warn" role="status" data-testid="framing-counts">{p.countsNotice}</div>
      )}
      <div className="tfs-readouts" data-testid="framing-readouts">
        {p.lines
          ? p.lines.map((l) => <div key={l} className="tfs-row tfs-mono tfs-readout">{l}</div>)
          : p.linesNote && <div className="tfs-row tfs-note">{p.linesNote}</div>}
      </div>
      {p.campaign && <div className="tfs-row tfs-note" data-testid="framing-campaign">{p.campaign}</div>}
    </section>
  );
}

export default RunSection;
