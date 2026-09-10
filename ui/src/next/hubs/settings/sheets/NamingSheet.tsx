// NamingSheet.tsx - Settings > MORE > File naming (plan section C.6, row
// FILE NAMING).
//
// One reused panel, mounted unchanged. `lib/naming.ts` is a byte-for-byte
// mirror of the server's `render_relative_path`; the panel reuses it
// verbatim and this sheet does not touch it either.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import { DEFAULT_TEMPLATE } from "../../../../lib/naming";
import NamingPanel from "../../../../components/settings/NamingPanel";

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
      <NamingPanel />
    </Sheet>
  );
}
