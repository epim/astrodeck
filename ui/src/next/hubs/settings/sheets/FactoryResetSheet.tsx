// FactoryResetSheet.tsx - Settings > MORE > Factory reset (plan section C.6,
// row FACTORY RESET).
//
// STAGE 2 (wave R7, T-R7-12). Wave 1 mounted
// `components/settings/FactoryResetPanel.tsx` unchanged; `FactoryResetEditor`
// now re-implements its presentation in the design vocabulary and keeps all
// three interlocks exactly as they were - the `admin.users` capability, the
// typed word RESET, and the app's hold-to-confirm dialog - plus the measured
// scope preview and the whole-origin `clearClientState()`. The legacy panel is
// untouched and still serves `#/classic`.
//
// This sheet adds the DANGER ZONE framing above the editor (the design's
// "findable, never brushed against") and nothing else: no second lock note, no
// second confirm.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet, Label, Divider } from "../../../ui";
import { FactoryResetEditor } from "../tuning/system";

export function FactoryResetSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-factoryReset"
      title="FACTORY RESET"
      sub="irreversible - clears settings, keeps captures unless you say otherwise"
      icon={<NxIcon name="stop" />}
      backLabel="SETTINGS"
      onBack={nav.back}
    >
      <Label>DANGER ZONE</Label>
      <Divider />
      <FactoryResetEditor />
    </Sheet>
  );
}
