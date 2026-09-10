// WeatherSettingsSheet.tsx - the `weatherSettings` sheet.
//
// The body is `components/settings/WeatherPanel` MOUNTED AS-IS. It already
// implements every field this sheet owes (enabled, cloud threshold 0-100 %,
// sustained-for 15-240 min, the write-only Astrospheric key with its "Clear
// key" verb), the default-site warning, the 409 reload-and-retoast, the 403
// capability sentence and the unsaved-draft chip.
//
// THE SECRET ECHO PATTERN IS NOT TO BE "IMPROVED". The panel never re-sends the
// key it read back; an empty string on write means "leave unchanged"; only
// `astrospheric_configured: boolean` ever comes back from the server. Wrapping
// it in anything that reseeds or remounts on a config change would throw away a
// half-typed key, so this file adds a frame and nothing else - in particular no
// `key={config.version}`.

import type { JSX } from "react";
import WeatherPanel from "../../../../components/settings/WeatherPanel";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { Sheet } from "../../../ui";

export function WeatherSettingsSheet(): JSX.Element {
  return (
    <Sheet
      title="WEATHER"
      sub="forecast, hold threshold and the optional seeing feed"
      icon={<NxIcon name="weather" size={18} />}
      onBack={() => nav.back()}
      data-testid="sheet-weather-settings"
    >
      <WeatherPanel />
    </Sheet>
  );
}
