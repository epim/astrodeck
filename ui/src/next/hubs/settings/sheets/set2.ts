// set2.ts - T-SET-2's two sheets, handed to the SETTINGS hub as one object.
//
// The hub composes the registry (`hubs/settings/sheets.ts`); this file is the
// seam between that composition and this task's directory, so the two can land
// independently without either editing the other's file. Names are GLOBAL
// across the app (ARCHITECTURE.md section 5), which is why they are spelled
// here once rather than at each `nav.sheet(...)` call site.

import type { SheetComponent } from "../../sheets";
import { ConnectionSheet } from "./ConnectionSheet";
import { OpticsSheet } from "./OpticsSheet";

export const sheets2: Record<string, SheetComponent> = {
  connection: ConnectionSheet,
  optics: OpticsSheet,
};

export default sheets2;
