// glyphs.tsx - the five drawings the Sky hub needs that `next/icons.tsx` cannot
// carry.
//
// `NxIcon` renders exactly ONE `<path>`, which is the right shape for the other
// eighty glyphs in the app. These five are not single paths in the design: the
// toolbar's mode button is a phone body plus a lens plus four bracket corners,
// the FRAME glyph is a bracket outline plus a dashed inner rectangle, and the
// gyro is a handset inside two gimbal arcs. Flattening them into one `d` would
// lose the dashes and the partial opacities that make them readable at 20 px.
//
// Every path here is lifted verbatim from `proto/01-sky-finder.html`, so the
// toolbar in the screenshot and the toolbar in the app are the same drawing.
// They live under `cards/` rather than in `next/icons.tsx` because that file
// belongs to another task; if the icon set later grows a multi-element form,
// these five move there and this file goes away.

import type { JSX } from "react";

export type SkyGlyphName = "arcamera" | "map" | "frame" | "gyro" | "plan";

const COMMON = {
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function SkyGlyph({ name, size = 20 }: { name: SkyGlyphName; size?: number }): JSX.Element {
  const sw = size <= 16 ? 1.8 : 1.6;
  return (
    <svg width={size} height={size} {...COMMON} strokeWidth={sw} aria-hidden="true" focusable="false" data-glyph={name}>
      {name === "arcamera" && (
        <>
          <rect x="7" y="2.5" width="10" height="19" rx="2" />
          <circle cx="12" cy="11" r="3.2" />
          <circle cx="12" cy="11" r="1" fill="currentColor" />
          <path d="M9.5 5.5h5" />
          <path
            d="M2.5 9.5v-3a2 2 0 0 1 2-2h1M2.5 14.5v3a2 2 0 0 0 2 2h1M21.5 9.5v-3a2 2 0 0 0-2-2h-1M21.5 14.5v3a2 2 0 0 1-2 2h-1"
            opacity=".6"
          />
        </>
      )}
      {name === "map" && (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />
        </>
      )}
      {name === "frame" && (
        <>
          <path d="M4 9V6a2 2 0 0 1 2-2h3M15 4h3a2 2 0 0 1 2 2v3M20 15v3a2 2 0 0 1-2 2h-3M9 20H6a2 2 0 0 1-2-2v-3" />
          <rect x="8" y="9" width="8" height="6" rx=".5" strokeDasharray="2 2" />
        </>
      )}
      {name === "gyro" && (
        <>
          <rect x="8.5" y="4" width="7" height="16" rx="1.6" />
          <path d="M4 8.5C2.6 10 2.6 14 4 15.5M20 8.5c1.4 1.5 1.4 5.5 0 7" />
          <path d="M4 15.5l-1.4-.6M4 15.5l.2-1.5M20 8.5l1.4.6M20 8.5l-.2 1.5" />
          <path d="M8.5 1.6C10 .6 14 .6 15.5 1.6M15.5 22.4c-1.5 1-5.5 1-7 0" opacity=".7" />
        </>
      )}
      {name === "plan" && <path d="M4 6h10M4 12h10M4 18h7M17 9v8M13 13h8" strokeWidth={1.7} />}
    </svg>
  );
}
