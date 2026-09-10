// session/flows/tonight/index.ts - the seam this area publishes.
//
// The Flows cutover task composes the SESSION hub's sheet registry from the
// four flows areas' own `*Sheets` exports; none of the four imports another, so
// a missing export is a compile error at cutover rather than a blank screen.
//
// `CalibrationMatrixCard` is exported beside the sheet because the LIBRARY
// HEALTH block has two homes: the PLAN tab here, and the inspector's CALIBRATION
// QUEUE node. The inspector task cannot import this directory, so the cutover
// wires that second mount.

/** The registry entry itself lives in `reg.ts`, which imports no component at
 *  all. `session/sheets/index.ts` is in the entry chunk and imports THAT, not
 *  this barrel: the static re-exports below are the area's whole component
 *  tree, and pulling them in to register one name is what put the TONIGHT area
 *  in front of first paint (T-R7-21a item 16). */
export { flowTonightSheets } from "./reg";

export { FlowTonightSheet, TONIGHT_NO_FLOW } from "./TonightSheet";
export { CalibrationMatrixCard } from "./CalibrationMatrixCard";
export { TonightTimelineCard } from "./TonightTimelineCard";
export { TonightStoryList } from "./TonightStoryList";
export { TonightPlanBlock } from "./TonightPlanBlock";
export { TonightCampaignCard } from "./TonightCampaignCard";
export {
  TONIGHT_LOCK_REASON, TONIGHT_TABS, TONIGHT_TAB_LABEL, TONIGHT_TAB_SUB,
  nightLine, plainDashes, readCampaign, readTonight, resumesLine, storyStamp,
  storyToneVar, TL_TONE_VAR,
  type TonightRead,
} from "./tonightModel";
