// RadarScreen.tsx - WEATHER · RADAR (plan section A.4).
//
// `components/weather/RadarMap` is MOUNTED WHOLE and takes no props: it reads
// the site, the mount and the weather slice itself (`RadarMap.tsx:68-77`).
// Everything GAP-ANALYSIS section 10 lists as Partial is already inside it -
// the two layers, refresh, recentre with its locked reason, the four-state
// per-layer tile-health badge that stays visible when healthy so a later
// failure has a baseline, the zoom stepper, and the scope pierce-point overlay
// with its wedge, dashed sight-line and low/mid/high deck dots.
//
// TWO THINGS THIS FILE IS CAREFUL ABOUT:
//
//  1. It does NOT mount the map when weather is switched off. Mounting it would
//     fire a grid of tile requests through the server-side IEM proxy for a
//     feature the operator has turned off, and the badge would then report the
//     health of imagery nobody asked for.
//  2. It prints the SECOND half of the design's caption only. `RadarMap` renders
//     its own IEM attribution line; a second copy would be two claims about one
//     source, and the compliance line is the one that must not be duplicated or
//     paraphrased.
//
// THE CHROME AROUND THE MAP IS A `Card`, NOT A `<Panel>` (wave R7, T-R7-15;
// plan section 2.3, which classifies `RadarMap` itself as a KEEP - a tile map
// and its layer maths, with no design analogue - and rewraps only what this
// file owns). What this file owns is now the design's card.
//
// NO CARD INSIDE THE CARD: `RadarMap` takes an additive `chrome?: "panel" |
// "bare"` prop (the one `SkyDomePanel` took in 1474867c) and is mounted "bare"
// here, so it draws the map, the controls and the IEM attribution with no
// `<Panel>` title bar of its own. "panel" is the default, so `#/classic`'s
// monitor grid is untouched. The other next-UI mount site
// (`monitor/live/LiveScreen.tsx`) passes "bare" for the same reason.

import type { CSSProperties, JSX } from "react";
import RadarMap from "../../../../components/weather/RadarMap";
import { useWeather } from "../../../../store";
import { useLock } from "../../../lib/gateHook";
import { nav } from "../../../router";
import { ActionButton, Card, Label, Mono } from "../../../ui";
import { WEATHER_OFF_HINT, WEATHER_OFF_TITLE } from "../conditions/verdict";

/** The design's caption, minus its first clause (which `RadarMap` already
 *  prints as its attribution). `proto/12-weather-dome.html`, last radar block. */
export const PIERCE_NOTE =
  "The dashed ray is your line of sight pierced through the low, mid and high decks: "
  + "a low target looks through far more sideways sky.";

const COL: CSSProperties = {
  display: "flex", flexDirection: "column", gap: 10, padding: "0 2px 24px",
};

export function RadarScreen(): JSX.Element {
  const weather = useWeather();
  // Same gate as the hub header's gear: one sheet, one rule for every door.
  const { lockedReason: settingsLock, onExplain } = useLock({ cap: "config.site_optics" });
  const off = !weather || !weather.enabled;

  if (off) {
    return (
      <div data-testid="wx-radar" style={COL}>
        <Card tone="dashed" data-testid="wx-radar-off">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <Label size={11}>WEATHER IS OFF</Label>
            <Mono size={11} tone="dim">{`${WEATHER_OFF_TITLE} ${WEATHER_OFF_HINT}`}</Mono>
            <Mono size={10} tone="dim">
              No radar or satellite tiles are fetched while it is off.
            </Mono>
            <ActionButton
              kind="secondary"
              onPress={() => nav.sheet("weatherSettings")}
              lockedReason={settingsLock}
              onExplain={onExplain}
              data-testid="wx-radar-off-cta"
            >
              WEATHER SETTINGS
            </ActionButton>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div data-testid="wx-radar" style={COL}>
      <Card data-testid="wx-radar-card">
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <RadarMap chrome="bare" />
          <Mono size={10} tone="dim">{PIERCE_NOTE}</Mono>
        </div>
      </Card>
    </div>
  );
}
