// hubs/index.ts - the hub registry: what each tab is called, what it draws in
// the tab bar and the rail, what its sub-nav chips say, and which component
// renders its body.
//
// The bodies here are PLACEHOLDERS (T0.1 ships the shell; the hub tasks replace
// them one directory at a time). A placeholder renders an EmptyCard naming the
// hub, so a walk through the six tabs is a walk through six distinguishable
// screens rather than six blank ones - which is the difference between "the
// router works" and "the router appears to work".

import type { JSX } from "react";
import type { NxIconName } from "../icons";
import type { SubNavItem, Tone } from "../ui";
import type { HubId } from "../router";
import { SUBS } from "../router";

import { SkyHub } from "./sky/SkyHub";
import { WeatherHub } from "./weather/WeatherHub";
import { SessionHub } from "./session/SessionHub";
import { RigHub } from "./rig/RigHub";
import { MonitorHub } from "./monitor/MonitorHub";
import { SettingsHub } from "./settings/SettingsHub";

import { sheets as skySheets } from "./sky/sheets";
import { sheets as weatherSheets } from "./weather/sheets";
import { sheets as sessionSheets } from "./session/sheets";
import { sheets as rigSheets } from "./rig/sheets";
import { sheets as monitorSheets } from "./monitor/sheets";
import { sheets as settingsSheets } from "./settings/sheets";

export type { SheetComponent, SheetProps } from "./sheets";

/** What a hub's sub-nav is allowed to know. Deliberately a small explicit bag
 *  rather than the whole store: `subs()` stays a pure function of it, so the
 *  chips can be unit-tested and the shell can read each field with one narrow
 *  selector instead of subscribing the sub-nav to every store write. Hub tasks
 *  ADD fields here; nothing reads the store from inside `subs`. */
export interface SubContext {
  /** Saved flows in the library, or null while it has never been read - which
   *  is not the same claim as zero. */
  flowCount: number | null;
  /** The top incident's colour, for the dot on Session - Now. */
  incidentTone: Tone | null;
}

export interface HubMeta {
  id: HubId;
  label: string;
  icon: NxIconName;
  subs: (ctx: SubContext) => SubNavItem[];
}

/** A sub-nav item for every entry in `SUBS[hub]`, uppercase, no counts. The
 *  hubs that have something to count override it. */
function plainSubs(hub: HubId): (ctx: SubContext) => SubNavItem[] {
  const ids = SUBS[hub];
  return () => ids.map((id) => ({ id, label: id.toUpperCase() }));
}

export const HUB_META: Record<HubId, HubMeta> = {
  sky: { id: "sky", label: "SKY", icon: "sky", subs: plainSubs("sky") },
  weather: { id: "weather", label: "WEATHER", icon: "weather", subs: plainSubs("weather") },
  session: {
    id: "session",
    label: "SESSION",
    icon: "session",
    subs: (ctx) => [
      { id: "now", label: "NOW", dot: ctx.incidentTone ?? undefined },
      { id: "gallery", label: "GALLERY" },
      { id: "flows", label: "FLOWS", count: ctx.flowCount ?? undefined },
    ],
  },
  rig: { id: "rig", label: "RIG", icon: "rig", subs: plainSubs("rig") },
  monitor: { id: "monitor", label: "MONITOR", icon: "monitor", subs: plainSubs("monitor") },
  settings: { id: "settings", label: "SETTINGS", icon: "settings", subs: plainSubs("settings") },
};

/** The tab-bar / rail order, which is also the sub-nav-free reading order of
 *  the app: find something, check the sky, watch the run, drive the rig, read
 *  the log, change a setting. */
export const HUB_ORDER: readonly HubId[] = ["sky", "weather", "session", "rig", "monitor", "settings"];

export const HUBS: Record<HubId, () => JSX.Element> = {
  sky: SkyHub,
  weather: WeatherHub,
  session: SessionHub,
  rig: RigHub,
  monitor: MonitorHub,
  settings: SettingsHub,
};

/** The sheet registry, composed from each hub's own `sheets` export.
 *
 *  Names are GLOBAL, and one name is one screen: `sites` is shared by Sky and
 *  Settings and is ONE component registered from both, which is fine - the same
 *  function under the same name is the contract working. What is NOT fine is two
 *  DIFFERENT components under one name: the hash then names a screen that
 *  depends on which hub's module loaded last, so the same URL opens different
 *  things on different builds. That is checked at module load rather than at the
 *  tap that opens the wrong one, because by then it looks like a routing bug. */
const REGISTRIES: Record<string, Record<string, unknown>> = {
  sky: skySheets, weather: weatherSheets, session: sessionSheets,
  rig: rigSheets, monitor: monitorSheets, settings: settingsSheets,
};

function composeSheets() {
  const seen: Record<string, string> = {};
  const out: Record<string, unknown> = {};
  for (const [hub, reg] of Object.entries(REGISTRIES)) {
    for (const name of Object.keys(reg)) {
      if (seen[name] && out[name] !== reg[name]) {
        throw new Error(
          `next/hubs: "${name}" is registered by ${seen[name]} and ${hub} as two different ` +
          "components. Sheet names are global: share the one component, or rename one of them.",
        );
      }
      seen[name] = hub;
      out[name] = reg[name];
    }
  }
  return out;
}

export const SHEETS = composeSheets() as Record<string, import("./sheets").SheetComponent>;
