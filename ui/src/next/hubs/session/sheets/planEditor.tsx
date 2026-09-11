// planEditor.tsx - the plan editor, whole, at tablet and desktop
// (plan section D.4; wave R7 task T-R7-5).
//
// This is the doorway for everything Flows cannot express and the phone does not
// show: the targets editor with `CatalogSearch` and "Order by tonight", the plan
// identity and library (save / save-as / import / export / the saved list), the
// per-target schedule, the automation column, the when/then rules, the past
// sessions, the pre-flight checks and START.
//
// IT NO LONGER MOUNTS `views/SequenceView`. Wave R7 rebuilds it in the design's
// own vocabulary under `hubs/session/plan/**`, sharing every LOGIC module the
// legacy view used (`lib/preflight`, `lib/photometry`, `lib/planLibrary`,
// `lib/planFile`, `lib/planGroups`, `lib/sequenceTemplates`, `lib/exposure`,
// `lib/filterSettings`, `lib/visibility`, `components/sequence/stepDefaults`)
// and re-implementing only presentation. The legacy file is untouched and
// `#/classic` still mounts it.
//
// THE RUN LOCKS COME ALONG. `SequenceView` carries three notes about what a
// live run does and does not read (`:1052-1057`, `:1496-1499`, `:1758-1759`):
// the server is executing the copy of the plan it took at Run, so edits here
// reach the NEXT run. They are rendered by `PlanEditorBody`, around the
// sections they govern, and every clause of the first one is enforced by the
// controls under it.
//
// THE SCHEDULE LAYER IS A SECOND WHOLE SHEET, not a sheet inside a sheet: it
// gets its own header, its own BACK, and none of this sheet's START footer,
// which would be a control for a screen that cannot start anything. It is
// addressed by a route param (`?schedule=<target id>`) so a reload lands back
// on it and the browser's Back closes it.
//
// PHONE. Below 768 px the sheet renders the reason and nothing else. This is not
// squeamishness about width: the editor is a dense form with a catalog search,
// per-target schedules and eight numeric guards, and squeezing it onto a 390 px
// screen at 2 a.m. is how a quota gets set to the wrong number on the wrong
// plan. The reason names where it does open.

import type { JSX } from "react";

import { nav } from "../../../router";
import { useBreakpoint } from "../../../breakpoint";
import { NxIcon } from "../../../icons";
import { EmptyCard, Sheet } from "../../../ui";
import type { SheetProps } from "../../sheets";
import {
  closePlanSchedule, PlanEditorBody, PlanEditorFooter, PlanScheduleSheet, usePlanSchedule,
} from "../plan";

/** The one sentence every locked door to this editor uses - the Now screen's
 *  quota rows, the count-mode warning's fix button and the Flows screen's own
 *  chip - so a phone user meets the same words wherever they press. */
export const PLAN_EDITOR_PHONE_REASON = "The plan editor opens on a tablet or desktop.";

export function PlanEditorSheet({ params }: SheetProps): JSX.Element {
  const phone = useBreakpoint() === "phone";
  // Called before any branch: the layer's props are a hook, and a hook cannot
  // hide behind a breakpoint.
  const schedule = usePlanSchedule(params);

  if (!phone && schedule) {
    return (
      <PlanScheduleSheet
        name={schedule.name}
        schedule={schedule.schedule}
        lockedReason={schedule.lockedReason}
        onChange={schedule.onChange}
        onBack={closePlanSchedule}
      />
    );
  }

  return (
    <Sheet
      data-testid="session-plan-editor"
      title="PLAN EDITOR"
      sub={phone ? "tablet or desktop" : "targets, schedule, automation, quotas, rules"}
      icon={<NxIcon name="session" size={18} />}
      onBack={() => nav.back()}
      footer={phone ? undefined : <PlanEditorFooter />}
    >
      {phone ? (
        <EmptyCard
          data-testid="plan-editor-phone"
          title="NOT ON A PHONE"
          hint={`${PLAN_EDITOR_PHONE_REASON} Everything the run is executing is still`
            + " readable on Session - Now; RUN and RESUME work here."}
        />
      ) : (
        <PlanEditorBody />
      )}
    </Sheet>
  );
}

export default PlanEditorSheet;
