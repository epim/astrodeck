// next/hubs/rig/inspect - the INSPECT sheet's instruments, rebuilt (wave R7,
// T-R7-19).
//
// The mount file `hubs/rig/sheets/inspect.tsx` imports from here and nowhere
// else, so seven legacy presentation modules (`components/preview/
// {PreviewToolbar,StretchHistogram,PreviewMeta,FrameStats,FrameFilmstrip,
// LiveStackReadout,FocusVerdict}.tsx`) are no longer reachable from that sheet.
// None of them is edited, deleted or moved: `#/classic` imports them still.
//
// `components/preview/PreviewStage.tsx` is deliberately NOT here. It is a KEEP
// (wave plan section 2.3): the double-buffered image swap, the `.preview-transform`
// layer and the overlay canvas are the pixel pipeline, and the design has no
// analogue for it. Only its chrome moved, and this is where the chrome went.
//
// This module is the area ROOT: it is the one import site for `inspect.css`, so
// whichever instrument renders first, none of them can render unstyled.
//
// The pure model is exported too, so the sheet's test can grade the sentences
// the screen prints rather than ones it typed itself.

import "./inspect.css";

export { InspectToolbar, type InspectToolbarProps } from "./InspectToolbar";
export { StretchPanel, type StretchPanelProps } from "./StretchPanel";
export { FrameMetaRow, FrameStatsGrid } from "./FrameReadouts";
export { Filmstrip } from "./Filmstrip";
export { LiveStackLine } from "./LiveStackLine";
// `AutofocusLine` is exported for the named follow-up at
// `hubs/rig/sheets/focuser.tsx`, which still mounts the legacy `AutofocusVerdict`.
export { FocusLine, AutofocusLine } from "./FocusLine";

export {
  DISPLAY_KEEP, LINEAR_KEEP, NINA_LOCK_REASON,
  ageStr, autofocusLine, clipLockReason, curveFrom, downloadLockReason, downloadRows,
  focusLine, framesBehind, hfrGlyph, histogramDomainLine, histogramPath, hyphens,
  isClipped, linearState, liveStackLine, magnifierLockReason, metaTiles, sourceLine,
  stageFrameId, statText, statTiles, toggleNext, toggleOn, toggleRows, zoomLockReason,
  zoomText,
  type AutofocusLineModel, type DownloadInput, type DownloadRow, type FocusLineModel,
  type LiveStackLineModel, type MetaTile, type StatTile, type ToggleId, type ToggleRow,
} from "./inspectModel";
