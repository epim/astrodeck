// next/hubs/settings/tuning/files - the four FILES-AND-STANDARDS editors,
// rebuilt (wave R7, T-R7-13).
//
// The five mount files (`settings/sheets/{Sync,Naming,Wcs,Standards}Sheet.tsx`
// and the plate-solve card in `settings/sheets/OpticsSheet.tsx`) import from
// here and nowhere else, so `components/settings/{SyncPanel,NamingPanel,
// WcsStampPanel,StandardsPanel}.tsx` are no longer reachable from anything
// under `ui/src/next/**`. The legacy files are untouched and still serve
// `#/classic`.

export { SyncEditor } from "./SyncEditor";
export { NamingEditor } from "./NamingEditor";
export { WcsStampEditor } from "./WcsStampEditor";
export { StandardsEditor } from "./StandardsEditor";

// The pure module, exported so a test grades the sentences the screen prints
// rather than ones it typed itself - and so `formatBytes` / `syncSummary` have
// a home outside `components/settings/SyncPanel.tsx` (wave plan section 2.1's
// finding: importing a helper out of a presentation module drags that module's
// whole legacy render tree into the lazily-split next bundle).
export {
  filesLockSentence, formatBytes, lastPassLine, namingPreview, NAMING_SAMPLE,
  plainDashes, pushBlockedReason, STANDARDS_INPUT, syncCadenceLine,
  syncEnableBlockedReason, syncSummary, tokenText,
  type SyncTone,
} from "./filesModel";
