// sky/sheets/index.ts - the SKY hub's sheet registry (ARCHITECTURE.md section 5).
//
// Seven names, four owners, ONE component each. `sites` and `horizon` are shared
// with the Settings hub and are registered from both maps as the SAME function
// object - which is the contract working, not a collision. `hubs/index.ts`
// checks that at module load precisely because the other case (two DIFFERENT
// components under one name) makes the same URL open different screens depending
// on which module loaded last, and by the time a user notices it looks like a
// routing bug rather than a registry one.
//
// This file replaced a `sky/sheets.ts` placeholder. A `.ts` file and a directory
// of the same name are both resolvable for `import "./sky/sheets"`, and the FILE
// wins - so leaving it behind would have shadowed every sheet here with an empty
// map and left the hub's own hash pointing at "THIS SHEET IS NOT BUILT YET".

import type { SheetComponent } from "../../sheets";

import { TargetsSheet } from "./targets";
import { BriefSheet } from "./brief";
import { QuickSessionSheet } from "./quick";
import { FlowCardSheet } from "./flow";
import { SitesSheet } from "./sites";
import { HorizonSheet } from "./horizon";
import { CoordsSheet } from "./coords";

export const sheets: Record<string, SheetComponent> = {
  targets: TargetsSheet,
  brief: BriefSheet,
  quick: QuickSessionSheet,
  flow: FlowCardSheet,
  sites: SitesSheet,
  horizon: HorizonSheet,
  coords: CoordsSheet,
};
