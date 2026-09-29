// wizard/index.ts - the barrel for the #/next Send to Flow Wizard area (#196;
// spec 2026-09-23 flows mosaic, Revision 2 ruling 4, D13).
//
// One sheet, `flowWizard`, and the helper every door into it uses. The
// stepped wizard itself is NOT here and is not re-implemented anywhere under
// `next/`: `FlowWizardSheet` mounts the shared `components/flows/wizard` sheet
// through its lazy door, so importing this barrel costs the adapter, the model
// and the router, never the sheet.
//
// The session hub's registry imports `./reg`, not this barrel (see
// `session/sheets/index.ts`): the barrel re-exports the sheet component, and a
// registry that imported it would put the component in the entry chunk.

export {
  FlowWizardSheet, FLOW_WIZARD_SHEET, WIZARD_LOADING, flowWizardParams, openFlowWizard,
} from "./FlowWizardSheet";
export { flowWizardSheets } from "./reg";
