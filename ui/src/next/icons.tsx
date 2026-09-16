// next/icons.tsx - the design's glyph set for the new front end.
//
// Every path here that exists in the prototype is LIFTED VERBATIM from
// `AstroDeck Mobile.dc.html`'s logic class (`ICONS`, `DEVICE_ICONS`, `KG` for
// the lens-dial target kinds, `AI` for the flow rules, `INC` for the incident
// tiles) so the hub tabs, device tiles and target markers are the same drawings
// the screenshots show. The handful the prototype never drew - rotator, dome,
// and the plain interface chevrons - are drawn in the same idiom: one `d`
// string on a 24x24 grid, no fill, `currentColor`, round caps and joins, so
// they sit beside the lifted ones without looking imported.
//
// This is intentionally NOT `components/icons.tsx`: that set is the legacy
// UI's and is keyed by a different name union. Both stay.
import type { JSX } from "react";

export type NxIconName =
  // the six hubs
  | "sky" | "weather" | "session" | "rig" | "monitor" | "settings"
  // device glyphs
  | "camera" | "mount" | "focuser" | "wheel" | "guider" | "safety" | "power"
  | "rotator" | "dome"
  // interface
  | "back" | "funnel" | "layers" | "info" | "x" | "check"
  | "chevron-right" | "chevron-down" | "lock"
  | "play" | "pause" | "stop" | "download" | "share" | "refresh"
  | "plus" | "minus" | "search" | "gps" | "aperture" | "constellation" | "compass-rose"
  // sky and weather
  | "sun" | "moon" | "star" | "galaxy" | "nebula" | "cluster" | "planet"
  | "comet" | "satellite" | "wind" | "gauge"
  // flow / device extras
  | "flows" | "optics" | "horizon" | "clock" | "temp" | "drop" | "eye";

