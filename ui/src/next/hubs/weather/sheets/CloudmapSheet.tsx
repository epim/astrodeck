// CloudmapSheet.tsx - the `cloudmap` sheet.
//
// The body is `components/settings/CloudmapPanel` MOUNTED AS-IS: the enable
// toggle, the satellite select (auto / G18 GOES-West / G19 GOES-East) with the
// mispin warning when the geometry disagrees with a manual pin and its one-click
// "switch to Automatic", the poll interval (5-60 min), the window half-width
// (16-400 cells) and the live "About N MB an hour on this setting" estimate.
//
// The panel exists because the model shipped complete with no way to switch it
// on - "a real screen with no such control on it: a promise nothing kept". Its
// own headline caveat, that the model is ADVISORY and nothing in the sequencer,
// the safety gate or auto-resume reads it, stays exactly where it is.

import type { JSX } from "react";
import CloudmapPanel from "../../../../components/settings/CloudmapPanel";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { Sheet } from "../../../ui";

export function CloudmapSheet(): JSX.Element {
  return (
    <Sheet
      title="CLOUD MAP"
      sub="the GOES cloud model behind the dome - advisory, and it gates nothing"
      icon={<NxIcon name="layers" size={18} />}
      onBack={() => nav.back()}
      data-testid="sheet-cloudmap"
    >
      <CloudmapPanel />
    </Sheet>
  );
}
