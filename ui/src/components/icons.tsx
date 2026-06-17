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
  | "sun" | "moon" | "lock" | "unlock" | "frame"
  // account / rbac
  | "user" | "logout" | "eye"
  // status
  | "alert" | "check" | "info" | "x"
  // misc
  | "grid"
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
  rig: <><rect x="4" y="4" width="16" height="6" rx="1" /><rect x="4" y="14" width="16" height="6" rx="1" /><path d="M8 10v4M16 10v4" /></>,
  capture: <><rect x="3" y="7" width="18" height="13" rx="2" /><path d="M8 7l1.5-2h5L16 7" /><circle cx="12" cy="13" r="3" /></>,
  focus: <><path d="M12 4v16" /><path d="M7 5H5v2M17 5h2v2M7 19H5v-2M17 19h2v-2" /></>,
  mount: <><path d="M5 20l7-9 7 9" /><path d="M12 11V4" /><circle cx="12" cy="3.5" r="1.5" /><path d="M9 20h6" /></>,
  align: <><circle cx="12" cy="12" r="7" /><path d="M12 3v4M12 17v4M3 12h4M17 12h4" /><circle cx="12" cy="12" r="1" /></>,
  guide: <><rect x="3" y="4" width="18" height="16" rx="1" /><path d="M5 15l4-5 3 3 4-6 3 4" /></>,
  plan: <><path d="M5 5h14M5 12h14M5 19h9" /><circle cx="20" cy="19" r="1.5" /></>,
  power: <><path d="M12 3v9" /><path d="M7 6a8 8 0 1010 0" /></>,
  settings: <><circle cx="12" cy="12" r="3" /><path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" /></>,
  monitor: <><rect x="3" y="4" width="18" height="12" rx="1" /><path d="M8 20h8M12 16v4" /><path d="M6 11l3-3 2 2 4-4" /></>,
  // atlas — a telescope tube on a tripod, reads as "point at the sky" (nav glyph)
  atlas: <><path d="M3 14l11.5-4 1.6 4.4L4.6 18.4z" /><path d="M14.5 10l2.6-4.6 3.5 1.6L18 11.6" /><path d="M9 16.5L7 22M11 17.5L13 22M6 22h9" /></>,
  // frame — a viewfinder rectangle with corner ticks (per-row Frame action, NOT align)
  frame: <><path d="M4 8V5a1 1 0 011-1h3M16 4h3a1 1 0 011 1v3M20 16v3a1 1 0 01-1 1h-3M8 20H5a1 1 0 01-1-1v-3" /><rect x="9" y="9" width="6" height="6" rx="0.5" /></>,
  play: <><path d="M7 5l11 7-11 7z" /></>,
  stop: <><rect x="6" y="6" width="12" height="12" rx="1" /></>,
  pause: <><rect x="7" y="5" width="3" height="14" rx="1" /><rect x="14" y="5" width="3" height="14" rx="1" /></>,
  refresh: <><path d="M20 11a8 8 0 10-2 6" /><path d="M20 4v6h-6" /></>,
  bridge: <><path d="M4 12h16" /><path d="M7 12V8M17 12V8" /><path d="M4 16h16" /></>,
  link: <><path d="M9 12a3 3 0 013-3h2a3 3 0 010 6h-2" /><path d="M15 12a3 3 0 01-3 3h-2a3 3 0 010-6h2" /></>,
  sun: <><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></>,
  moon: <><path d="M20 14a8 8 0 11-9-11 6 6 0 009 11z" /></>,
  lock: <><rect x="5" y="11" width="14" height="9" rx="1" /><path d="M8 11V8a4 4 0 018 0v3" /></>,
  unlock: <><rect x="5" y="11" width="14" height="9" rx="1" /><path d="M8 11V8a4 4 0 017-2.6" /></>,
  // user — head + shoulders (account / signed-in identity)
  user: <><circle cx="12" cy="8" r="3.5" /><path d="M5 20a7 7 0 0114 0" /></>,
  // logout — door + out-arrow (sign out)
  logout: <><path d="M14 4h4a1 1 0 011 1v14a1 1 0 01-1 1h-4" /><path d="M10 12H3M6 8l-4 4 4 4" /></>,
  // eye — view-only indicator (read-only / viewer role)
  eye: <><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7z" /><circle cx="12" cy="12" r="3" /></>,
  alert: <><path d="M12 4l9 16H3z" /><path d="M12 10v4" /><circle cx="12" cy="17" r="0.8" /></>,
  check: <><path d="M5 13l4 4L19 7" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><circle cx="12" cy="8" r="0.8" /></>,
  x: <><path d="M6 6l12 12M18 6L6 18" /></>,
  // grid — 2x2 cells (mosaic / total-field glyph), Lucide-style
  grid: <><rect x="4" y="4" width="7" height="7" rx="1" /><rect x="13" y="4" width="7" height="7" rx="1" /><rect x="4" y="13" width="7" height="7" rx="1" /><rect x="13" y="13" width="7" height="7" rx="1" /></>,
  "arrow-up": <><path d="M12 19V5M6 11l6-6 6 6" /></>,
  "arrow-down": <><path d="M12 5v14M6 13l6 6 6-6" /></>,
  "arrow-left": <><path d="M19 12H5M11 6l-6 6 6 6" /></>,
  "arrow-right": <><path d="M5 12h14M13 6l6 6-6 6" /></>,
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
