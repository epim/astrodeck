// UsersSheet.tsx - Settings > USERS > "People" (plan section C.7.3), rebuilt
// for wave R7 (T-R7-11, cutover table section 7).
//
// It now mounts `tuning/people`'s `UsersEditor` instead of
// `components/settings/UsersPanel.tsx`. The legacy panel is untouched and still
// serves `#/classic`.
//
// THE GATE MOVED, AND WITH IT WHAT A NON-ADMIN SEES. `UsersPanel`'s mount
// effect calls `listUsers()` unconditionally - it assumes it is only ever
// mounted for an admin - so this sheet used to protect it by rendering an
// EmptyCard INSTEAD of the panel for anyone else, which hid the whole feature.
// `UsersEditor` owns its own fetch and only issues it with `admin.users`, so
// the sheet no longer has to choose between "leak a 403" and "hide the screen":
// the editor renders for everyone, every control is honest-disabled with the
// reason (ARCHITECTURE.md section 8), and the list region - the one part the
// server genuinely will not hand over - says so and names the capability.
// Nothing is requested from the rig without the capability, which is the
// property the old arrangement was protecting.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { Sheet } from "../../../ui";
import { UsersEditor } from "../tuning/people";

/** The design's people glyph (plan section 2.5's `users` path, pre-authorised
 *  there only for T-SET-1's append to `icons.tsx`). Drawn locally, in the
 *  same 24x24/currentColor/round-cap idiom `NxIcon` uses, rather than waiting
 *  on that append or editing a file this task does not own.
 *
 *  Exported: `UsersScreen.tsx` uses it for the PEOPLE row's tile. */
export function UsersGlyph({ size = 20 }: { size?: number }): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M9 12a4 4 0 1 0 0-8a4 4 0 0 0 0 8M2 21v-1a6 6 0 0 1 6-6h2a6 6 0 0 1 6 6v1M17.5 12.5a3 3 0 1 0 0-6M22 21v-1a5 5 0 0 0-3.6-4.8" />
    </svg>
  );
}

export function UsersSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-users"
      title="PEOPLE"
      icon={<UsersGlyph size={18} />}
      onBack={nav.back}
    >
      <UsersEditor />
    </Sheet>
  );
}