const PATHS: Record<NxIconName, string> = {
  // --- hubs (prototype ICONS) ---------------------------------------------
  sky: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 1v3M12 20v3M1 12h3M20 12h3M15.5 8.5l-2 5-5 2 2-5z",
  weather: "M7 18h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.2 9.1A4.5 4.5 0 0 0 7 18z",
  session: "M4 7a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3zM10 9l5 3-5 3z",
  rig: "M5.5 12.5l11-7.5 2.2 3.2-11 7.5zM16.5 5l1.4-1 2.2 3.2-1.4 1M5.5 12.5l2.2 3.2M12 13.5V17M12 17l-4.5 5M12 17l4.5 5M12 17v5",
  monitor: "M3 13h4l2-6 4 12 2-6h6",
  settings: "M12 8.5a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7zM19.4 13.5a7.8 7.8 0 0 0 0-3l2-1.5-2-3.4-2.4.9a7.8 7.8 0 0 0-2.6-1.5L14 2.5h-4l-.4 2.5a7.8 7.8 0 0 0-2.6 1.5l-2.4-.9-2 3.4 2 1.5a7.8 7.8 0 0 0 0 3l-2 1.5 2 3.4 2.4-.9a7.8 7.8 0 0 0 2.6 1.5l.4 2.5h4l.4-2.5a7.8 7.8 0 0 0 2.6-1.5l2.4.9 2-3.4z",

  // --- devices (prototype DEVICE_ICONS) -----------------------------------
  camera: "M4 8h3l2-3h6l2 3h3v11H4zM12 10a3.5 3.5 0 1 0 0 7a3.5 3.5 0 1 0 0-7",
  mount: "M5.5 12.5l11-7.5 2.2 3.2-11 7.5zM16.5 5l1.4-1 2.2 3.2-1.4 1M5.5 12.5l2.2 3.2M12 13.5V17M12 17l-4.5 5M12 17l4.5 5M12 17v5",
  focuser: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 7.5a4.5 4.5 0 1 0 0 9a4.5 4.5 0 1 0 0-9M12 3v2M12 19v2M3 12h2M19 12h2",
  wheel: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 6.5a1.4 1.4 0 1 0 0 2.8a1.4 1.4 0 1 0 0-2.8M16.8 9.3a1.4 1.4 0 1 0 0 2.8a1.4 1.4 0 1 0 0-2.8M15 15a1.4 1.4 0 1 0 0 2.8a1.4 1.4 0 1 0 0-2.8M9 15a1.4 1.4 0 1 0 0 2.8a1.4 1.4 0 1 0 0-2.8M7.2 9.3a1.4 1.4 0 1 0 0 2.8a1.4 1.4 0 1 0 0-2.8",
  guider: "M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16M12 2v4M12 18v4M2 12h4M18 12h4M12 10.5a1.5 1.5 0 1 0 0 3a1.5 1.5 0 1 0 0-3",
  safety: "M12 3l8 3v6c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V6zM9 12l2 2 4-4",
  power: "M13 2L5 13h6l-1 9 9-12h-6z",
  // Not in the prototype: a camera-angle ring with its rotation arrow.
  rotator: "M20 12a8 8 0 1 1-2.4-5.7M20 3.2V7h-3.8M9.5 9.5h5v5h-5z",
  // Not in the prototype: a roll-off dome with its slit.
  dome: "M4 20h16M5.5 20v-6a6.5 6.5 0 0 1 13 0v6M12 20V7.6",

  // --- interface ----------------------------------------------------------
  aperture: "M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20M14.8 2.4l-5.6 9.7M21.7 9.2H10.5M18.9 19.2l-5.6-9.7M9.2 21.6l5.6-9.7M2.3 14.8h11.2M5.1 4.8l5.6 9.7",
  constellation: "M4 6l7 3 7-5-2 12-9 4-3-14M11 9l5 7M4 4.8a1.2 1.2 0 1 0 0 2.4a1.2 1.2 0 1 0 0-2.4M11 7.8a1.2 1.2 0 1 0 0 2.4a1.2 1.2 0 1 0 0-2.4M18 2.8a1.2 1.2 0 1 0 0 2.4a1.2 1.2 0 1 0 0-2.4M16 14.8a1.2 1.2 0 1 0 0 2.4a1.2 1.2 0 1 0 0-2.4M7 18.8a1.2 1.2 0 1 0 0 2.4a1.2 1.2 0 1 0 0-2.4",
  "compass-rose": "M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4zM12 2v20M2 12h20M5 5l3 3M16 16l3 3M19 5l-3 3M8 16l-3 3",
  back: "M15 5l-7 7 7 7",
  funnel: "M4 5h16l-6 7v6l-4 2v-8z",
  layers: "M12 3l9 5-9 5-9-5 9-5zM3 13l9 5 9-5",
  info: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 11v6M12 7.6h.01",
  x: "M6 6l12 12M18 6L6 18",
  check: "M5 12.5l5 5L19 7",
  "chevron-right": "M9 5l7 7-7 7",
  "chevron-down": "M5 9l7 7 7-7",
  lock: "M6 11h12v10H6zM9 11V7a3 3 0 0 1 6 0v4",
  play: "M8 5l11 7-11 7z",
  pause: "M9 5v14M15 5v14",
  stop: "M6 6h12v12H6z",
  download: "M12 3v12M6 10l6 6 6-6M4 21h16",
  share: "M12 3v12M8 7l4-4 4 4M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5",
  refresh: "M20 12a8 8 0 1 1-2.4-5.7M20 3.2V7h-3.8",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  search: "M11 4a7 7 0 1 0 0 14a7 7 0 1 0 0-14M16.2 16.2L20 20",
  gps: "M12 21s-6-5.3-6-11a6 6 0 0 1 12 0c0 5.7-6 11-6 11zM12 8a2 2 0 1 0 0 4a2 2 0 1 0 0-4",

  // --- sky and weather (prototype KG / ICONS / AI) -------------------------
  sun: "M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8M12 2v3M12 19v3M2 12h3M19 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1",
  moon: "M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z",
  star: "M12 2l3 7h7l-5.5 4.5 2 7.5L12 17l-6.5 4 2-7.5L2 9h7z",
  galaxy: "M3 12a9 4.5 0 1 0 18 0a9 4.5 0 1 0-18 0M12 12h.01",
  nebula: "M7 18h10a4 4 0 0 0 .6-7.95A6 6 0 0 0 6.2 9.1A4.5 4.5 0 0 0 7 18z",
  cluster: "M6 8h.01M12 5h.01M17 9h.01M9 14h.01M15 15h.01M12 19h.01",
  planet: "M7 12a5 5 0 1 0 10 0a5 5 0 1 0-10 0M3 14c4 3 14 3 18-2",
  comet: "M11 12a4 4 0 1 0 8 0a4 4 0 1 0-8 0M11 9L3 3M11 15l-7 5",
  satellite: "M6 8l3-3 4 4-3 3zM14 14l3-3 4 4-3 3zM10 10l4 4M5 19l4-4",
  wind: "M3 8h11a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h8a2 2 0 1 1-2 2",
  gauge: "M4 18a8 8 0 1 1 16 0H4M12 17l4.4-6.4",

  // --- extras used by the header, settings and device sheets ---------------
  flows: "M4 6h6v4H4zM14 6h6v4h-6zM9 14h6v4H9zM10 8h4M7 10v4h5M17 10v4h-5",
  optics: "M4 12a8 8 0 0 1 16 0a8 8 0 0 1-16 0M2 12h20M12 4v16",
  horizon: "M3 18c3-6 6-9 9-9s6 3 9 9M3 18h18M12 9V3M9 5l3-2 3 2",
  clock: "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18M12 7v5l3 2",
  temp: "M10 4a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0zM12 10v6",
  drop: "M12 3s6 6.5 6 11a6 6 0 0 1-12 0c0-4.5 6-11 6-11z",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6",
};

export interface NxIconProps {
  name: NxIconName;
  size?: number;
  strokeWidth?: number;
  className?: string;
  /** Give a title ONLY when the glyph is the sole carrier of meaning. Beside a
   *  text label it must stay `aria-hidden`, or every row is read twice. */
  title?: string;
}

export function NxIcon({ name, size = 20, strokeWidth, className = "", title }: NxIconProps): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth ?? (size <= 16 ? 1.8 : size <= 20 ? 1.7 : 1.6)}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
      className={`nx-icon ${className}`.trim()}
      data-icon={name}
    >
      {title && <title>{title}</title>}
      <path d={PATHS[name]} />
    </svg>
  );
}

export const NX_ICON_NAMES = Object.keys(PATHS) as NxIconName[];
