// settings/sheets/index.ts - the SETTINGS hub's sheet registry
// (`ARCHITECTURE.md` section 5). `hubs/index.ts` composes the global registry
// from all six hubs' `sheets` exports at module load, and this is settings'.
//
// FOUR TASKS, FOUR FILES, ONE OBJECT. T-SET-2, T-SET-3 and T-SET-4 each hand
// their sheets over as one object (`set2.ts` / `set3.ts` / `set4.ts`) rather
// than editing this file, so the four could be written at the same time without
// four writers on one line. This file spreads them and adds its own two.
//
// `sites` and `horizon` are the SKY hub's modules, registered here a second
// time UNDER THE SAME NAMES AND THE SAME IDS. `hubs/index.ts` allows exactly
// that and refuses the thing that would actually break - two DIFFERENT modules
// under one name - at module load rather than at the tap that opens the wrong
// screen. Settings
// links to them (`nav.sheet("sites")`, `nav.sheet("horizon", {site:"current"})`)
// and re-plans nothing; registering them here means those links keep working
// even from a build where the Sky hub's own registry has not landed yet.
//
// This file REPLACES the placeholder `settings/sheets.ts`, which shadowed this
// directory: with both present, `./settings/sheets` resolved to the .ts file and
// nothing in here was ever loaded.

import type { SheetRegistry } from "../../sheets";

import { sheets2 } from "./set2";
import { sheets3 } from "./set3";
import { sheets4 } from "./set4";

export const sheets: SheetRegistry = {
  ...sheets2,
  ...sheets3,
  ...sheets4,
  setup: { id: "settings/sheets/SetupSheet", load: () => import("./SetupSheet").then((m) => ({ default: m.SetupSheet })) },
  quickDefaults: {
    id: "settings/sheets/QuickDefaultsSheet",
    load: () => import("./QuickDefaultsSheet").then((m) => ({ default: m.QuickDefaultsSheet })),
  },
  // The SKY hub's two sheets, under the SAME ids the Sky registry gives them
  // and loading the same two modules. That is the shared-name contract working:
  // one id is one screen, and the check in `hubs/index.ts` refuses only the
  // other case - one name over two different modules.
  sites: {
    id: "sky/sheets/sites",
    load: () => import("../../sky/sheets/sites").then((m) => ({ default: m.SitesSheet })),
  },
  horizon: {
    id: "sky/sheets/horizon",
    load: () => import("../../sky/sheets/horizon").then((m) => ({ default: m.HorizonSheet })),
  },
};

export default sheets;
