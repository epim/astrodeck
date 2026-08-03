// RECONSTRUCTED (lane 1A owns this file per Batch-1 orchestrator override) —
// restored to a working inline-SVG set after an isolation type-check overwrote
// the original. 1A's canonical version takes precedence at merge.
// Inline SVG, currentColor, 24x24 viewBox, stroke 1.5, round caps/joins.
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
  // gallery (2026-08-03): a STACK of pictures, deliberately not `frame` (crop
  // corners — "the sensor's field") and not `grid` (four blank squares — the
  // generic layout glyph, already the More-sheet idea). The nav rail is read at
  // a glance in the dark; two nav entries whose glyphs differ only in whether
  // the squares overlap are two entries nobody can tell apart.
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
  rig: <><circle cx="12" cy="12" r="3" /><circle cx="5" cy="19" r="2" /><circle cx="19" cy="5" r="2" /><path d="M10 14l-3 3M14 10l3-3M12 15v4" /></>,
  capture: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4l1.4-1.4M17 7l1.4-1.4" /></>,
  focus: <><circle cx="12" cy="12" r="10" /><path d="M12 6a6 6 0 010 12" /><path d="M12 6a6 6 0 000 12" /><path d="M6 12h12" /></>,
  mount: <><path d="M12 2v6" /><path d="M7 8h10" /><path d="M12 8l-5 10h10z" /><circle cx="12" cy="8" r="2" /></>,
  align: <><circle cx="12" cy="12" r="9" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /><circle cx="12" cy="12" r="1.5" /></>,
  guide: <><path d="M2 12h20M4 12l4-5 4 10 4-5h4" /></>,
  plan: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M7 9h6M7 13h10M7 17h4" /></>,
  power: <><circle cx="12" cy="12" r="9" /><path d="M12 3v9" /></>,
  settings: <><path d="M4 8h16M4 16h16" /><circle cx="10" cy="8" r="2" /><circle cx="16" cy="16" r="2" /></>,
  monitor: <><rect x="2" y="6" width="20" height="12" rx="1" /><path d="M8 22h8M12 18v4" /></>,
  atlas: <><circle cx="12" cy="12" r="10" /><circle cx="8" cy="10" r="1" /><circle cx="14" cy="7" r="1" /><circle cx="16" cy="15" r="1" /><path d="M8 10l6-3 2 8-8-5" /></>,
  frame: <><path d="M6 4H4v2M18 4h2v2M4 18v2h2M20 18v2h-2" /><rect x="8" y="8" width="8" height="8" rx="1" /></>,
  play: <><polygon points="6 4 20 12 6 20" /></>,
  stop: <><rect x="6" y="6" width="12" height="12" /></>,
  pause: <><rect x="6" y="5" width="4" height="14" /><rect x="14" y="5" width="4" height="14" /></>,
  refresh: <><path d="M21 12a9 9 0 10-2.6 6.3" /><path d="M21 11v7h-7" /></>,
  bridge: <><path d="M4 10h16M4 14h16M4 10a4 4 0 014-4h8a4 4 0 014 4v8a4 4 0 01-4 4H8a4 4 0 01-4-4v-8z" /></>,
  link: <><path d="M10 14a3.5 3.5 0 005-5l1.5-1.5a3.5 3.5 0 00-5-5L10 4" /><path d="M14 10a3.5 3.5 0 00-5 5l-1.5 1.5a3.5 3.5 0 005 5l1.5-1.5" /><path d="M9 15l6-6" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4l1.4-1.4M17 7l1.4-1.4" /></>,
  moon: <><path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" /></>,
  // brightness/dimmer — the universal half-filled "contrast" glyph, deliberately
  // NOT a sun: in night mode the day/night toggle shows a sun, so the dimmer must
  // read as a distinct control (fixes the two-identical-suns header confusion).
  brightness: <><circle cx="12" cy="12" r="8" /><path d="M12 4a8 8 0 010 16z" fill="currentColor" stroke="none" /><path d="M12 3v2M12 19v2M3 12h2M19 12h2" /></>,
  lock: <><rect x="5" y="11" width="14" height="10" rx="1" /><path d="M8 11V7a4 4 0 118 0v4" /><circle cx="12" cy="16" r="1.5" /></>,
  unlock: <><rect x="5" y="11" width="14" height="10" rx="1" /><path d="M8 11V7a4 4 0 117.5-2" /><circle cx="12" cy="16" r="1.5" /></>,
  user: <><circle cx="12" cy="8" r="4" /><path d="M4 22c0-4.4 3.6-8 8-8s8 3.6 8 8" /></>,
  logout: <><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4" /><path d="M16 17l5-5-5-5" /><path d="M21 12H9" /></>,
  eye: <><path d="M2 12s3.5-8 10-8 10 8 10 8-3.5 8-10 8-10-8-10-8z" /><circle cx="12" cy="12" r="3" /></>,
  alert: <><path d="M12 2L2 21h20L12 2z" /><path d="M12 9v4" /><circle cx="12" cy="17" r="1" /></>,
  check: <><path d="M20 6L9 17l-5-5" /></>,
  info: <><circle cx="12" cy="12" r="10" /><path d="M12 16v-4" /><circle cx="12" cy="8" r="1" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  x: <><path d="M18 6L6 18M6 6l12 12" /></>,
  grid: <><rect x="4" y="4" width="6" height="6" rx="1" /><rect x="14" y="4" width="6" height="6" rx="1" /><rect x="4" y="14" width="6" height="6" rx="1" /><rect x="14" y="14" width="6" height="6" rx="1" /></>,
  key: <><circle cx="8" cy="16" r="4" /><path d="M10.5 13.5L19 5v4h4v4h-4v-4l-2 2" /></>,
  shield: <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></>,
  plus: <><path d="M12 5v14M5 12h14" /></>,
  trash: <><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M10 11v6M14 11v6M5 6l1 14a2 2 0 002 2h8a2 2 0 002-2l1-14" /></>,
  download: <><path d="M12 3v12M7 10l5 5 5-5" /><path d="M4 19h16" /></>,
  upload: <><path d="M12 21V9M7 14l5-5 5 5" /><path d="M4 19h16" /></>,
  gallery: <><rect x="2.5" y="6.5" width="14" height="12" rx="1.5" /><path d="M7 3.5h12a1.5 1.5 0 011.5 1.5v10" /><circle cx="6.8" cy="10.5" r="1.1" /><path d="M2.5 15.5l3.8-3.6 3.4 3.2 2.6-2.4 4.2 3.8" /></>,
  "arrow-up": <><path d="M12 19V5M5 12l7-7 7 7" /></>,
  "arrow-down": <><path d="M12 5v14M19 12l-7 7-7-7" /></>,
  "arrow-left": <><path d="M19 12H5M12 19l-7-7 7-7" /></>,
  "arrow-right": <><path d="M5 12h14M12 5l7 7-7 7" /></>,
};

export function Icon({ name, size = 18, className = "", strokeWidth = 1.5, title }: IconProps): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
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
