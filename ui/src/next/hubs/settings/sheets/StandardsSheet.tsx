// StandardsSheet.tsx - Settings > MORE > Imaging standards (plan section
// C.6, row IMAGING STANDARDS).
//
// Wave 1 mounted `components/settings/StandardsPanel.tsx` whole; wave R7's
// T-R7-13 rebuilds the body as
// `hubs/settings/tuning/files/StandardsEditor.tsx`. The legacy panel is not
// edited and still serves `#/classic`.
//
// THE FOOTER LINK CHANGED, DELIBERATELY. The legacy panel's "plan editor" link
// calls `useStore().setView("sequence")`, which `next/legacyBridge.ts:54` maps
// to `/session/flows/planEditor`; `ARCHITECTURE.md` section 9 forbids the new
// UI from writing `store.view` at all, so the rebuilt link calls
// `nav.sheet("planEditor")` directly. The bridge mapping STAYS - `#/classic`
// and the legacy panel still need it.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import { standardsOrDefault } from "../../../../lib/standards";
import { StandardsEditor } from "../tuning/files";

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
      <StandardsEditor />
    </Sheet>
  );
}
