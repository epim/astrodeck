// WcsSheet.tsx - Settings > MORE > Plate-solve stamp (plan section C.6, row
// PLATE-SOLVE STAMP).
//
// One reused panel, mounted unchanged. The SAME `WcsStampPanel` component is
// also mounted inline inside the Optics sheet (plan C.5.4) - one component,
// two mount points, neither is a fork.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import { wcsStampSummary } from "../../../../lib/wcsStamp";
import WcsStampPanel from "../../../../components/settings/WcsStampPanel";

export function WcsSheet(_p: SheetProps): JSX.Element {
  const config = useConfig();
  const sub = wcsStampSummary(config?.solve_saved_lights ?? false, config?.wcs_stamp);

  return (
    <Sheet
      data-testid="settings-wcs"
      title="PLATE-SOLVE STAMP"
      sub={sub}
      icon={<NxIcon name="star" />}
      onBack={nav.back}
    >
      <WcsStampPanel />
    </Sheet>
  );
}
