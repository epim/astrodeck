// session/flows/inspector/index.ts - what wave R7's cutover task (T-R7-20)
// composes from this area.
//
// The three named exports the brief fixes are `FlowInspectorColumn`,
// `FlowPaletteRail` and `flowInspectorSheets` (registering `flowNode` and
// `flowPalette`). Everything else below is a seam the canvas or the cutover
// needs: the two copy constants, the palette opener, and the pure modules a
// test or a sibling surface can reuse without pulling a component in.
//
// This area imports NO sibling R7 directory (`canvas/`, `tonight/`, `create/`).
// The one place a sibling's component belongs - the calibration matrix inside a
// `calib` node - is a `calibSlot` prop on `FlowInspectorColumn`, filled at
// cutover.

export {
  FlowInspectorColumn, FLOW_READONLY_REASON, INSPECTOR_FOOTER,
  type FlowInspectorColumnProps,
} from "./FlowInspectorColumn";
export { FlowNodeEditor, type FlowNodeEditorProps } from "./FlowNodeEditor";
export { FlowFieldRow, type FlowFieldRowProps, type FlowFieldVariant } from "./FlowFieldRow";
export { FlowCyclePlanRows, type FlowCyclePlanRowsProps } from "./FlowCyclePlanRows";
export {
  FlowPaletteRail, useOpenFlowPalette,
  ADD_STAGE_LABEL, ADD_STAGE_TITLE, PALETTE_FOOTER,
  type FlowPaletteRailProps,
} from "./FlowPaletteRail";
export { flowInspectorSheets, FlowNodeSheet, FlowPaletteSheet, NO_STAGE_HINT } from "./sheets";
export { FLOW_NODE_SHEET, FLOW_PALETTE_SHEET } from "./reg";

export {
  selectOptions, fieldIsNumeric, numericValue, splitUnit, fieldAriaLabel, filterInk,
  SEGMENTED_MAX, UNIT_INLINE_MAX,
} from "./fieldModel";
export {
  splitUnmapped, levelWord, RIG_ADVISORY, noteRows, stageNameFor, markSentence,
  nodeMarkLevel, nodeMarkDetail, markWord, markTone, isLoss, rigValueFor,
  MARK_LOST, MARK_PARTIAL, MARK_RIG, NOTES_LEAD, CARRIED_TAG, FROM_RIG_TAG,
  RIG_VALUE_PREFIX,
  type UnmappedSplit, type UnmappedLevel, type NoteRow,
} from "./issues";
export { paletteDropPoint, PALETTE_FALLBACK_DROP } from "./paletteDrop";
