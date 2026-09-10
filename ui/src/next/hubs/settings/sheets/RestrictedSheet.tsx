// RestrictedSheet.tsx - Settings > MORE > Restricted assets (plan section
// C.6, row RESTRICTED ASSETS).
//
// STAGE 2 (wave R7, T-R7-12). `RestrictedList` replaces the mounted
// `components/settings/RestrictedAssetsPanel.tsx`. Two behaviours are carried
// across unchanged: the licensor's quote and our reading of it stay separate
// blocks (a README once turned an interpretation into a "fact" and six binaries
// shipped on it), and the acknowledgment is INSTANCE-WIDE, so it is gated on
// `config.backend` - it is a statement about the deployment, not about whoever
// is signed in.
//
// One thing did change: where the legacy panel returned `null` on a server with
// no restricted assets (or one too old to have the route), leaving this sheet's
// header sitting over blank space, the rebuilt list renders an honest empty
// state instead. A route that renders nothing is a dead end.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { RestrictedList } from "../tuning/system";

export function RestrictedSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-restricted"
      title="RESTRICTED ASSETS"
      sub="what we are not entitled to hand you, and what this rig does about it"
      icon={<NxIcon name="lock" />}
      backLabel="SETTINGS"
      onBack={nav.back}
    >
      <RestrictedList />
    </Sheet>
  );
}
