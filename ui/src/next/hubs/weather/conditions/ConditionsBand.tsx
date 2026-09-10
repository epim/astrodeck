// ConditionsBand.tsx - the six-tile band: WIND, HUMIDITY, SEEING,
// TRANSPARENCY, DEW, MOON (README section 7).
//
// EVERY TILE KEEPS ITS SLOT. A field the feed does not carry renders its own
// reason ("not reported", "not measured", why) instead of disappearing: six
// tiles that become four read as "not applicable here", which is a different
// and wrong claim, and the grid reflows under the reader's thumb every time
// the upstream drops a series.
//
// EVERY FIELD IS INDEPENDENTLY NULLABLE. `normalizeNow` (`lib/weather.ts:210-229`)
// nulls each one on its own - "a station reporting wind but no dew point is a
// real thing, and it must not take the whole reading down" - so each clause
// below is dropped on its own rather than the tile printing NaN.

import type { JSX } from "react";
import { ReadoutGrid, ReadoutTile } from "../../../ui";
import type { Tone } from "../../../ui";
import type { MoonInfo, WeatherNow } from "../../../../types";
import { drift } from "./verdict";
import { moonSub } from "./moon";

export const NO_ASTRO_HINT = "add an Astrospheric key in Weather settings";
export const NO_WIND_HINT = "this feed carries no wind";
export const NO_MOON_TARGET_HINT = "pick a target in Sky and the moon comes with it";

export interface ConditionsBandProps {
  now: WeatherNow | null;
  /** Nearest-hour Astrospheric samples, or null when there is no key/feed. */
  seeing: number | null;
  transparency: number | null;
  moon: MoonInfo | null;
  moonTargetName: string | null;
  /** Why there is no moon block, when there is none. */
  moonReason: string;
}

export function ConditionsBand({
  now, seeing, transparency, moon, moonTargetName, moonReason,
}: ConditionsBandProps): JSX.Element {
  // --- wind -----------------------------------------------------------------
  const windKmh = now?.wind_kmh ?? null;
  const gust = now?.gust_kmh ?? null;
  const dirDeg = now?.wind_dir_deg ?? null;
  const windClauses: string[] = [];
  if (gust !== null) windClauses.push(`gust ${Math.round(gust)}`);
  if (dirDeg !== null) {
    const d = drift(dirDeg);
    // Both ends, and the arrow on the Sky screen uses the SECOND one. See
    // `drift()` for why printing only the meteorological end is a trap.
    windClauses.push(`${d.from} to ${d.toward} at cloud base`);
  }

  // --- dew margin -----------------------------------------------------------
  const tempC = now?.temp_c ?? null;
  const dewC = now?.dewpoint_c ?? null;
  const margin = tempC !== null && dewC !== null ? tempC - dewC : null;
  const dewTone: Tone | undefined =
    margin === null ? undefined : margin <= 0 ? "bad" : margin < 2 ? "warn" : undefined;

  const humidity = now?.humidity_pct ?? null;

  return (
    <ReadoutGrid cols={3} data-testid="wx-band">
      <ReadoutTile
        label="WIND"
        value={windKmh === null ? "not reported" : `${Math.round(windKmh)} km/h`}
        sub={windKmh === null ? NO_WIND_HINT : (windClauses.join(" · ") || undefined)}
        data-testid="wx-tile-wind"
      />
      <ReadoutTile
        label="HUMIDITY"
        value={humidity === null ? "not reported" : `${Math.round(humidity)}%`}
        sub={margin === null ? undefined : `dew margin ${margin.toFixed(1)}°C`}
        data-testid="wx-tile-humidity"
      />
      <ReadoutTile
        label="SEEING"
        value={seeing === null ? "not measured" : `${seeing}″`}
        sub={seeing === null ? NO_ASTRO_HINT : "Astrospheric · 6 h model"}
        data-testid="wx-tile-seeing"
      />
      <ReadoutTile
        label="TRANSPARENCY"
        // The raw model value, not a word. Astrospheric's transparency scale is
        // not documented anywhere this codebase can see (`weather.py:414` takes
        // `Astrospheric_Transparency` through unchanged), so grading it "above
        // avg" would be a word the number does not support.
        value={transparency === null ? "not measured" : String(transparency)}
        sub={transparency === null ? NO_ASTRO_HINT : "Astrospheric · 6 h model"}
        data-testid="wx-tile-transparency"
      />
      <ReadoutTile
        label="DEW"
        value={margin === null ? "not reported" : `${margin.toFixed(1)}°C`}
        sub={
          margin === null
            ? "ambient or dew point missing"
            : `ambient ${(tempC as number).toFixed(1)} · dew ${(dewC as number).toFixed(1)}`
        }
        tone={dewTone}
        data-testid="wx-tile-dew"
      />
      <ReadoutTile
        label="MOON"
        value={moon === null ? "no target" : `${Math.round(moon.illumination * 100)}%`}
        sub={moon === null ? moonReason : moonSub(moon, moonTargetName)}
        data-testid="wx-tile-moon"
      />
    </ReadoutGrid>
  );
}
