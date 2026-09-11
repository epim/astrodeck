// create/index.ts - flow creation: the guided wizard and the quick flow
// (wave R7 task T-R7-4, rows A16 and A17).
//
// THIS IS THE AREA'S ONE SEAM. `T-R7-20` composes the flows sheets and registers
// them; it imports `flowCreateSheets` from here and nothing else, so a name this
// area stops exporting is a compile error at the cutover rather than a sheet
// that silently is not in the map.
//
// `create.css` is imported here and, since T-R7-21a item 16, by the two sheet
// modules as well - `wizard.tsx` and `quick.tsx`. It is the same file, so there
// is no cascade order to get wrong; what the second and third import sites buy
// is that the stylesheet arrives with whichever chunk actually draws a sheet,
// now that the registry no longer reaches this barrel at all. `next.css` still
// belongs to T-R7-0 and no area file may touch a primitive class.

import "./create.css";

/** The registry entries live in `reg.ts`, which imports no component and no
 *  stylesheet. `session/sheets/index.ts` is in the entry chunk and imports
 *  THAT, not this barrel: the `import "./create.css"` above and the static
 *  re-exports below are the area, and pulling the area in to register two names
 *  is what put both sheets in front of first paint (T-R7-21a item 16). */
export { flowCreateSheets } from "./reg";

export { FlowNewSheet, newFlowRoute } from "./wizard";
export { FlowQuickSheet, ONE_CHANNEL_NOTE, WHEEL_NOTE, NO_FILTER_REASON } from "./quick";

export {
  AUTOMATIONS, AUTOMATION_DEFAULTS, GENERATE_FAILED, KINDS, TARGET_PLACEHOLDER, WIZARD_NOTE,
  type WizardKind,
} from "./wizardModel";

/**
 * The quick flow's pure helpers, owned by `next`.
 *
 * `sky/sheets/quick.tsx` imports these six names from
 * `components/flows/QuickFlow.tsx` today, which drags the whole 497-line legacy
 * component - `Overlay`, `CatalogSearch`, `HonestButton` and their Tailwind tree
 * - into the lazily split next bundle for the sake of four pure functions and
 * two strings (wave R7 section 2.1's finding). Re-exported here so that import
 * can move; see `quickPayload.ts` for why this is a copy rather than a
 * pass-through, and `__tests__/createDom.test.tsx` for the test that pins the
 * copy to the legacy original.
 *
 * FOLLOW-UP, NOT DONE HERE: `hubs/sky/sheets/quick.tsx` is owned by T-U7a-E
 * right now, so this task does not switch its import. One line changes when it
 * is free - see the task report.
 */
export {
  DEFAULT_SUBS, OSC_LABEL, QUICK_RUN_FAILED, QUICK_SAVE_FAILED, decDms, quickPayload, raHms,
  targetFromEntry, type QuickTarget,
} from "./quickPayload";
