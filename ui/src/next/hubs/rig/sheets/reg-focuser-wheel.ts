// reg-focuser-wheel.ts - the two sheets T-RIG-4 owns, offered to the Rig hub's
// registry as one object.
//
// The registry itself (`hubs/rig/sheets.tsx`) is composed by the Rig devices
// task from these per-task `reg-*` files. Splitting it this way is not tidiness:
// six tasks writing one `sheets/index.ts` in parallel is six agents editing the
// same file, which is exactly the conflict the file-ownership partition exists
// to prevent (parallel-agent rule: partition by FILE, not by feature).

import type { SheetComponent } from "../../sheets";
import { FocuserSheet } from "./focuser";
import { WheelSheet } from "./wheel";

export const sheetsFocuserWheel: Record<string, SheetComponent> = {
  focuser: FocuserSheet,
  wheel: WheelSheet,
};
