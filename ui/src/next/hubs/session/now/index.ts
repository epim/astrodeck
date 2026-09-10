// now/index.ts - what SESSION / NOW exports.
//
// The seven components at the top are the ones the desktop `SessionColumn`
// mounts (plan section E.4). They read the store themselves and take ONLY
// layout props, which is what lets one implementation serve both densities:
// there is no second copy of the phase mapping, the incident derivation or the
// ledger fold anywhere in this app.

export { NowScreen } from "./NowScreen";

export { RunHeader } from "./RunHeader";
export { IncidentStack } from "./IncidentStack";
export { LiveStack } from "./LiveStack";
export { VitalsBand } from "./VitalsBand";
export { IntegrationBar } from "./IntegrationBar";
export { RunControls } from "./RunControls";
export { NowEmpty } from "./NowEmpty";

export { CampaignLedger } from "./CampaignLedger";
export { ChannelStrip } from "./ChannelStrip";
export { ArmedRules } from "./ArmedRules";
export { PoolChips } from "./PoolChips";
export { Interrupted } from "./Interrupted";
export { NowBanners } from "./NowBanners";
export { QuotaRows, quotaRows, PHONE_EDIT_REASON } from "./QuotaRows";

// Pure pieces, exported for the tests and for the cross-hub data module.
export { phaseOf, laneOf, phaseWord, isTerminalState, PHASE_COLOR } from "./phase";
export type { PhaseInput, PhaseView } from "./phase";
export {
  INCIDENT_ACTIONS, OMITTED_ACTION_IDS, ARMED_ACTION_IDS, INCIDENT_ARM_MS,
  INCIDENT_ARM_LABEL, specFor, refineIncident, actionsFor, runIncidentAction,
} from "./incidentActions";
export type { IncidentActionSpec, ActionContext, RefineContext } from "./incidentActions";
export {
  useCampaign, useCampaignFlowId, paletteWord, CLEAR_HOURS_PER_NIGHT,
  LEDGER_NOTE, NO_LEDGER_NOTE, resetCampaignForTests,
} from "./useCampaign";
export type { BudgetRow, CampaignRead, CampaignState, NightWindow } from "./useCampaign";
export { useActiveSession, useFlowLibrary, resetSessionDataForTests } from "./sessionData";
export {
  useStackView, useSessionStackStatus, STRETCH_FILTER, STRETCH_KEY,
  resetStackViewForTests, resetSessionStackStateForTests,
} from "./stackView";
export type { StretchMode } from "./stackView";
export { useSubFrame } from "./useSubFrame";
export { useEta } from "./useEta";
export { useNowIncidents, useNowMs, suggestedSetpointC } from "./useNowIncidents";
export { armedChips, instructionChip } from "./ArmedRules";
export { rmsWord, flipCell } from "./VitalsBand";
export { filterColor, filterToken, plannedByFilter, acceptedByFilter, tonightNightKey } from "./filters";
export { sendControl } from "./sendControl";
export { RUN_CONTROL_REASON, MANUAL_STOP_NOTE } from "./RunControls";
export { TINT_NOTE } from "./ChannelStrip";
export { NO_SAFETY_WARNING, WEATHER_VETO, WEATHER_OVERRIDE } from "./NowBanners";
export { RERUN_PHONE_REASON, RERUN_TITLE } from "./Interrupted";
