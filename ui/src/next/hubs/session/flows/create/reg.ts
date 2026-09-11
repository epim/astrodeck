// create/reg.ts - the registry ENTRIES for this area's two sheets, and nothing
// else (wave R7 follow-up T-R7-21a item 16).
//
// WHY THIS IS ITS OWN FILE. `session/sheets/index.ts` is reached synchronously
// from `hubs/index.ts`, which is in the entry chunk, so a static import of the
// area BARREL is paid for before first paint (D-FU-2) - and that barrel imports
// `create.css` and re-exports `FlowNewSheet`, `FlowQuickSheet` and their models
// statically. Two names in a map do not need any of it.
//
// `create.css` stays on the barrel AND on the two sheet modules the loaders
// below fetch, so the stylesheet still arrives with the chunk that draws the
// sheets rather than in front of first paint. It is the same file either way,
// so there is no cascade order to get wrong.

import type { SheetRegistry } from "../../../sheets";

/** Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
 *  collision), hence the `flow` prefix. */
export const flowCreateSheets: SheetRegistry = {
  flowNew: {
    id: "session/flows/create/wizard",
    load: () => import("./wizard").then((m) => ({ default: m.FlowNewSheet })),
  },
  flowQuick: {
    id: "session/flows/create/quick",
    load: () => import("./quick").then((m) => ({ default: m.FlowQuickSheet })),
  },
};
