// CalibrationSheet.tsx - Settings > MORE > Calibration (plan section C.6, row
// CALIBRATION; wave R7, T-R7-14 cutover).
//
// Two rebuilt editors, in the design's own vocabulary: the master library
// (rebuild/delete, reads the shared `masters` store slice so this refreshes the
// live pre-flight coverage row too) and the matching/stacking tolerances. Both
// live in `hubs/settings/tuning/calibration/`; `components/settings/
// CalibrationLibraryPanel.tsx` and `CalibrationTolerancesPanel.tsx` are no
// longer mounted here and are untouched for `#/classic`.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useMasters } from "../../../../store";
import { CalibrationLibraryEditor, CalibrationTolerancesEditor } from "../tuning/calibration";

export function CalibrationSheet(_p: SheetProps): JSX.Element {
  const masters = useMasters();
  const n = masters.length;
  const sub = `${n} master${n === 1 ? "" : "s"} in the library`;

  return (
    <Sheet
      data-testid="settings-calibration"
      title="CALIBRATION"
      sub={sub}
      icon={<NxIcon name="layers" />}
      onBack={nav.back}
    >
      <CalibrationLibraryEditor />
      <CalibrationTolerancesEditor />
    </Sheet>
  );
}
