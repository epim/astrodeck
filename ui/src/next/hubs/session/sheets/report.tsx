// report.tsx - the NIGHT REPORT sheet.
//
// WHAT CHANGED IN R7. This file used to mount `views/ReportView` whole, and
// said so: the report survived because nothing here re-implemented it. Wave R7
// closes that (decision D-SES-3): `hubs/session/report/**` is the report in the
// new UI's own vocabulary, sharing the LOGIC (`lib/reportChart.ts`,
// `lib/bundleView.ts`, `lib/eta.ts`, `api/reports.ts`) and re-implementing only
// presentation. `views/ReportView.tsx` is untouched and still renders at
// `#/classic/report`.
//
// `params.id` is now HONOURED rather than announced. The old file passed no id
// to `ReportView` (which selects `lastReportId` or the newest report on its
// own) and printed a line asking the user to pick the right night out of the
// picker. `ReportScreen` takes the id, so a deep link opens the report it
// names; the picker is still there for reading another night.

import type { JSX } from "react";
import { ReportScreen } from "../report";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
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
      <ReportScreen reportId={id || undefined} />
    </Sheet>
  );
}

export default ReportSheet;
