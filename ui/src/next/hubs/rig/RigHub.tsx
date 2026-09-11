// RigHub.tsx - the RIG hub root: DEVICES and CAPTURE.
//
// The chips themselves are the shell's (`shell/SubNav.tsx` renders them from
// `HUB_META`), so this file's whole job is to mount ONE of the two screens for
// the section the hash names. Both are real screens with their own state, and
// only the named one is mounted: keeping the other alive would leave a capture
// bench subscribed to the preview stream while the user is reading a driver
// list, which is a phone's battery and a rig's bandwidth spent on a screen
// nobody is looking at.
//
// DEVICES is the default (`SUBS.rig[0]`), which is the right first answer for a
// hub whose first night starts with "connect something".

import type { JSX } from "react";
import { useRoute } from "../../router";
import { DevicesScreen } from "./devices/DevicesScreen";
import { CaptureScreen } from "./capture";

export function RigHub(): JSX.Element {
  const { sub } = useRoute();
  return (
    <div data-testid="hub-rig">
      {sub === "capture" ? <CaptureScreen /> : <DevicesScreen />}
    </div>
  );
}
