// canvas/sheets.ts - the sheets this area registers.
//
// One name, `flowStages`: the phone's stage list, monitor and tap-to-wire
// footer. It is a NAMED export rather than an edit to the session hub's registry
// because that file is composed from several tasks' halves - a shared registry
// edited by two tasks is how a merge silently drops a sheet, with the file
// resolving, the app building, and one name simply not in the map any more.
//
// Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a collision),
// which is why this is a plain word rather than a hub-prefixed one.

import type { SheetComponent } from "../../../sheets";
import { FlowStagesPhoneSheet, FLOW_STAGES_SHEET } from "./FlowStagesPhoneSheet";

export const flowCanvasSheets: Record<string, SheetComponent> = {
  [FLOW_STAGES_SHEET]: FlowStagesPhoneSheet,
};
