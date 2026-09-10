// UsersSheet.tsx - Settings > USERS > "People" (plan section C.7.3).
//
// UsersPanel is mounted WHOLE and UNEDITED - add/role/enable/reset/delete,
// its two self-harm hold-confirms ("Remove your own admin access?", "Disable
// your own account?"), the delete confirm and its inline (never
// `window.prompt` - breaks night mode) password reset all come along for
// free by not touching the panel.
//
// GATED HERE, not inside the panel: UsersPanel's own mount effect calls
// `listUsers()` (`GET /api/users`) unconditionally, assuming it is only ever
// mounted for an admin. Deep-linking this sheet directly must not make that
// assumption false, so a caller without admin.users never sees the panel
// mount at all (plan C.7.4: "neither issues GET /api/users").
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { EmptyCard, Sheet } from "../../../ui";
import { accessPhrase, useCanAdminUsers } from "../../../../lib/caps";
import UsersPanel from "../../../../components/settings/UsersPanel";

/** The design's people glyph (plan section 2.5's `users` path, pre-authorised
 *  there only for T-SET-1's append to `icons.tsx`). Drawn locally, in the
 *  same 24x24/currentColor/round-cap idiom `NxIcon` uses, rather than waiting
 *  on that append or editing a file this task does not own. */
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
  const canAdmin = useCanAdminUsers();
  return (
    <Sheet
      data-testid="settings-users"
      title="PEOPLE"
      icon={<UsersGlyph size={18} />}
      onBack={nav.back}
    >
      {canAdmin ? (
        <UsersPanel />
      ) : (
        <EmptyCard
          title="PEOPLE LIST HIDDEN"
          hint={`The people list needs ${accessPhrase("admin.users")}.`}
          data-testid="empty-people-sheet"
        />
      )}
    </Sheet>
  );
}
