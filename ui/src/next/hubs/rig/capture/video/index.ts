// video/index.ts - the VIDEO mode area root (D-RIG-1).
//
// This module is the ONE import site for `video.css`, the same discipline
// `hubs/rig/inspect/index.ts` keeps: an area stylesheet belongs to the area,
// not to `NextApp.tsx`, and importing it from more than one component would
// make the load order depend on which one rendered first.
//
// Everything a caller outside this folder needs is re-exported here, so the
// capture screen and the VIDEO LIBRARY sheet both import from `./video` and
// neither reaches inside.

import "./video.css";

export { VideoMode, WATCHDOG_MS, NO_VIDEO_ROUTE_NOTE, KEEP_OPTIONS } from "./VideoMode";
export type { VideoModeProps } from "./VideoMode";
export { RecordingsList, NO_RECORDINGS_HINT, keepNote } from "./RecordingsList";
export type { RecordingsListProps } from "./RecordingsList";
export { RoiPicker, ROI_PRESETS } from "./RoiPicker";
export type { RoiPickerProps } from "./RoiPicker";
export { VideoControls } from "./VideoControls";
export type { VideoControlsProps, VideoSettings } from "./VideoControls";
export { useVideoLibrary } from "./useVideoLibrary";
export type { VideoLibrary } from "./useVideoLibrary";
export {
  DEFAULT_ROI_ALIGN, MAX_DURATION_S, SER_HEADER_BYTES, SER_FRAME_TRAILER_BYTES,
  STILL_STILL_WORKS_NOTE, alignRoi, centredRoi, clampNote, diskShortfallSentence,
  estimateBytes, isTerminalVideoState, laneBusy, lcm, mergeVideoState,
  noVideoPathReason, plannedFrames, progress, progressLine, recordingLine,
  recordingStamp, roiAlignNote, roiLine, videoCapability, videoStateFromEvent,
} from "./videoModel";
export type { VideoCapability, VideoProgress } from "./videoModel";
