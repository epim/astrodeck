// canvas/index.ts - the Flows canvas area's public surface (wave R7, T-R7-1).
//
// The cutover task composes this with the inspector, tonight and creation areas
// into `FlowsCanvasHost.tsx`; nothing here imports any of them, so a name that
// went missing is a COMPILE error at the cutover rather than a blank rectangle
// on the glass.
//
// The pure helpers below are re-exported from this area rather than imported
// from `components/flows/*`. They are pure, but each one is declared inside a
// legacy PRESENTATION module, and importing one from `next` would drag that
// component and its whole Tailwind/`Panel` tree into the lazily split next
// bundle. The legacy files keep their own copies for `#/classic`; nothing under
// `ui/src/components/**` is edited.

export { FlowCanvasSurface, ADD_STAGE_LABEL, type FlowCanvasSurfaceProps } from "./FlowCanvasSurface";
export { FlowCanvasToolbar, RUN_ARM_LABEL, SAVE_LABEL } from "./FlowCanvasToolbar";
export {
  FlowStagesPhoneSheet, FLOW_STAGES_SHEET, FLOW_STAGES_UNTITLED, stageOrder,
} from "./FlowStagesPhoneSheet";
export { flowCanvasSheets } from "./sheets";

// Pieces the composing screen may want to place itself.
export { FlowNodeCard, FlowPortRow } from "./FlowNode";
export { FlowWireLayer, FlowWireDelete } from "./FlowWires";
export { FlowZoomCluster } from "./FlowZoomCluster";
export { FlowLogStrip } from "./FlowLogStrip";
export { FlowTapWireBar } from "./FlowTapWireBar";

// The pure canvas helpers.
export { clearMountedFlowCanvas, flowCanvasDropPoint, setMountedFlowCanvas } from "./canvasMount";
export {
  PALETTE_FALLBACK_DROP, resolveWireDrop, type WireDropResult,
  wireLane, wireAnchors, wireMidpoint, wireStroke, wireWidth, wireDash,
  isRunning, isWireActive, portKind, portAttr,
  HIT_W_CANVAS, HIT_W_AUTO,
  LOG_TAIL, LOG_TONE, IDLE_LOG_TEXT, logTail, logTime,
  tapWireHint, formatEta, checksLabel, stageWord, framesWord,
  CHECKS_UNKNOWN, CHECKS_UNKNOWN_WHY, CHECKS_CLEAN_WHY, ETA_UNREPORTED, lossesWhy,
  PLAN_TITLE, tonightLockReason,
  NODE_STATUS_WORD, NODE_STATUS_TONE, asNodeStatus,
  // The compile-mark vocabulary, re-exported through `canvasModel` from the
  // one module that owns it (`../inspector/issues`) so the badge on a card and
  // the panel in the inspector cannot drift apart.
  markWord, markTone, isLoss, nodeMarkLevel, nodeMarkDetail, rigValueFor,
  lossCount, worstLoss,
  MARK_LOST, MARK_PARTIAL, MARK_RIG, RIG_VALUE_PREFIX, type UnmappedLevel,
  // The save / run / draft-checks vocabulary the review's P0 added. One home,
  // because the canvas toolbar and the phone stage list both render it and a
  // second copy is how the two surfaces end up disagreeing about whether a flow
  // is safe to start.
  CHECKS_DRAFT_PREFIX, CHECKS_DRAFT_WHY, checksWord, checksTone,
  SAVE_STATE_DIRTY, SAVE_STATE_CLEAN, SAVE_STATE_READONLY,
  SAVE_READONLY_REASON, SAVE_CLEAN_REASON, saveStateWord, saveStateTone, saveLockReason,
  RUN_UNSAVED_REASON, RUN_UNSAVED_EXAMPLE_REASON, unsavedRunReason,
  NO_WIRES_TEXT, wireRowLabel, wireRemoveLabel,
} from "./canvasModel";
