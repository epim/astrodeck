// reg-safety.ts - T-RIG-6's one line into the Rig hub's sheet registry.
//
// The registry itself (`hubs/rig/sheets.tsx`) is composed by the Rig devices
// task, which spreads this object in. Six sheet tasks run in parallel and none
// of them may edit that one file, so each contributes its own `sheetsX` map and
// the composer never has to be re-opened.

import type { SheetComponent } from "../../sheets";
import { SafetySheet } from "./safety";

export const sheetsSafety: Record<string, SheetComponent> = { safety: SafetySheet };
