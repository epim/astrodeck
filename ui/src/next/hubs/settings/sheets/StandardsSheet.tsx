// StandardsSheet.tsx - Settings > MORE > Imaging standards (plan section
// C.6, row IMAGING STANDARDS).
//
// One reused panel, mounted unchanged: what counts as a usable frame on this
// rig, and when a night gives up. `StandardsPanel`'s own footer link calls
// `useStore().setView("sequence")` (plan F.5) - `ui/src/next/legacyBridge.ts`
// maps that to `/session/flows/planEditor` (`LEGACY_VIEW_ROUTE.sequence`),
// confirmed landed, so the button already works under the new router with no
// change needed here.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import { standardsOrDefault } from "../../../../lib/standards";
import StandardsPanel from "../../../../components/settings/StandardsPanel";

export function StandardsSheet(_p: SheetProps): JSX.Element {
  const config = useConfig();
  const s = standardsOrDefault(config?.standards);
  const sub =
    `eccentricity ceiling ${s.max_eccentricity} · ` +
    `min stars ${s.min_stars || "off"} · ` +
    `guide RMS ${s.max_guide_rms || "off"}`;

  return (
    <Sheet
      data-testid="settings-standards"
      title="IMAGING STANDARDS"
      sub={sub}
      icon={<NxIcon name="gauge" />}
      onBack={nav.back}
    >
      <StandardsPanel />
    </Sheet>
  );
}
