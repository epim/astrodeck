// planEditor.tsx - the plan editor, whole, at tablet and desktop
// (plan section D.4; GAP-ANALYSIS section 7).
//
// This is the doorway for everything Flows cannot express and the phone does not
// show: the targets editor with `CatalogSearch` and "Order by tonight",
// `PlanLibraryPanel` (plan identity, save / save-as, import / export, the saved
// plans list), `SchedulePanel` per target, the Automation panel (guide, dither,
// refocus, filter offsets, meridian flip and its warning lead, the safety gate,
// recover guiding, cool-to, the HFR spike guard, park and warm when done, the
// count mode and the four numeric quality guards), `InstructionsPanel`'s
// when/then rules, `SessionsPanel`, `PreflightStrip` and Run.
//
// `views/SequenceView` self-subscribes and takes no props (`export default
// function SequenceView()`), so mounting it is the whole of this file. That is
// deliberate: every one of the features above survives BECAUSE nothing here
// re-implements one. Its two-column grid is layout-bound
// (`inventory-session-monitor.md` section 9), but a 420 px sheet is narrower
// than the `lg` breakpoint that grid switches on, so it lays out as the single
// column it already degrades to. It is a tuning editor, and ARCHITECTURE.md
// section 11 permits its `Panel` chrome there.
//
// THE RUN LOCKS COME ALONG UNCHANGED. `SequenceView` carries three notes about
// what a live run does and does not read (`:1052-1057`, `:1496-1499`,
// `:1758-1759`): the server is executing the copy of the plan it took at Run, so
// edits here reach the NEXT run. Those sentences are the reason this sheet is
// safe to open mid-night, and they are not restated here - one statement of a
// fact is a fact, two are a maintenance problem.
//
// PHONE. Below 768 px the sheet renders the reason and nothing else. This is not
// squeamishness about width: the editor is a dense two-column form with a
// catalog search, per-target schedules and eight numeric guards, and squeezing
// it onto a 390 px screen at 2 a.m. is how a quota gets set to the wrong number
// on the wrong plan. The reason names where it does open.

import type { JSX } from "react";

import SequenceView from "../../../../views/SequenceView";
import { nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { NxIcon } from "../../../icons";
import { EmptyCard, Sheet } from "../../../ui";
import type { SheetProps } from "../../sheets";

/** The one sentence every locked door to this editor uses - the Now screen's
 *  quota rows, the count-mode warning's fix button and the Flows screen's own
 *  chip - so a phone user meets the same words wherever they press. */
export const PLAN_EDITOR_PHONE_REASON = "The plan editor opens on a tablet or desktop.";

export function PlanEditorSheet(_props: SheetProps): JSX.Element {
  const phone = useBreakpoint() === "phone";

  return (
    <Sheet
      data-testid="session-plan-editor"
      title="PLAN EDITOR"
      sub={phone ? "tablet or desktop" : "targets, schedule, automation, quotas, rules"}
      icon={<NxIcon name="session" size={18} />}
      onBack={() => nav.back()}
    >
      {phone ? (
        <EmptyCard
          data-testid="plan-editor-phone"
          title="NOT ON A PHONE"
          hint={`${PLAN_EDITOR_PHONE_REASON} Everything the run is executing is still`
            + " readable on Session - Now; RUN and RESUME work here."}
        />
      ) : (
        <SequenceView />
      )}
    </Sheet>
  );
}

export default PlanEditorSheet;
