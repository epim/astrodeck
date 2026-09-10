// FactoryResetSheet.tsx - Settings > MORE > Factory reset (plan section C.6,
// row FACTORY RESET).
//
// One reused panel, mounted unchanged. Its own three interlocks stay exactly
// as they are: the admin.users capability, the typed word RESET, and the
// app's hold-to-confirm dialog. This sheet adds a DANGER ZONE label above it
// (the design's "findable, never brushed against" framing) and nothing else -
// no second lock note, no second confirm.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet, Label, Divider } from "../../../ui";
import FactoryResetPanel from "../../../../components/settings/FactoryResetPanel";

export function FactoryResetSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-factoryReset"
      title="FACTORY RESET"
      sub="irreversible - clears settings, keeps captures unless you say otherwise"
      icon={<NxIcon name="stop" />}
      onBack={nav.back}
    >
      <Label>DANGER ZONE</Label>
      <Divider />
      <FactoryResetPanel />
    </Sheet>
  );
}
