// SyncSheet.tsx - Settings > MORE > File sync (plan section C.6, row FILE
// SYNC).
//
// Wave 1 mounted `components/settings/SyncPanel.tsx` whole - a `Panel`, four
// native `disabled` attributes and Tailwind chrome inside the new sheet - and
// named the rebuild as a follow-up. Wave R7's T-R7-13 is that follow-up: the
// body is `hubs/settings/tuning/files/SyncEditor.tsx`, in the design's own
// vocabulary, sharing every write path the legacy panel used
// (`setSyncPushConfig`, `getSyncPushStatus`, `pushSyncNow`). The legacy file is
// not edited, not deleted and not imported from anywhere under `next/`; it
// still serves `#/classic`.
//
// The sheet owns the header and its one live line: whether sync is on, and
// where it points. Push itself needs `view.media` (it moves science frames);
// the editor states that separately, per the plan's cap column.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import { SyncEditor } from "../tuning/files";

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
      <SyncEditor />
    </Sheet>
  );
}
