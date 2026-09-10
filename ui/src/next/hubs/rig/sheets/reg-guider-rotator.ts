// reg-guider-rotator.ts - T-RIG-5's two sheets, ready for the Rig hub's
// registry to compose (plan hub-rig.md D.1 "No file is edited by two tasks").
//
// The registry itself lives in `sheets.tsx` and belongs to the Rig devices task.
// This file exists so that composition is a one-line spread there rather than
// two imports and two keys, which is what keeps the two tasks off the same
// lines of the same file.

import type { SheetRegistry } from "../../sheets";

export const sheetsGuiderRotator: SheetRegistry = {
  guider: { id: "rig/sheets/guider", load: () => import("./guider").then((m) => ({ default: m.GuiderSheet })) },
  rotator: { id: "rig/sheets/rotator", load: () => import("./rotator").then((m) => ({ default: m.RotatorSheet })) },
};
