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
//
// THE ONE LINE UNDER THE BAND (D-RIG-3, T-U7b-6). The DEW tile says how many
// degrees of margin the glass has left; the line under the band says what the
// HEATERS are doing about it, from `status.dew`. They are two different facts
// and the second is the one an operator can act on.
//
// WHY THIS FILE READS THE STORE and its five props do not grow to seven: the
// dew loop is a RIG reading, not a weather one - it arrives on the status bus,
// not in the weather payload - and the screen above this band fetches weather.
// `next/ui/*` primitives are the store-free layer; a hub component reading the
// store with narrow selectors is ARCHITECTURE section 9's own rule.
//
// `view.weather` GATES THE READINGS, NOT THE LINE. `api/redact.py:151-173`
// deletes the margin, the air temperature and the dew point and nulls
// `power_pct`, while `enabled`, `following`, `reason` and `ports` survive - so
// a principal without it still learns whether anything is being done about the
// dew point, and the DEW tile above says the capability is what is missing
// rather than blaming the feed for a field it did send.

import type { JSX } from "react";
import { Mono, ReadoutGrid, ReadoutTile } from "../../../ui";
import type { Tone } from "../../../ui";
import type { MoonInfo, WeatherNow } from "../../../../types";
import { useConfig, useStatus } from "../../../../store";
import { accessPhrase, useCanViewWeather } from "../../../../lib/caps";
import { dewBandLine, dewView } from "../../rig/lib/dewModel";
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

  // --- what the heaters are doing about it ----------------------------------
  const status = useStatus();
  const config = useConfig();
  const canViewWeather = useCanViewWeather();
  const loopLine = dewBandLine(
    dewView(status?.dew, canViewWeather),
    config?.dew?.camera_window,
  );
  // A missing margin has two different causes and only one of them is the feed.
  const dewSub = margin === null
    ? (canViewWeather
      ? "ambient or dew point missing"
      : `the dew margin needs ${accessPhrase("view.weather")}`)
    : `ambient ${(tempC as number).toFixed(1)} · dew ${(dewC as number).toFixed(1)}`;

  return (
    <>
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
          // "not reported" BLAMES THE FEED, and for a principal without
          // `view.weather` the feed sent the number: `api/redact.py:151-173`
          // deletes it on the way out. Two different facts, two different words
          // - and the sub line beside this one already names the capability, so
          // a tile reading "not reported" over it contradicted its own caption.
          value={margin !== null
            ? `${margin.toFixed(1)}°C`
            : canViewWeather ? "not reported" : "hidden"}
          sub={dewSub}
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
      {loopLine != null && (
        <div data-testid="wx-dew-loop">
          {/* "heaters", not "dew heaters": every sentence the loop produces
              already names the dew point or the dew margin, and the tile
              immediately above this line is labelled DEW. */}
          <Mono size={10.5} tone="dim">{`heaters · ${loopLine}`}</Mono>
        </div>
      )}
    </>
  );
}
