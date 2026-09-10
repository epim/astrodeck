// UsersEditor.tsx - PEOPLE, rebuilt in the design's vocabulary (wave R7,
// T-R7-11; plan section 3.F4). Replaces `components/settings/UsersPanel.tsx` at
// its one mount inside the new UI, `settings/sheets/UsersSheet.tsx`. The legacy
// file is untouched and still serves `#/classic`.
//
// TWO THINGS CHANGED, AND WHY.
//
// 1. THE FETCH IS GATED, THE SCREEN IS NOT. `UsersPanel`'s mount effect calls
//    `listUsers()` unconditionally - it assumes it is only ever mounted for an
//    admin - so `UsersSheet` used to protect it by rendering an EmptyCard
//    INSTEAD of the panel for anyone else. That hid the whole feature: a viewer
//    could not tell PEOPLE existed, let alone what it needed.
//    ARCHITECTURE.md section 8 asks for the opposite ("nothing is hidden, so a
//    viewer sees the same screen as the operator"). This editor owns its own
//    fetch, so it can do both: the request fires only with `admin.users`, and
//    every control renders honest-disabled with the reason for everyone else.
//    The LIST itself still cannot be shown - the server would refuse it - so
//    that one region says so and names the capability.
//
// 2. NO NATIVE `disabled`. The legacy row greys out the role select, the
//    toggle, RESET and DELETE on `busy` and shows nothing for a non-admin at
//    all. Every one is `lockedReason` + `onExplain` here.
//
// The three `confirmDialog` flows, the inline password reset and the "created
// as <role>" sentence are unchanged in behaviour; see `UserRow.tsx` and
// `AddUserForm.tsx`.

import { useCallback, useEffect, useState, type JSX } from "react";
import { listUsers } from "../../../../../api/backends";
import { useCanAdminUsers } from "../../../../../lib/caps";
import { usePrincipal } from "../../../../../store";
import type { User } from "../../../../../types";
import { useLock } from "../../../../lib/gateHook";
import { ActionButton, Card, EmptyCard, LockNote, Mono } from "../../../../ui";
import { AddUserForm } from "./AddUserForm";
import { Note, Section, Verdict } from "./PeopleSection";
import { UserRow } from "./UserRow";
import {
  PEOPLE_CAP, PEOPLE_LIST_HIDDEN_HINT, PEOPLE_LIST_HIDDEN_TITLE, USERS_ADD, USERS_ADD_CANCEL,
  USERS_EMPTY_HINT, USERS_EMPTY_TITLE, USERS_EYEBROW, USERS_INTRO, USERS_LOADING,
  USERS_LOAD_FAILED, errText,
} from "./peopleModel";

export function UsersEditor(): JSX.Element {
  const me = usePrincipal();
  // The CAPABILITY decides whether to ask the rig for the list; the full lock
  // (which also covers a dead link) decides whether a control may be pressed.
  // Gating the read on the lock would leave an admin staring at an empty list
  // for as long as the socket takes to come up, for no gain: the GET is
  // capability-checked server-side either way.
  const canAdmin = useCanAdminUsers();
  const { lockedReason, onExplain } = useLock({ cap: PEOPLE_CAP });

  const [users, setUsers] = useState<User[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(async () => {
    if (!canAdmin) return;
    try {
      setUsers(await listUsers());
      setErr(null);
    } catch (e) {
      setErr(errText(e, USERS_LOAD_FAILED));
    }
  }, [canAdmin]);

  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <Section
      eyebrow={USERS_EYEBROW}
      data-testid="users-editor"
      action={
        <ActionButton
          kind={adding ? "ghost" : "primary"}
          onPress={() => setAdding((v) => !v)}
          lockedReason={lockedReason}
          onExplain={onExplain}
          data-testid="users-add"
        >
          {adding ? USERS_ADD_CANCEL : USERS_ADD}
        </ActionButton>
      }
    >
      <Note>{USERS_INTRO}</Note>
      <LockNote reason={lockedReason} data-testid="users-locknote" />

      {err && <Verdict tone="bad" data-testid="users-error">{err}</Verdict>}

      {adding && (
        <AddUserForm
          lockedReason={lockedReason}
          onExplain={onExplain}
          onCreated={async () => { setAdding(false); await refresh(); }}
        />
      )}

      {!canAdmin ? (
        <EmptyCard
          title={PEOPLE_LIST_HIDDEN_TITLE}
          hint={PEOPLE_LIST_HIDDEN_HINT}
          data-testid="users-hidden"
        />
      ) : users == null ? (
        <Card data-testid="users-loading"><Mono>{USERS_LOADING}</Mono></Card>
      ) : users.length === 0 ? (
        <EmptyCard title={USERS_EMPTY_TITLE} hint={USERS_EMPTY_HINT} data-testid="users-empty" />
      ) : (
        <Card className="nx-people-list" data-testid="users-list">
          {users.map((u) => (
            <UserRow
              key={u.id}
              user={u}
              isSelf={!!me?.email && me.email === u.email}
              onChanged={refresh}
              onError={setErr}
              lockedReason={lockedReason}
              onExplain={onExplain}
            />
          ))}
        </Card>
      )}
    </Section>
  );
}
