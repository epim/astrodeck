// glyphs.tsx - the four glyphs this area needs and `next/icons.tsx` does not
// carry (wave R7, T-R7-11).
//
// Drawn here rather than appended to `icons.tsx`, which no R7 task owns; same
// 24x24 / currentColor / round-cap idiom `NxIcon` uses, so they sit beside the
// shared set without looking borrowed from somewhere else. `UsersSheet.tsx`'s
// own `UsersGlyph` made the same call for the same reason.

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

/** One person - a row that is not an admin and is enabled. */
export function PersonGlyph({ size = 18 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M12 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8M4 21v-1a6 6 0 0 1 6-6h4a6 6 0 0 1 6 6v1" />;
}

/** A shield - an admin row, and the sign-in-methods heading. */
export function ShieldGlyph({ size = 18 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M12 3l7 3v5c0 4.4-2.9 8.4-7 10c-4.1-1.6-7-5.6-7-10V6z" />;
}

/** A key - reset password. */
export function KeyGlyph({ size = 16 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M15.5 3.5a5 5 0 1 0 3.2 8.8L21 14.5V18h-3.5v-2H15v-2.3a5 5 0 0 0 .5-10.2M14 8.5h.01" />;
}

/** A bin - delete user. */
export function TrashGlyph({ size = 16 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13M10 11v6M14 11v6" />;
}

/** A door with an arrow - sign out. */
export function SignOutGlyph({ size = 16 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M15 4h3a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-3M10 8l-4 4l4 4M6 12h9" />;
}

/** An eye - a viewer row, and the read-only session card. */
export function EyeGlyph({ size = 18 }: { size?: number }): JSX.Element {
  return <Svg size={size} d="M2 12s3.6-6 10-6s10 6 10 6s-3.6 6-10 6s-10-6-10-6m10 2.5a2.5 2.5 0 1 0 0-5a2.5 2.5 0 0 0 0 5" />;
}
