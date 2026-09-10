// session/gallery/frames/index.ts - what the FRAME LIBRARY area publishes, and
// the single import site for `frames.css`.
//
// The mount file (`session/sheets/archive.tsx`) imports from here and from
// nothing else in this directory. The css is imported HERE rather than from one
// of the four components because the area has four independent entry points -
// the grid, the viewer, the bin and the live stack - and each is rendered by a
// different tab. Hanging the stylesheet off any one of them would leave the
// other three unstyled the first time they are the only thing on screen.
//
// `frameCopy` is exported alongside the components so the sentences that decide
// whether someone downloads a 26 MP FITS or bins a night's work can be asserted
// without a DOM.

import "./frames.css";

export { FrameGrid, FrameTile, TileSurface, type TileState } from "./FrameTile";
export { FrameViewer } from "./FrameViewer";
export { TrashPanel } from "./TrashPanel";
export { SessionStackPanel } from "./SessionStackPanel";
export {
  bytesLabel, countLabel, costLabel, DELETE_LOCK, FITS_LOCK, FITS_LOCK_NOTE,
  FITS_NOTE, hy, lineOrNull, MEDIA_PHRASE, NOT_RESTORABLE, STACK_LOCK,
  STACK_LOCK_NOTE, tileCopy, tileTitle, TRASH_LOCK, trashIntro, trashRowSub,
  VIEWER_FAIL_HINT, viewerLive,
} from "./frameCopy";
