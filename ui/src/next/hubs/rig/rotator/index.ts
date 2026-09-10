// next/hubs/rig/rotator - the ROTATOR sheet's body, rebuilt (wave R7, T-R7-8).
//
// The mount file `hubs/rig/sheets/rotator.tsx` imports from here and nowhere
// else, so `components/equipment/RotatorCard.tsx` is no longer reachable from
// anything under `ui/src/next/**`. The legacy file is untouched and still
// serves `#/classic`.

export { RotatorPanel } from "./RotatorPanel";
export { RotatorArc } from "./RotatorArc";

// Re-exported rather than imported from the legacy card (wave plan section
// 2.1's finding): pulling a constant out of a presentation module drags that
// module's whole render tree into the lazily-split next bundle.
export {
  DEFAULT_ROTATOR_CFG, RANGE_LABEL, RANGE_OPTIONS, CONFIG_LOCK_NOTE,
  MOTION_LOCK_NOTE, lockNote, outOfRangeLine, paStops, toleranceStops, toNum,
} from "./rotatorModel";
