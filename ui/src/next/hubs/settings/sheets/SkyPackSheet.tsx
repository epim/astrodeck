// SkyPackSheet.tsx - Settings > MORE > Sky atlas offline pack (plan section
// C.6, row SKY ATLAS OFFLINE PACK; GAP-ANALYSIS section 6).
//
// One reused panel, mounted unchanged: the offline HiPS pack (download,
// update, delete) plus the online-fetch (CDS) toggle. The panel's own
// attribution line ("DSS2 imagery (c) AAO/STScI, served from CDS/ESA HiPS
// mirrors.") is always shown by the panel itself - not duplicated here.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import SkyAtlasPanel from "../../../../components/settings/SkyAtlasPanel";

export function SkyPackSheet(_p: SheetProps): JSX.Element {
  const config = useConfig();
  const onlineFetch = config?.survey?.online_fetch ?? false;

  return (
    <Sheet
      data-testid="settings-skyPack"
      title="SKY ATLAS OFFLINE PACK"
      sub={`online CDS fetch ${onlineFetch ? "on" : "off"}`}
      icon={<NxIcon name="sky" />}
      onBack={nav.back}
    >
      <SkyAtlasPanel />
    </Sheet>
  );
}
