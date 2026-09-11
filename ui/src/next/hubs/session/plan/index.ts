// plan/index.ts - what the rebuilt plan editor exports.
//
// The mount file (`session/sheets/planEditor.tsx`) composes exactly three of
// these: the body, the sticky footer, and the schedule layer's props. Everything
// else is exported for the tests and for the next task that needs one of these
// sentences to match word for word.
//
// The three sections under `automation/`, `instructions/` and `sessions/`
// belong to T-R7-6 and are NOT re-exported here: their own `index.ts` files are
// the contract, and re-exporting them would put this file in two tasks' hands.

export {
  PlanEditorBody, PlanEditorFooter, usePlanSchedule, closePlanSchedule, SCHEDULE_PARAM,
} from "./PlanEditor";
export { PlanScheduleSheet } from "./PlanScheduleSheet";
export { PlanRunHeader, RUN_CONTROL_REASON, RERUN_TITLE } from "./PlanRunHeader";
export { PlanResume, RESUME_REASON } from "./PlanResume";
export { PlanTargets } from "./PlanTargets";
export { PlanTargetCard } from "./PlanTargetCard";
export { PlanLibrary } from "./PlanLibrary";
export { PlanPreflight, PlanStartFooter, preflightWord, warnLine } from "./PlanPreflight";
export { TargetSpark, resetTargetSparkCacheForTests } from "./TargetSpark";
export { usePlanStart } from "./planStart";
export type { PlanStart } from "./planStart";
export {
  LIVE_SEQUENCE_STATES, isSequenceLive, isSequenceFinished, stateView, targetFrames,
  targetSeconds, planFrames, planSeconds, fmtHoursMinutes, planBlocks, structureLockNote,
  automationLockNote, instructionsLockNote, runningPlanName, useRecoverable, UNDO_MS,
} from "./planModel";
export type { PlanBlock, Recoverable, StateView, UndoState } from "./planModel";
