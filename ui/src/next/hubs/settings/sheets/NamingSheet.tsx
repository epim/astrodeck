// NamingSheet.tsx - Settings > MORE > File naming (plan section C.6, row
// FILE NAMING).
//
// Wave 1 mounted `components/settings/NamingPanel.tsx` whole; wave R7's
// T-R7-13 rebuilds the body as `hubs/settings/tuning/files/NamingEditor.tsx`
// in the design's own vocabulary. `lib/naming.ts` is a byte-for-byte mirror of
// the server's `render_relative_path` and is REUSED verbatim by the rebuild -
// a second renderer would be a second answer to where tonight's frames land.
// The legacy panel is not edited and still serves `#/classic`.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import { DEFAULT_TEMPLATE } from "../../../../lib/naming";
import { NamingEditor } from "../tuning/files";

export function NamingSheet(_p: SheetProps): JSX.Element {
  const config = useConfig();
  const saved = config?.naming?.template ?? DEFAULT_TEMPLATE;

  return (
    <Sheet
      data-testid="settings-naming"
      title="FILE NAMING"
      sub={`saved: ${saved}`}
      icon={<NxIcon name="funnel" />}
      onBack={nav.back}
    >
      <NamingEditor />
    </Sheet>
  );
}
