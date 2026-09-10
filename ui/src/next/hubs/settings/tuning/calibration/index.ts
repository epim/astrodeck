// The Calibration and Sky-pack tuning area (wave R7, T-R7-14). Three editors
// across two sheets:
//
//   * `CalibrationLibraryEditor` + `CalibrationTolerancesEditor` - the body of
//     Settings > MORE > CALIBRATION.
//   * `SkyPackEditor` - the body of Settings > MORE > SKY ATLAS OFFLINE PACK.
//
// This module is the area's root: both sheets import from here, so the ONE css
// import below is guaranteed to run whichever sheet the user opens first, and
// neither can be rendered unstyled.
//
// The pure model is exported too, so a test grades the sentences the screens
// print rather than ones it typed itself.

import "./calibration.css";

export { CalibrationLibraryEditor } from "./CalibrationLibraryEditor";
export { CalibrationTolerancesEditor } from "./CalibrationTolerancesEditor";
export { SkyPackEditor } from "./SkyPackEditor";
export {
  binTooNarrow, binWarning, buildReportLine, groupHeading, libraryLoadError,
  libraryLockSentence, surveyLockSentence, toleranceLockSentence, toleranceSummary,
  BUILD_LABEL, LIB_EMPTY_TITLE, LIB_TITLE, PACK_ATTRIBUTION, PACK_DELETE_TITLE,
  PACK_FETCHING_REASON, PACK_RETRY_HINT, TOL_ALREADY_DEFAULT_REASON, TOL_BIN_REASON,
  TOL_CLEAN_REASON, TOL_DEFAULTS, TOL_TITLE, TOLERANCE_FIELDS, type ToleranceField,
} from "./calibrationModel";
