// RestrictedSheet.tsx - Settings > MORE > Restricted assets (plan section
// C.6, row RESTRICTED ASSETS).
//
// One reused panel, mounted unchanged. `RestrictedAssetsPanel` renders `null`
// entirely when its route 404s or returns zero assets (a server too old to
// have the route, or nothing to disclose) - this sheet still renders its own
// frame either way, so the row the user tapped always opens onto a real
// screen; it just has nothing below the header on that server.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { RestrictedAssetsPanel } from "../../../../components/settings/RestrictedAssetsPanel";

export function RestrictedSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-restricted"
      title="RESTRICTED ASSETS"
      sub="what we are not entitled to hand you, and what this rig does about it"
      icon={<NxIcon name="lock" />}
      onBack={nav.back}
    >
      <RestrictedAssetsPanel />
    </Sheet>
  );
}
