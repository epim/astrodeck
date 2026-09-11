// SkyPackSheet.tsx - Settings > MORE > Sky atlas offline pack (plan section
// C.6, row SKY ATLAS OFFLINE PACK; GAP-ANALYSIS section 6; wave R7, T-R7-14
// cutover).
//
// One rebuilt editor, in the design's own vocabulary: the offline HiPS pack
// (download, update, delete, live progress) plus the online-fetch (CDS) toggle.
// The DSS2 attribution line is rendered by the editor itself, always, because
// it is a licence condition - not duplicated here.
// `components/settings/SkyAtlasPanel.tsx` is no longer mounted here and is
// untouched for `#/classic`.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import { SkyPackEditor } from "../tuning/calibration";
import { EphemerisCard } from "./EphemerisCard";

export function SkyPackSheet(_p: SheetProps): JSX.Element {
  const config = useConfig();
  const onlineFetch = config?.survey?.online_fetch ?? false;

  return (
    <Sheet
      data-testid="settings-skyPack"
      // SKY DATA, not SKY ATLAS OFFLINE PACK: the sheet now holds two
      // downloads, and the server's own stale-elements sentence sends people
      // here by name ("Refresh them from Sky settings"). A title naming only
      // the survey tiles would leave the second thing unfindable.
      title="SKY DATA"
      sub={`survey tiles and orbital elements · online CDS fetch ${onlineFetch ? "on" : "off"}`}
      icon={<NxIcon name="sky" />}
      onBack={nav.back}
    >
      <SkyPackEditor />
      <EphemerisCard />
    </Sheet>
  );
}
