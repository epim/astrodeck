// WcsSheet.tsx - Settings > MORE > Plate-solve stamp (plan section C.6, row
// PLATE-SOLVE STAMP).
//
// Wave 1 mounted `components/settings/WcsStampPanel.tsx` whole; wave R7's
// T-R7-13 rebuilds the body as
// `hubs/settings/tuning/files/WcsStampEditor.tsx`. The SAME rebuilt component
// is also mounted inside the Optics sheet's plate-solve card (plan C.5.4) -
// one component, two mount points, neither is a fork. The legacy panel is not
// edited and still serves `#/classic`.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet, Card } from "../../../ui";
import { useConfig } from "../../../../store";
import { wcsStampSummary } from "../../../../lib/wcsStamp";
import { WcsStampEditor, plainDashes } from "../tuning/files";

export function WcsSheet(_p: SheetProps): JSX.Element {
  const config = useConfig();
  const sub = plainDashes(
    wcsStampSummary(config?.solve_saved_lights ?? false, config?.wcs_stamp),
  );

  return (
    <Sheet
      data-testid="settings-wcs"
      title="PLATE-SOLVE STAMP"
      sub={sub}
      icon={<NxIcon name="star" />}
      onBack={nav.back}
    >
      <Card>
        <WcsStampEditor />
      </Card>
    </Sheet>
  );
}
