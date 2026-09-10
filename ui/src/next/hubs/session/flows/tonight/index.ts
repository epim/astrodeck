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

import type { SheetComponent } from "../../../sheets";
import { FlowTonightSheet } from "./TonightSheet";

/** Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
 *  collision), which is why this one is `flowTonight` rather than `tonight`. */
export const flowTonightSheets: Record<string, SheetComponent> = {
  flowTonight: FlowTonightSheet,
};

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
