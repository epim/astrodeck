// glyphs.tsx - the three glyphs this area needs and `next/icons.tsx` does not
// carry (wave R7, T-R7-17).
//
// Drawn here rather than appended to `icons.tsx`, which no R7 task owns; same
// 24x24 / currentColor / round-cap idiom `NxIcon` uses, so they sit beside the
// shared set without looking borrowed from somewhere else. The PEOPLE area's
// `glyphs.tsx` made the same call for the same reason.

import type { JSX } from "react";

function Svg({ size, d }: { size: number; d: string }): JSX.Element {
  return (
    <svg
      width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" focusable="false"
    >
      <path d={d} />
    </svg>
  );
}

/** A bin - delete this profile. Glyph AND the word DELETE, never the glyph
 *  alone: meaning is not carried by an icon on the one irreversible row. */
export function TrashGlyph({ size = 16 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6" />;
}

/** An arrow into a tray - import a profile file. The mirror of `NxIcon`'s
 *  `download`, which this area uses unchanged for EXPORT. */
export function UploadGlyph({ size = 16 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M12 15V3M6 8l6-6 6 6M4 21h16" />;
}

/** A pencil - rename in place. */
export function PencilGlyph({ size = 16 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M4 20h4L20 8l-4-4L4 16zM14.5 5.5l4 4" />;
}
