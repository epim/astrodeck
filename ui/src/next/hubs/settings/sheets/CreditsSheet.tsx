// CreditsSheet.tsx - Settings > ABOUT > Credits and licences (plan section
// C.8).
//
// STAGE 2 (wave R7, T-R7-12). `CreditsBrowser` replaces the mounted
// `components/settings/CreditsPanel.tsx`: the groups are the design's
// `Disclosure`, the obligation codes are `Pill`s and the search box is the
// design's field. What did not change is the two things that make this screen
// work - the ~550 KB of licence text still arrives by dynamic `import()` so it
// gets its own chunk, fetched once and cached, and it is still served from this
// rig's own origin, so a rig with no internet loses nothing.
//
// Deliberately UNGATED: "a licence notice that only an admin can read is not
// published" (inventory-settings-weather.md section 14.1). The editor says so
// on screen rather than leaving a reader to wonder whether something is hidden.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { CreditsBrowser } from "../tuning/system";

export function CreditsSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-credits"
      title="CREDITS AND LICENCES"
      sub="every component this build ships, and what its licence asks of us"
      icon={<NxIcon name="layers" />}
      backLabel="SETTINGS"
      onBack={nav.back}
    >
      <CreditsBrowser />
    </Sheet>
  );
}
