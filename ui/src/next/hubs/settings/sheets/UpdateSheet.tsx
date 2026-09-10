// UpdateSheet.tsx - Settings > ABOUT > Update (plan section C.8).
//
// STAGE 2 (wave R7, T-R7-12), closing the stage-1 decision this file used to
// document. Wave 1 mounted `components/settings/UpdatePanel.tsx` whole - two
// `Panel`s, five native `disabled` attributes and a `.field` number input
// inside a design sheet - and named the restyle as a follow-up. `UpdateEditor`
// is that follow-up: it re-implements the presentation in the design
// vocabulary and keeps the logic that was expensive to learn (the two-half
// apply gate, the `!dirty` clause on the Saved chip, the rig-idle derivation).
// The legacy panel is not edited, not deleted and not imported from anywhere
// under `next/`; it still serves `#/classic`.
//
// The sheet stays mountable for every role: `GET /api/update/status` needs only
// `view.status`, and every mutating control states `system.update` in its own
// words, so this file adds no second lock note on top.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useUpdate } from "../../../../store";
import { UpdateEditor } from "../tuning/system";

export function UpdateSheet(_p: SheetProps): JSX.Element {
  const status = useUpdate();
  // The one live line: the version running now, and the one waiting if any.
  // Both are numbers the body prints too, but the header is what stays on
  // screen while the body scrolls.
  const live = status
    ? status.update_available && status.latest
      ? `v${status.current} - v${status.latest} available`
      : `v${status.current} - up to date`
    : undefined;
  return (
    <Sheet
      data-testid="settings-update"
      title="UPDATE"
      live={live}
      icon={<NxIcon name="refresh" />}
      backLabel="SETTINGS"
      onBack={nav.back}
    >
      <UpdateEditor />
    </Sheet>
  );
}
