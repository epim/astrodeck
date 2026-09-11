// The Weather tuning area (wave R7, T-R7-15). Two entry points, one for each
// sheet that mounts a rebuilt panel:
//
//   * `WeatherTuningPanel`  - the body of `sheets/WeatherSettingsSheet.tsx`
//     (plan 3.F18, replacing `components/settings/WeatherPanel`).
//   * `CloudmapTuningPanel` - the body of `sheets/CloudmapSheet.tsx`
//     (plan 3.F19, replacing `components/settings/CloudmapPanel`).
//
// THIS FILE OWNS THE AREA'S CSS IMPORT, and that is deliberate. The area has two
// roots and neither renders the other, so pinning the import to one panel would
// leave the other unstyled for anyone who opened only that sheet. Both sheets
// import their panel FROM HERE, so entering the area by any door loads the
// stylesheet. Precedent: `session/flows/create/index.ts`.
import "./tuning.css";

export { WeatherTuningPanel } from "./WeatherTuningPanel";
export { CloudmapTuningPanel } from "./CloudmapTuningPanel";

// The pure modules are exported too, so a test grades the sentences the screen
// prints rather than ones it typed itself, and so the bounds this UI enforces
// can be checked against the server's.
export {
  ASTRO_EMPTY_REASON, ASTRO_LABEL, ASTRO_SET_HINT, keyStateLabel, policyLine, SUSTAIN_MAX,
  SUSTAIN_MIN, THRESHOLD_MAX, THRESHOLD_MIN, weatherLockSentence, WX_CONFLICT,
  WX_DEFAULT_SITE_WARN, WX_EMPTY_TITLE, WX_INTRO, WX_SAVE_FAILED, WX_TITLE,
} from "./weatherModel";
export {
  CM_CONFLICT, CM_EMPTY_TITLE, CM_INTRO, CM_SAVE_FAILED, CM_TITLE, cloudmapLockSentence, costLine,
  HALF_MAX, HALF_MIN, megabytesPerHour, mispinLine, PLATFORM_LABEL, PLATFORM_NOTE, PLATFORM_ORDER,
  POLL_MAX, POLL_MIN, SWITCH_TO_AUTO, type Platform,
} from "./cloudmapModel";
