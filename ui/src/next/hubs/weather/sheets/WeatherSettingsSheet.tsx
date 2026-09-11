// WeatherSettingsSheet.tsx - the `weatherSettings` sheet.
//
// The body is `hubs/weather/tuning/WeatherTuningPanel`, the wave-R7 rebuild of
// `components/settings/WeatherPanel` in the design's own vocabulary (plan
// section 3.F18, cutover table section 7). The legacy panel is untouched and
// still serves `#/classic`; this sheet no longer imports it.
//
// It carries the same four things it always owed - enabled, cloud threshold
// 0-100 %, sustained-for 15-240 min, and the write-only Astrospheric key with
// its clear verb - plus the default-site warning, the 409 reload-and-restate,
// the 403 capability sentence and the read-only note.
//
// THE SECRET ECHO PATTERN IS NOT TO BE "IMPROVED". The panel never re-sends the
// key it read back; an empty string on write means "leave unchanged"; only
// `astrospheric_configured: boolean` ever comes back from the server. Wrapping
// this in anything that reseeds or remounts on a config change would throw away
// a half-typed key, so this file adds a frame and nothing else - in particular
// no `key={config.version}`.

import type { JSX } from "react";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { Sheet } from "../../../ui";
import { WeatherTuningPanel } from "../tuning";

export function WeatherSettingsSheet(): JSX.Element {
  return (
    <Sheet
      title="WEATHER"
      sub="forecast, hold threshold and the optional seeing feed"
      icon={<NxIcon name="weather" size={18} />}
      onBack={() => nav.back()}
      data-testid="sheet-weather-settings"
    >
      <WeatherTuningPanel />
    </Sheet>
  );
}
