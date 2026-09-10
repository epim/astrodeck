// UpdateSheet.tsx - Settings > ABOUT > Update (plan section C.8).
//
// UpdatePanel is mounted WHOLE and UNEDITED. It is safe to mount for every
// role: `GET /api/update/status` needs only `view.status` (every role holds
// it), and every mutating control (Check now, Upgrade, the settings form)
// already disables itself on `useCan("system.update")` - this sheet adds no
// second lock note on top (plan C.6's rule for every reused panel: "the
// panels self-gate ... do not double up a lock note").
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useUpdate } from "../../../../store";
import UpdatePanel from "../../../../components/settings/UpdatePanel";

export function UpdateSheet(_p: SheetProps): JSX.Element {
  const status = useUpdate();
  return (
    <Sheet
      data-testid="settings-update"
      title="UPDATE"
      sub={status?.current ? `v${status.current}` : undefined}
      icon={<NxIcon name="refresh" />}
      onBack={nav.back}
    >
      <UpdatePanel />
    </Sheet>
  );
}
