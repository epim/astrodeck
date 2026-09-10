// rig/sheets.tsx - the Rig hub's sheet registry (ARCHITECTURE.md section 5).
//
// The device sheets (Camera, Mount -> Polar, Focuser, Filter wheel, Guider,
// Safety, Power, Add device, Driver, Profiles, Inspect) land here in the Rig
// task. One entry exists now: `demo`, the sheet host's own proof of life.
//
// Why `demo` is real code and not test-only scaffolding: `SheetHost` has three
// behaviours that only exist once something is registered - the BACK pill wired
// to `nav.back()`, the params handed through from the query string, and the
// second sheet keeping the first mounted underneath. A registry that stayed
// empty until the first hub task landed would leave all three unexercised, and
// the first person to open a real sheet would be the one finding out.

import type { JSX } from "react";
import type { SheetComponent, SheetProps } from "../sheets";
import { Sheet } from "../../ui";
import { NxIcon } from "../../icons";
import { nav } from "../../router";

function DemoSheet({ params, depth }: SheetProps): JSX.Element {
  const entries = Object.entries(params);
  return (
    <Sheet
      title="SHEET HOST"
      sub={`depth ${depth}`}
      icon={<NxIcon name="rig" size={18} />}
      live={entries.length ? entries.map(([k, v]) => `${k}=${v}`).join(" ") : "no params"}
      // Every sheet wires BACK to the router the same way: the primitive takes
      // no router import, and `SheetProps` carries no callback, so the one line
      // lives here rather than in a wrapper that could forget it.
      onBack={() => nav.back()}
      data-testid="sheet-demo"
    >
      <p className="nx-sheet-sub">
        The device sheets open here. This one exists so the host&apos;s BACK, its params
        and its two-deep stack are exercised before a real sheet depends on them.
      </p>
    </Sheet>
  );
}

export const sheets: Record<string, SheetComponent> = {
  demo: DemoSheet,
};
