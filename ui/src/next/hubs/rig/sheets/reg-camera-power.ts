// reg-camera-power.ts - the two sheets T-RIG-2 owns, offered to the Rig hub's
// registry as one object.
//
// The registry itself (`hubs/rig/sheets.tsx`) is composed by the Rig devices
// task from these per-task `reg-*` files. Splitting it this way is not tidiness:
// six tasks writing one `sheets/index.ts` in parallel is six agents editing the
// same file, which is exactly the conflict the file-ownership partition exists
// to prevent (parallel-agent rule: partition by FILE, not by feature).

import type { SheetComponent } from "../../sheets";
import { CameraSheet } from "./camera";
import { PowerSheet } from "./power";

export const sheetsCameraPower: Record<string, SheetComponent> = {
  camera: CameraSheet,
  power: PowerSheet,
};
