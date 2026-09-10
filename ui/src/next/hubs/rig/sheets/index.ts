// rig/sheets/index.ts - the RIG hub's sheet registry (ARCHITECTURE.md section 5).
//
// Twelve sheets, six tasks, ONE file that names them - so the composition is a
// spread per task rather than six agents editing the same twelve lines. Each
// device task contributes a `reg-*.ts` fragment and never opens this file; this
// file imports the fragments and nothing else of theirs.
//
// `demo` is not test scaffolding that outlived its purpose. `SheetHost` has
// three behaviours that only exist once something is registered - BACK wired to
// `nav.back()`, the query params handed through, and the second sheet keeping
// the first mounted underneath - and the shell test exercises all three through
// this one entry. A real device sheet would exercise them too, but it would
// also drag a rig's worth of store state into a test about routing.

import type { JSX } from "react";
import type { SheetComponent, SheetProps } from "../../sheets";
import { Sheet } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { createElement } from "react";

import { sheetsCameraPower } from "./reg-camera-power";
import { sheetsMountPolar } from "./reg-mount-polar";
import { sheetsFocuserWheel } from "./reg-focuser-wheel";
import { sheetsGuiderRotator } from "./reg-guider-rotator";
import { sheetsSafety } from "./reg-safety";
import { InspectSheet } from "./inspect";
import { AddDeviceSheet } from "./addDevice";
import { DriverSheet } from "./driver";
import { ProfilesSheet } from "./profiles";

function DemoSheet({ params, depth }: SheetProps): JSX.Element {
  const entries = Object.entries(params);
  return createElement(
    Sheet,
    {
      title: "SHEET HOST",
      sub: `depth ${depth}`,
      icon: createElement(NxIcon, { name: "rig", size: 18 }),
      live: entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(" ") : "no params",
      // Every sheet wires BACK to the router the same way: the primitive takes
      // no router import, and `SheetProps` carries no callback, so the one line
      // lives here rather than in a wrapper that could forget it.
      onBack: () => nav.back(),
      "data-testid": "sheet-demo",
      children: createElement(
        "p",
        { className: "nx-sheet-sub" },
        "The device sheets open here. This one exists so the host's BACK, its params "
        + "and its two-deep stack are exercised before a real sheet depends on them.",
      ),
    },
  );
}

export const sheets: Record<string, SheetComponent> = {
  ...sheetsCameraPower,
  ...sheetsMountPolar,
  ...sheetsFocuserWheel,
  ...sheetsGuiderRotator,
  ...sheetsSafety,
  inspect: InspectSheet,
  addDevice: AddDeviceSheet,
  driver: DriverSheet,
  profiles: ProfilesSheet,
  demo: DemoSheet,
};
