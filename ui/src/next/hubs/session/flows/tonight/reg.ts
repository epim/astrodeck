// tonight/reg.ts - the registry ENTRY for this area's sheet, and nothing else
// (wave R7 follow-up T-R7-21a item 16).
//
// WHY THIS IS ITS OWN FILE. `session/sheets/index.ts` is reached synchronously
// from `hubs/index.ts`, which is in the entry chunk, so a static import of the
// area BARREL is paid for before first paint (D-FU-2) - and this area's barrel
// re-exports `TonightSheet`, the timeline, the story list, the plan block, the
// campaign card and the calibration matrix, every one of them statically. The
// registry entry needs none of them: it needs an id and a loader.
//
// `tonight.css` stays on `TonightSheet.tsx`, which is the module the loader
// below fetches, so the stylesheet arrives with the chunk that uses it.

import type { SheetRegistry } from "../../../sheets";

/** Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
 *  collision), which is why this one is `flowTonight` rather than `tonight`. */
export const flowTonightSheets: SheetRegistry = {
  flowTonight: {
    id: "session/flows/tonight/TonightSheet",
    load: () => import("./TonightSheet").then((m) => ({ default: m.FlowTonightSheet })),
  },
};
