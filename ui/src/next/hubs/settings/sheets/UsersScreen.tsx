// UsersScreen.tsx - Settings > USERS (`#/settings/users`, plan section C.7).
//
// Three stacked sections, `admin.users` gating the last two:
//   1. SIGNED IN - the account identity + relay/loopback notes, always
//      visible (`AccountBody`, shared verbatim with `AccountSheet.tsx` - the
//      route table's "account ... renders inline; the sheet form is the
//      tablet/desktop panel host" for the SAME sheet name).
//   2. SIGN-IN METHODS - a summary row into the `authMethods` sheet for an
//      admin; an EmptyCard carrying the exact reason (verbatim, plan C.7.4)
//      for anyone else, with no request fired either way.
//   3. PEOPLE - the same shape, into the `users` sheet; the non-admin path
//      also keeps the design's own explainer paragraph as this section's
//      footer (plan C.7.4, amended for the real four-role model).
// A ROLES reference block closes the screen: the four `ROLE_DESCRIPTIONS`
// sentences (`lib/caps.ts`), visible to every role - nobody needs a
// capability to be told what the roles mean.
import type { JSX } from "react";
import { Card, EmptyCard, Label, ListRow } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { accessPhrase, ROLE_DESCRIPTIONS, useCanAdminUsers } from "../../../../lib/caps";
import { useAuthMethods } from "../../../../store";
import type { PrincipalRole } from "../../../../types";
import { AccountBody } from "./AccountSheet";
import { UsersGlyph } from "./UsersSheet";

const NOTE_STYLE = {
  margin: "6px 0 0",
  fontSize: 11.5,
  lineHeight: 1.45,
  color: "var(--text-faint)",
} as const;

/** A one-line summary of the enabled sign-in methods for the row's sub-line.
 *  Never fabricated: it reads the SAME `authMethods` slice the panel itself
 *  seeds from, and says "open" honestly rather than guessing a method. */
function methodsSummary(methods: string[] | undefined): string {
  if (!methods || methods.length === 0) return "open - no method enabled";
  return `${methods.join(" + ")} enabled`;
}

const ROLE_ORDER: readonly PrincipalRole[] = ["admin", "operator", "syncer", "viewer"];

export function UsersScreen(): JSX.Element {
  const canAdmin = useCanAdminUsers();
  const authMethods = useAuthMethods();

  return (
    <div data-testid="screen-users">
      <Label>SIGNED IN</Label>
      <AccountBody />

      <Label>SIGN-IN METHODS</Label>
      {canAdmin ? (
        <Card>
          <ListRow
            icon={<NxIcon name="safety" />}
            title="SIGN-IN METHODS"
            sub={methodsSummary(authMethods?.methods)}
            chevron
            onPress={() => nav.sheet("authMethods")}
            data-testid="row-auth-methods"
          />
        </Card>
      ) : (
        <EmptyCard
          title="SIGN-IN METHODS HIDDEN"
          hint={`Sign-in methods need ${accessPhrase("admin.users")}.`}
          data-testid="empty-auth-methods"
        />
      )}

      <Label>PEOPLE</Label>
      {canAdmin ? (
        <Card>
          <ListRow
            icon={<UsersGlyph size={18} />}
            title="PEOPLE"
            sub="roles, access and sign-in for everyone on this rig"
            chevron
            onPress={() => nav.sheet("users")}
            data-testid="row-people"
          />
        </Card>
      ) : (
        <>
          <EmptyCard
            title="PEOPLE LIST HIDDEN"
            hint={`The people list needs ${accessPhrase("admin.users")}.`}
            data-testid="empty-people"
          />
          <p style={NOTE_STYLE} data-testid="users-footer">
            Owner configures the rig and sites. Operators can run and stop
            flows. Viewers see Monitor and the Gallery only - handy for a club
            night. A syncer only copies raw files to another machine.
          </p>
        </>
      )}

      <Label>ROLES</Label>
      <Card>
        {ROLE_ORDER.map((role) => (
          <ListRow
            key={role}
            title={role.toUpperCase()}
            sub={ROLE_DESCRIPTIONS[role]}
            data-testid={`role-${role}`}
          />
        ))}
      </Card>
    </div>
  );
}
