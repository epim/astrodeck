// UsersScreen.tsx - Settings > USERS (`#/settings/users`, plan section C.7).
//
// Three stacked sections, `admin.users` gating the last two:
//   1. SIGNED IN - the account identity + relay/loopback notes, always
//      visible (`AccountBody`, shared verbatim with `AccountSheet.tsx` - the
//      route table's "account ... renders inline; the sheet form is the
//      tablet/desktop panel host" for the SAME sheet name).
//   2. SIGN-IN METHODS - a summary row into the `authMethods` sheet.
//   3. PEOPLE - the same shape, into the `users` sheet.
// A ROLES reference block closes the screen: the four `ROLE_DESCRIPTIONS`
// sentences (`lib/caps.ts`), visible to every role - nobody needs a
// capability to be told what the roles mean.
//
// THE TWO ROWS RENDER FOR EVERY ROLE (T-R7-21a item 18). They used to be
// replaced by `empty-auth-methods` / `empty-people` cards for a non-admin,
// which is the "a viewer sees a different screen" shape ARCHITECTURE section 8
// exists to forbid - and it disagreed with the two SHEETS behind these rows,
// both of which already render in full and honest-lock their own controls (see
// `UsersSheet.tsx`'s header for why that was the right call there). The rows
// are now always drawn, dimmed with `aria-disabled`, and a press states the
// reason rather than doing nothing.
//
// WHAT A LOCKED ROW MUST NOT DO IS GUESS. `methodsSummary` reads the
// `authMethods` slice, and a reader without the capability never issued the
// request that fills it - so an unfilled slice would print "open - no method
// enabled" and tell a viewer this rig accepts anyone. The sub-line says the
// configuration was not read instead.
//
// THE LOCK IS `canAdmin`, NOT `useLock`. `lockReason` ranks a dropped link
// above a missing capability, and these two rows open a LOCAL sheet: an admin
// on a flapping link must still be able to open PEOPLE.
import type { JSX } from "react";
import { Card, Label, ListRow } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { accessPhrase, ROLE_DESCRIPTIONS, useCanAdminUsers } from "../../../../lib/caps";
import { PRINCIPAL_ROLES } from "../tuning/people/peopleModel";
import { useAuthMethods, useStore } from "../../../../store";
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

/** The four roles, most privileged first. DERIVED, not typed out: a hand-written
 *  list is a second copy of the server's role table, and the disagreement is
 *  silent - a role added to `lib/caps.ts` would simply never appear in this
 *  reference block, so the one screen that explains what the words mean would
 *  quietly stop explaining one of them. `PRINCIPAL_ROLES` is
 *  `Object.keys(ROLE_DESCRIPTIONS)`, whose `Record<PrincipalRole, string>` type
 *  makes the compiler refuse a role that reaches `PrincipalRole` without
 *  reaching it. That order is ascending privilege (the pickers want it that
 *  way), and this block reads best the other way round, so it is reversed
 *  rather than re-listed. `peopleModel` is a constants-and-decisions module -
 *  no React, no component, no stylesheet - so importing one array from it does
 *  not pull the PEOPLE editors into this screen's chunk. */
const ROLE_ORDER: readonly PrincipalRole[] = [...PRINCIPAL_ROLES].reverse();

/** The one sentence both locked rows state. The same words the people editor
 *  behind them uses (`tuning/people`'s `PEOPLE_LOCK_SENTENCE`), spelled here
 *  rather than imported so this screen does not pull that editor's module
 *  graph in for a string. */
export const USERS_LOCK_SENTENCE =
  `Managing people and sign-in needs ${accessPhrase("admin.users")}.`;

/** What the SIGN-IN METHODS row says instead of a summary it cannot honestly
 *  make: with no `admin.users` nothing was ever requested, so an empty slice
 *  says nothing at all about how this rig signs people in. */
export const METHODS_NOT_READ_SUB =
  `not read from the rig - reading it needs ${accessPhrase("admin.users")}`;

export function UsersScreen(): JSX.Element {
  const canAdmin = useCanAdminUsers();
  const authMethods = useAuthMethods();
  const lockedReason = canAdmin ? null : USERS_LOCK_SENTENCE;
  const onExplain = (reason: string): void => {
    useStore.getState().enqueueToast({ level: "warning", title: reason });
  };

  return (
    <div data-testid="screen-users">
      <Label>SIGNED IN</Label>
      <AccountBody />

      <Label>SIGN-IN METHODS</Label>
      <Card>
        <ListRow
          icon={<NxIcon name="safety" />}
          title="SIGN-IN METHODS"
          sub={canAdmin ? methodsSummary(authMethods?.methods) : METHODS_NOT_READ_SUB}
          chevron
          onPress={() => nav.sheet("authMethods")}
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid="row-auth-methods"
        />
      </Card>

      <Label>PEOPLE</Label>
      <Card>
        <ListRow
          icon={<UsersGlyph size={18} />}
          title="PEOPLE"
          sub="roles, access and sign-in for everyone on this rig"
          chevron
          onPress={() => nav.sheet("users")}
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid="row-people"
        />
      </Card>
      {!canAdmin && (
        <p style={NOTE_STYLE} data-testid="users-footer">
          The ROLES list below says what each of those four words means on this
          rig; yours is on the SIGNED IN line above.
        </p>
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
