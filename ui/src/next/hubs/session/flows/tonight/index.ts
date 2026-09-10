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

import type { SheetRegistry } from "../../../sheets";

/** Sheet names are GLOBAL across the app (`hubs/index.ts` throws on a
 *  collision), which is why this one is `flowTonight` rather than `tonight`.
 *
 *  `{ id, load }`, not the component: sheets are code-split (D-FU-2), so the
 *  registry carries a module identity and the one line that fetches it. The
 *  `import()` is deliberately NOT the static import this file used to make -
 *  `hubs/index.ts` is in the entry chunk and pulls every hub's registry
 *  synchronously, so a registry that named its component eagerly would put the
 *  whole TONIGHT area in front of first paint. */
export const flowTonightSheets: SheetRegistry = {
  flowTonight: {
    id: "session/flows/tonight/TonightSheet",
    load: () => import("./TonightSheet").then((m) => ({ default: m.FlowTonightSheet })),
  },
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
