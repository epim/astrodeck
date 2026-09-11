// next/hubs/settings/tuning/system - the four SYSTEM editors, rebuilt (wave R7,
// T-R7-12): software updates, factory reset, credits and licences, restricted
// assets.
//
// The four mount files `settings/sheets/{Update,FactoryReset,Credits,
// Restricted}Sheet.tsx` import from here and nowhere else, so
// `components/settings/{UpdatePanel,FactoryResetPanel,CreditsPanel,
// RestrictedAssetsPanel}.tsx` are no longer reachable from anything under
// `ui/src/next/**`. Those four legacy files are untouched and still serve
// `#/classic`.

export { UpdateEditor } from "./UpdateEditor";
export { FactoryResetEditor } from "./FactoryResetEditor";
export { CreditsBrowser } from "./CreditsBrowser";
export { RestrictedList } from "./RestrictedList";

// Re-exported rather than imported from the legacy panels (wave plan section
// 2.1's finding): pulling a helper out of a presentation module drags that
// module's whole render tree into the lazily-split next bundle.
export {
  RESET_WORD, clearClientState, confirmWordOk, fmtTime, human, isPipelineActive,
  noNumbersLine, phaseLabel, plural, remedyLabel, rigBlocker, staleRigBlock,
  PHASE_LABEL, RESET_ARM_NOTE, RESET_CAP_NOTE, RESTRICTED_LOCK_NOTE,
  UPDATE_LOCK_NOTE,
} from "./systemModel";
export type { CaptureInventory, ResetPreview, RestrictedAsset } from "./systemModel";
