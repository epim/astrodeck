// reg-focuser-wheel.ts - the two sheets T-RIG-4 owns, offered to the Rig hub's
// registry as one object.
//
// The registry itself (`hubs/rig/sheets.tsx`) is composed by the Rig devices
// task from these per-task `reg-*` files. Splitting it this way is not tidiness:
// six tasks writing one `sheets/index.ts` in parallel is six agents editing the
// same file, which is exactly the conflict the file-ownership partition exists
// to prevent (parallel-agent rule: partition by FILE, not by feature).

import type { SheetRegistry } from "../../sheets";

export const sheetsFocuserWheel: SheetRegistry = {
  focuser: { id: "rig/sheets/focuser", load: () => import("./focuser").then((m) => ({ default: m.FocuserSheet })) },
  wheel: { id: "rig/sheets/wheel", load: () => import("./wheel").then((m) => ({ default: m.WheelSheet })) },
};
