// session/report/index.ts - what the night-report area publishes.
//
// The mount file (`session/sheets/report.tsx`) imports `ReportScreen` and
// nothing else. The pure model is exported beside it because the report's
// sentences and its filter-token map are worth asserting without a DOM, and
// because a later task rebuilding the Files sheet's bundle block should reuse
// this copy rather than write a third version of it.

export { ReportScreen } from "./ReportScreen";
export { ReportPicker } from "./ReportPicker";
export { ReportSummary } from "./ReportSummary";
export { ByFilterCard, ByTargetCard, FilterRow } from "./FilterRows";
export { SafetyEvents } from "./SafetyEvents";
export { Trends } from "./Trends";
export { BundlePanel } from "./BundlePanel";
export { useReportData, type ReportData, type BundleOptionsState } from "./useReportData";
export {
  endReasonTone, endReasonWord, filterColor, filterLabel, filterLine,
  filterToken, fmtClock, fmtEventClock, fmtStamp, groupCountLine, groupLine,
  hyphenate, hyphenateOrNull, keptLine, MATERIALIZE_CAP_REASON,
  materializePreviewReason, materializeReason, rejectedLine, REPORT_COPY,
  trendPoints, type FilterToken, type TrendPointT,
} from "./reportModel";
