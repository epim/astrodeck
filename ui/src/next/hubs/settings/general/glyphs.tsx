// glyphs.tsx - the five row icons the Settings hub needs that `next/icons.tsx`
// does not carry.
//
// Plan section 2.5 pre-authorised an append to `icons.tsx` for four of these
// (`connection`, `bell`, `gallery`, `setup`, and a `users` path T-SET-3 has
// already drawn locally in `sheets/UsersSheet.tsx`). This task's brief WITHDREW
// that authorisation: `icons.tsx` is shared by six hubs and three tasks are
// writing at once, so a glyph nobody else references is drawn here instead, in
// the same 24x24 / currentColor / round-cap idiom `NxIcon` uses. The `d`
// strings for the first four are the prototype's own
// (`seams/proto/22-settings.html`, `23-first-time-setup.html`); `NamingGlyph`
// is new - the design has no glyph for the file-naming row.
//
// If `icons.tsx` ever grows these names, delete this file and switch the four
// call sites; nothing else imports it.

import type { JSX } from "react";

function Glyph({ d, size }: { d: string; size: number }): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={size <= 16 ? 1.8 : 1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className="nx-icon"
    >
      <path d={d} />
    </svg>
  );
}

/** A rig computer: a box on a stand with an antenna. Prototype's CONNECTION row. */
export function ConnectionGlyph({ size = 20 }: { size?: number }): JSX.Element {
  return <Glyph size={size} d="M4 16a4 4 0 0 1 4-4h8a4 4 0 0 1 4 4v1H4zM12 12V8M8 8h8M9 4h6" />;
}

/** A bell. Prototype's NOTIFICATIONS row. */
export function BellGlyph({ size = 20 }: { size?: number }): JSX.Element {
  return <Glyph size={size} d="M6 8a6 6 0 0 1 12 0v5l2 3H4l2-3zM10 20a2 2 0 0 0 4 0" />;
}

/** A framed picture. Prototype's GALLERY row. */
export function GalleryGlyph({ size = 20 }: { size?: number }): JSX.Element {
  return <Glyph size={size} d="M4 5h16v14H4zM4 15l5-5 4 4 3-3 4 4" />;
}

/** A ticked checklist. Prototype's FIRST-TIME SETUP tile. */
export function SetupGlyph({ size = 20 }: { size?: number }): JSX.Element {
  return (
    <Glyph
      size={size}
      d="M9 11l3 3 8-8M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9"
    />
  );
}

/** A document with a name tag. New: the file-naming row has no design glyph,
 *  and every existing icon that came close already means something else on
 *  this screen (`funnel` is the lens filter, `share` is file sync). */
export function NamingGlyph({ size = 20 }: { size?: number }): JSX.Element {
  return (
    <Glyph
      size={size}
      d="M6 3h7l5 5v13H6zM13 3v5h5M9 13h6M9 17h4"
    />
  );
}
