// SyncSheet.tsx - Settings > MORE > File sync (plan section C.6, row FILE
// SYNC).
//
// One reused panel, mounted unchanged: destination + live push-runner status
// + "Push now" (Push itself needs view.media, per the plan's cap column).
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import SyncPanel from "../../../../components/settings/SyncPanel";

export function SyncSheet(_p: SheetProps): JSX.Element {
  const config = useConfig();
  const saved = config?.sync_push;
  const sub = !saved?.enabled
    ? "off - frames stay on the rig"
    : `on · ${saved.path?.trim() ? saved.path : "no destination set"}`;

  return (
    <Sheet
      data-testid="settings-sync"
      title="FILE SYNC"
      sub={sub}
      icon={<NxIcon name="share" />}
      onBack={nav.back}
    >
      <SyncPanel />
    </Sheet>
  );
}
