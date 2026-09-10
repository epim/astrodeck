// CalibrationSheet.tsx - Settings > MORE > Calibration (plan section C.6, row
// CALIBRATION).
//
// Two reused panels, mounted unchanged: the master library (rebuild/delete,
// reads the shared `masters` store slice so this refreshes the live
// pre-flight coverage row too) and the matching/stacking tolerances.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useMasters } from "../../../../store";
import CalibrationLibraryPanel from "../../../../components/settings/CalibrationLibraryPanel";
import CalibrationTolerancesPanel from "../../../../components/settings/CalibrationTolerancesPanel";

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
      <CalibrationLibraryPanel />
      <CalibrationTolerancesPanel />
    </Sheet>
  );
}
