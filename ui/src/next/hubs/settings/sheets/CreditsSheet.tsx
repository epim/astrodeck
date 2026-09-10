// CreditsSheet.tsx - Settings > ABOUT > Credits and licences (plan section
// C.8).
//
// CreditsPanel is mounted WHOLE and UNEDITED, and deliberately UNGATED - "a
// licence notice that only an admin can read is not published"
// (inventory-settings-weather.md section 14.1). It reads
// `credits.generated.json` via a dynamic import so the ~550 KB of licence
// text gets its own chunk, fetched once and cached, offline-safe.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import CreditsPanel from "../../../../components/settings/CreditsPanel";

export function CreditsSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-credits"
      title="CREDITS AND LICENCES"
      icon={<NxIcon name="layers" />}
      onBack={nav.back}
    >
      <CreditsPanel />
    </Sheet>
  );
}
