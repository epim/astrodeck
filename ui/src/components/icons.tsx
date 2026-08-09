// AstroDeck icon set v2 — "instrument glyphs".
// Inline SVG, currentColor, 24x24 viewBox, stroke 1.5, round caps/joins.
// Filled 1.2px dots are reserved for stars (rig, atlas, gallery, align, focus).
// Drop-in replacement for the v1 file: same IconName union, same Icon API.
// Notable redraws vs v1: capture (iris shutter — no longer a second sun),
// rig (telescope on a star), focus (lens elements converging on a point),
// power (IEC symbol), bridge (suspension bridge), guide (baseline dropped).
import type { JSX } from "react";

export type IconName =
  // nav
  | "rig" | "capture" | "focus" | "mount" | "align" | "guide" | "plan" | "power"
  | "settings" | "monitor" | "atlas"
  // actions
  | "play" | "stop" | "pause" | "refresh" | "bridge" | "link"
  | "sun" | "moon" | "brightness" | "lock" | "unlock" | "frame"
  // account / rbac
  | "user" | "logout" | "eye" | "key" | "shield"
  // status
  | "alert" | "check" | "info" | "x" | "clock"
  // misc
  | "grid" | "plus" | "trash" | "download" | "upload"
  | "gallery"
  // directional
  | "arrow-up" | "arrow-down" | "arrow-left" | "arrow-right";

export interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
  strokeWidth?: number;
  title?: string;
}

