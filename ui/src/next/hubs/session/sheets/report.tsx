// report.tsx - the end-of-night report, whole and unmodified.
//
// `views/ReportView` self-subscribes and takes no props (`export default
// function ReportView()`), so mounting it inside the new `Sheet` chrome is the
// whole of this file. That is deliberate and is what the plan asks for: the
// report carries the picker's three states, the end-reason chip, the by-filter
// and by-target tables, the safety-events timeline, three trend lines, the
// stacking-bundle panel and frames.csv - and every one of those survives
// BECAUSE nothing here re-implements it.
//
// The report PICKER at its top is redundant inside a sheet opened for one id.
// It stays: removing it would mean editing `ReportView`, which is not this
// task's file, and a second way to reach another night costs nothing.
//
// `params.id` is accepted and NOT forced onto the view. `ReportView` selects
// `lastReportId` or the newest report on its own and takes no id prop; passing
// one would mean forking it. The id is used only to say which night the sheet
// was opened for, so a deep link that lands on a different report is visible
// rather than silent.

import type { JSX } from "react";
import ReportView from "../../../../views/ReportView";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Mono, Sheet } from "../../../ui";
import type { SheetProps } from "../../sheets";

export function ReportSheet({ params }: SheetProps): JSX.Element {
  const id = params.id ?? "";
  return (
    <Sheet
      data-testid="session-report"
      title="NIGHT REPORT"
      sub={id ? `opened for ${id}` : "the newest report on the rig"}
      icon={<NxIcon name="monitor" size={18} />}
      onBack={() => nav.back()}
    >
      {id && (
        <Mono size={10} tone="dim">
          The picker below starts on the newest report; pick {id} if this sheet
          was opened from an older session.
        </Mono>
      )}
      <ReportView />
    </Sheet>
  );
}

export default ReportSheet;
