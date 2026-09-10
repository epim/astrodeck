// AuthMethodsSheet.tsx - Settings > USERS > "Sign-in methods" (plan section
// C.7.2).
//
// AuthMethodPanel is mounted WHOLE and UNEDITED - the draft-until-Save model,
// the "Secure this server" guided card (step 2 stays locked until step 1's
// admin is ENABLED, not merely created), the open/lockout warnings and the
// sticky post-save toast all come along for free by not touching the panel.
//
// GATED HERE, not inside the panel: AuthMethodPanel calls `listUsers()`
// (`GET /api/users`, admin.users-gated) as soon as auth is open-LAN with no
// method enabled, with no capability check of its own - it assumes
// SettingsView mounted it only for an admin. This sheet is reachable by a
// direct deep-link regardless of what row (if any) led here, so it makes
// that assumption safe by never mounting the panel for anyone who lacks
// admin.users (plan C.7.4: "neither issues GET /api/users").
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { EmptyCard, Sheet } from "../../../ui";
import { accessPhrase, useCanAdminUsers } from "../../../../lib/caps";
import AuthMethodPanel from "../../../../components/settings/AuthMethodPanel";

export function AuthMethodsSheet(_p: SheetProps): JSX.Element {
  const canAdmin = useCanAdminUsers();
  return (
    <Sheet
      data-testid="settings-authMethods"
      title="SIGN-IN METHODS"
      icon={<NxIcon name="safety" />}
      onBack={nav.back}
    >
      {canAdmin ? (
        <AuthMethodPanel />
      ) : (
        <EmptyCard
          title="SIGN-IN METHODS HIDDEN"
          hint={`Sign-in methods need ${accessPhrase("admin.users")}.`}
          data-testid="empty-auth-methods-sheet"
        />
      )}
    </Sheet>
  );
}
