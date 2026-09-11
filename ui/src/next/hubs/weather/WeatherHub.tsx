// WeatherHub.tsx - the WEATHER hub: CONDITIONS · SKY · RADAR.
//
// The sub-nav chips are the shell's (`shell/SubNav.tsx` reads `HUB_META`); this
// file owns the body and the one control that belongs to all three screens, the
// settings gear.
//
// THE WHOLE HUB IS `view.weather`, AND A NON-HOLDER FIRES NOTHING. A viewer and
// a syncer hold no `view.weather` at all (`auth/capabilities.py:82-124`), and
// the split exists so an operator can have the forecast WITHOUT the rig's
// coordinates - the radar map discloses roughly where the rig is. So the three
// screens are not mounted for a non-holder: the hub renders its frame and the
// reason, and no request is issued rather than three being issued and eating
// their redactions (App.tsx:507's rule, and the DOM test asserts an empty
// `asked[]`).

import { useEffect, type CSSProperties, type JSX } from "react";
import { accessPhrase, useCanViewWeather } from "../../../lib/caps";
import { useLock } from "../../lib/gateHook";
import { nav, SUBS, useRoute } from "../../router";
import { NxIcon } from "../../icons";
import { EmptyCard, IconButton48, Mono } from "../../ui";
import { useWeather } from "../../../store";
import { ConditionsScreen } from "./conditions/ConditionsScreen";
import { DomeScreen } from "./dome/DomeScreen";
import { RadarScreen } from "./radar/RadarScreen";

/** Which section the hub was last left on. Per-phone, `astrodeck-next-`
 *  prefixed, try/catch on every access, correct with nothing stored
 *  (ARCHITECTURE.md section 9). */
const SUB_KEY = "astrodeck-next-wx-sub";

function readSub(): string | null {
  try { return window.localStorage.getItem(SUB_KEY); } catch { return null; }
}
function writeSub(v: string): void {
  try { window.localStorage.setItem(SUB_KEY, v); } catch { /* private mode, or no storage */ }
}

/** Does the CURRENT hash name a section explicitly? The router resolves the
 *  default when it does not, so `route.sub` alone cannot tell "the user asked
 *  for conditions" from "the user asked for weather". An explicit URL wins over
 *  the remembered section, which is why this reads the raw hash. */
function hashNamesSub(): boolean {
  const raw = typeof window === "undefined" ? "" : String(window.location?.hash ?? "");
  const path = (raw.startsWith("#") ? raw.slice(1) : raw).split("?")[0];
  const segs = path.split("/").filter(Boolean);
  return segs.length > 1 && (SUBS.weather as readonly string[]).includes(segs[1]);
}

const COL: CSSProperties = {
  display: "flex", flexDirection: "column", gap: 10, padding: "0 2px",
};

export function WeatherHub(): JSX.Element {
  const route = useRoute();
  const canView = useCanViewWeather();
  const weather = useWeather();
  const { lockedReason: settingsLock, onExplain } = useLock({ cap: "config.site_optics" });

  // Restore the remembered section, but only when the hash did not name one.
  useEffect(() => {
    if (hashNamesSub()) return;
    const saved = readSub();
    if (saved && saved !== route.sub && (SUBS.weather as readonly string[]).includes(saved)) {
      nav.replace(`/weather/${saved}`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if ((SUBS.weather as readonly string[]).includes(route.sub)) writeSub(route.sub);
  }, [route.sub]);

  const rule = weather && weather.enabled
    ? `hold at ${weather.threshold_pct}% for ${weather.sustain_minutes} min`
    : "";

  return (
    <div data-testid="hub-weather" style={COL}>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10,
      }}>
        <Mono size={10} tone="dim">{route.sub === "conditions" ? rule : ""}</Mono>
        <IconButton48
          glyph={<NxIcon name="settings" size={18} />}
          label="WEATHER SETTINGS"
          onPress={() => nav.sheet("weatherSettings")}
          lockedReason={settingsLock}
          onExplain={onExplain}
          data-testid="wx-settings-btn"
        />
      </div>

      {!canView ? (
        <EmptyCard
          data-testid="wx-no-cap"
          title="WEATHER IS NOT VISIBLE TO THIS ROLE"
          hint={
            <span>
              {`Weather needs ${accessPhrase("view.weather")}.`}
              <br />
              Your role can see rig status and previews. Forecast, dome and radar are
              operator surfaces because the radar map discloses roughly where the rig is.
            </span>
          }
        />
      ) : route.sub === "sky" ? (
        <DomeScreen />
      ) : route.sub === "radar" ? (
        <RadarScreen />
      ) : (
        <ConditionsScreen />
      )}
    </div>
  );
}