const PATHS: Record<IconName, JSX.Element> = {
  rig: <><path d="M4.8 10.6l6.4-6.4 4.6 4.6-6.4 6.4z" /><path d="M9.4 15.4L6 21M12.6 14.4L15 21" /><circle cx="19.4" cy="4.6" r="1.2" fill="currentColor" stroke="none" /></>,
  capture: <><circle cx="12" cy="12" r="8.8" /><path d="M12 20.8l3.7-6.6M4.4 16.4l7.6-.1M4.4 7.6l3.9 6.6M12 3.2l-3.7 6.6M19.6 7.6l-7.6.1M19.6 16.4l-3.9-6.6" /></>,
  focus: <><path d="M8.2 5.6a7.6 7.6 0 000 12.8M15.8 5.6a7.6 7.6 0 010 12.8M12 2.5v2M12 19.5v2" /><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" /></>,
  mount: <><path d="M12 2.8v3" /><circle cx="12" cy="8" r="2.2" /><path d="M10.9 9.9L5.2 20.5M13.1 9.9l5.7 10.6M7.9 15.5h8.2" /></>,
  align: <><circle cx="12" cy="12" r="8.6" /><path d="M12 1.8v2.4M12 19.8v2.4M1.8 12h2.4M19.8 12h2.4" /><circle cx="12" cy="12" r="1.3" fill="currentColor" stroke="none" /></>,
  guide: <><path d="M2.8 13h3.4l2.2-4.6 3.4 8.2 2.6-5.4 1.6 1.8h5.2" /></>,
  plan: <><rect x="4" y="4.8" width="16" height="15" rx="2" /><path d="M8 9.3h7M8 12.3h8M8 15.3h4.5" /></>,
  power: <><path d="M16.4 5.6a8 8 0 11-8.8 0M12 2.6v8.2" /></>,
  settings: <><path d="M4 8h16M4 16h16" /><circle cx="9.5" cy="8" r="2.1" /><circle cx="14.5" cy="16" r="2.1" /></>,
  monitor: <><rect x="2.8" y="5.2" width="18.4" height="12" rx="1.5" /><path d="M12 17.2v4M8.5 21.2h7" /></>,
  atlas: <><circle cx="12" cy="12" r="8.8" /><path d="M8 9.6l6-3M14 6.6l1.6 8.2M8 9.6l7.6 5.2" /><circle cx="8" cy="9.6" r="1.2" fill="currentColor" stroke="none" /><circle cx="14" cy="6.6" r="1.2" fill="currentColor" stroke="none" /><circle cx="15.6" cy="14.8" r="1.2" fill="currentColor" stroke="none" /></>,
  frame: <><path d="M4 7V5a1 1 0 011-1h2M17 4h2a1 1 0 011 1v2M20 17v2a1 1 0 01-1 1h-2M7 20H5a1 1 0 01-1-1v-2" /><rect x="8.2" y="8.2" width="7.6" height="7.6" rx="1" /></>,
  play: <><path d="M7.5 4.8v14.4L19.3 12z" /></>,
  stop: <><rect x="6.4" y="6.4" width="11.2" height="11.2" rx="1.2" /></>,
  pause: <><path d="M9 5.2v13.6M15 5.2v13.6" /></>,
  refresh: <><path d="M20.8 12a8.8 8.8 0 11-2.6-6.2" /><path d="M20.8 3.2v5.2h-5.2" /></>,
  bridge: <><path d="M6.5 6v11.5M17.5 6v11.5M2.5 17.5h19" /><path d="M2.5 10.5l4-3c2 4.5 9 4.5 11 0l4 3" /><path d="M12 10.9v6.6" /></>,
  link: <><path d="M9.8 14.2a3.6 3.6 0 005.1 0l2.5-2.5a3.6 3.6 0 00-5.1-5.1L11 7.9" /><path d="M14.2 9.8a3.6 3.6 0 00-5.1 0l-2.5 2.5a3.6 3.6 0 005.1 5.1l1.3-1.3" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4l1.4-1.4M17 7l1.4-1.4" /></>,
  moon: <><path d="M20.6 13.4A8.8 8.8 0 1110.6 3.4a7 7 0 0010 10z" /></>,
  brightness: <><circle cx="12" cy="12" r="8.4" /><path d="M12 3.6a8.4 8.4 0 010 16.8z" fill="currentColor" stroke="none" /></>,
  lock: <><rect x="5" y="11" width="14" height="10" rx="1.5" /><path d="M8 11V7a4 4 0 118 0v4" /><circle cx="12" cy="16" r="1.3" fill="currentColor" stroke="none" /></>,
  unlock: <><rect x="5" y="11" width="14" height="10" rx="1.5" /><path d="M8 11V7a4 4 0 117.5-2" /><circle cx="12" cy="16" r="1.3" fill="currentColor" stroke="none" /></>,
  user: <><circle cx="12" cy="8" r="3.8" /><path d="M4.5 21.2c.6-4 3.7-6.6 7.5-6.6s6.9 2.6 7.5 6.6" /></>,
  logout: <><path d="M9 21H5.5a2 2 0 01-2-2V5a2 2 0 012-2H9" /><path d="M16 17l5-5-5-5M21 12H9.5" /></>,
  eye: <><path d="M2.5 12s3.4-7.2 9.5-7.2S21.5 12 21.5 12s-3.4 7.2-9.5 7.2S2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></>,
  alert: <><path d="M12 3L2.8 20h18.4L12 3z" /><path d="M12 10v4" /><circle cx="12" cy="16.8" r="1" fill="currentColor" stroke="none" /></>,
  check: <><path d="M20 6.5L9.5 17 4 11.5" /></>,
  info: <><circle cx="12" cy="12" r="8.8" /><path d="M12 16.5V11" /><circle cx="12" cy="7.8" r="1" fill="currentColor" stroke="none" /></>,
  clock: <><circle cx="12" cy="12" r="8.8" /><path d="M12 6.8V12l3.4 2.2" /></>,
  x: <><path d="M17.5 6.5l-11 11M6.5 6.5l11 11" /></>,
  grid: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
  key: <><circle cx="8" cy="16" r="3.8" /><path d="M10.7 13.3L19.5 4.5M16 8l3 3M13.5 10.5l2 2" /></>,
  shield: <><path d="M12 21.5s7.5-3.8 7.5-9.5V5.3L12 2.5 4.5 5.3V12c0 5.7 7.5 9.5 7.5 9.5z" /></>,
  plus: <><path d="M12 5v14M5 12h14" /></>,
  trash: <><path d="M3.5 6.5h17M8.5 6.5V4.7a1.7 1.7 0 011.7-1.7h3.6a1.7 1.7 0 011.7 1.7v1.8M10 11v6M14 11v6M5.3 6.5l1 12.7a2 2 0 002 1.8h7.4a2 2 0 002-1.8l1-12.7" /></>,
  download: <><path d="M12 3.5v11.5M7 10.5l5 5 5-5M4.5 19.5h15" /></>,
  upload: <><path d="M12 15.5V4M7 9l5-5 5 5M4.5 19.5h15" /></>,
  gallery: <><path d="M7.2 3.6H19a1.8 1.8 0 011.8 1.8V16" /><rect x="3.2" y="6.8" width="13.6" height="12" rx="1.8" /><circle cx="7" cy="10.6" r="1.1" fill="currentColor" stroke="none" /><path d="M3.4 15.6l3.4-3.2 3.2 3 2.4-2.2 4.2 3.8" /></>,
  "arrow-up": <><path d="M12 19V5M5 12l7-7 7 7" /></>,
  "arrow-down": <><path d="M12 5v14M19 12l-7 7-7-7" /></>,
  "arrow-left": <><path d="M19 12H5M12 19l-7-7 7-7" /></>,
  "arrow-right": <><path d="M5 12h14M12 5l7 7-7 7" /></>,
};

// Optical stroke compensation: small renders get a slightly heavier stroke so
// glyphs don't go wispy at 16px (desktop client), large renders slightly lighter.
function autoStroke(size: number): number {
  if (size <= 16) return 1.7;
  if (size <= 20) return 1.6;
  if (size <= 26) return 1.5;
  return 1.4;
}

export function Icon({ name, size = 18, className = "", strokeWidth, title }: IconProps): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth ?? autoStroke(size)}
      strokeLinecap="round"
      strokeLinejoin="round"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      focusable="false"
      className={`shrink-0 ${className}`}
    >
      {title && <title>{title}</title>}
      {PATHS[name]}
    </svg>
  );
}
