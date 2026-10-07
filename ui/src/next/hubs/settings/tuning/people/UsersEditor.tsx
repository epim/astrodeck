// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
//
// OVER THE RELAY (#685). `/api/users` used to be refused outright on a tunnelled
// session, so this editor showed a LAN-only card instead of the list. The rig
// now answers the list read, and four changes (add a Google-only viewer or
// operator, move someone between viewer and operator or enable/disable them,
// delete a non-admin) behind a sign-in under five minutes old. So over the relay
// the list loads and the controls are armed; what the editor adds is the
// `StepUpCard`: it says what the five-minute rule is for and what stays on the
// LAN, and when the rig refuses a change for want of a recent sign-in
// (`step_up_required`) it opens a SIGN IN AGAIN form - Google by its full-page
// redirect, a local account in place.
//
// WHAT STAYS ON THE LAN IS LOCKED, NOT DISCOVERED (#734, #731, #761). The rig
// refuses a password reset, any role but viewer and operator, ANY change to or
// deletion of an administrator, and a create that carries a password, over the
// relay (403 `local_only`), so `UserRow` and `AddUserForm` take a second reason
// beside the group lock, `lanOnlyReason`, fed from here with the LAN sentence on
// the relay and null on the LAN. It locks RESET, the admin and syncer role
// options, an administrator's whole row (role, enabled, DELETE) and the add
// form's PASSWORD method, and nothing else; every other control stays armed.
//
// AND THE CHANGE IS NOT LOST TO THE REFUSAL. A row or the add form that is
// refused for want of a recent sign-in hands this editor a way to send the same
// change again (`onStepUp`). It is held (one at a time, the latest) and sent
// when a PASSWORD sign-in lands (`phase` becomes `fresh`). Google leaves the
// page, so nothing is held across it, and the card says to repeat the change.
//
// GOOGLE ASKS TO COME BACK HERE (#733). The rig's callback used to land on the
// home screen, so the person reopened Settings and People before they could
// repeat anything. SIGN IN WITH GOOGLE now goes to `/auth/login?return=` with
// the People route (`PEOPLE_RETURN`, a bare `#/...` fragment: no scheme, host
// or path, so the rig can only append it to a base it chose itself). The rig
// half, reading `return` from the signed pre-auth cookie and refusing anything
// that is not such a fragment, is in `auth/routes.py` (wave 16 integration),
// so `STEP_UP_GOOGLE_NOTE` says the callback lands back on People.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";
import { listUsers } from "../../../../../api/backends";
import { useCanAdminUsers } from "../../../../../lib/caps";
import { usePrincipal } from "../../../../../store";
import type { User } from "../../../../../types";
import { LOCAL_ONLY_REASON } from "../../../../lib/gate";
import {
  googleLoginHref, useLock, useOnRelay, useStepUp, type UseStepUpResult,
} from "../../../../lib/gateHook";
import { ActionButton, Card, EmptyCard, Field, LockNote, Mono, TextInput } from "../../../../ui";
import { AddUserForm } from "./AddUserForm";
import { Note, Section, Verdict } from "./PeopleSection";
import { UserRow } from "./UserRow";
import {
  PEOPLE_CAP, PEOPLE_LIST_HIDDEN_HINT, PEOPLE_LIST_HIDDEN_TITLE, PEOPLE_RELAY_RULE,
  PEOPLE_RELAY_SCOPE, PEOPLE_RELAY_TITLE, STEP_UP_BUSY, STEP_UP_FAILED, STEP_UP_FRESH_HINT,
  STEP_UP_GOOGLE_NOTE, STEP_UP_OPEN, STEP_UP_PASSWORD, STEP_UP_RATE_LIMITED,
  STEP_UP_REQUIRED_HINT, STEP_UP_RETRY_HINT, STEP_UP_SUBMIT, STEP_UP_TITLE,
  STEP_UP_USERNAME, USERS_ADD,
  USERS_ADD_CANCEL, USERS_EMPTY_HINT, USERS_EMPTY_TITLE, USERS_EYEBROW, USERS_INTRO,
  USERS_LOADING, USERS_LOAD_FAILED, errText, isStepUpMessage, SIGN_IN_GOOGLE,
} from "./peopleModel";

/** Where a Google sign-in started from PEOPLE asks to come back to: Settings,
 *  the USERS sub-nav, the `users` sheet that holds this editor. A bare hash
 *  fragment on purpose, and the whole of what the rig accepts as a return
 *  (`[A-Za-z0-9/_-]` after `#/`): nothing here can name another origin.
 *  `w16PeopleGoogleReturn.test.tsx` parses it with the router, so a renamed hub
 *  or sheet fails there. */
export const PEOPLE_RETURN = "#/settings/users/users";

/** The address SIGN IN WITH GOOGLE sends the browser to: the rig's login route,
 *  under the relay's mount prefix when there is one (`u`), with the People
 *  route as `return`. Percent-encoded, because a `#` in a query string is not
 *  part of the query. */
export function googleStepUpHref(): string {
  return googleLoginHref(PEOPLE_RETURN);
}

/** Full-page redirect to the Google sign-in, as `useStepUp().signInGoogle` does,
 *  but asking to be brought back to PEOPLE. */
function signInGoogleToPeople(): void {
  window.location.href = googleStepUpHref();
}

export function UsersEditor(): JSX.Element {
  const me = usePrincipal();
  // The CAPABILITY decides whether to ask the rig for the list; the full lock
  // (which also covers a dead link) decides whether a control may be pressed.
  // Gating the read on the lock would leave an admin staring at an empty list
  // for as long as the socket takes to come up, for no gain: the GET is
  // capability-checked server-side either way.
  const canAdmin = useCanAdminUsers();
  // The bare fact as well as the lock: over the relay the people routes are
  // open only in a narrowed form behind a recent sign-in, and the card that says
  // so is shown on that origin only.
  const onRelay = useOnRelay();
  // No `needsLan`: the list read and the four changes this screen makes are open
  // over the relay (#685). The ones that are not (password reset, an admin) are
  // refused by the rig with a sentence that names the rule; see the header.
  const { lockedReason, onExplain } = useLock({ cap: PEOPLE_CAP });
  const stepUp = useStepUp();
  const { markRequired } = stepUp;
  // The LAN sentence for the controls the rig refuses over the relay, null on
  // the LAN. Not `useLock({ needsLan })`: that would also fold the capability
  // sentence in, and the group lock above already carries that.
  const lanOnlyReason = onRelay ? LOCAL_ONLY_REASON : null;

  const [users, setUsers] = useState<User[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  // The change the rig last refused for want of a recent sign-in, sent again
  // when a password sign-in lands. A ref for the action (nothing renders from
  // it) and a flag for the card's wording.
  const heldRetry = useRef<(() => void) | null>(null);
  const [holding, setHolding] = useState(false);
  // Also raises the SIGN IN AGAIN form: a row reports its refusal as a sentence
  // that `report` below recognises, but the add form keeps its own error and
  // reports nothing upward, so without this a refused create left the form
  // closed and the person with nowhere to sign in.
  const hold = useCallback((retry: () => void) => {
    heldRetry.current = retry;
    setHolding(true);
    markRequired();
  }, [markRequired]);

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

  // A password sign-in has just landed: send the held change, once. Keyed on the
  // phase CHANGING to `fresh`, never on a change being held while it already is,
  // so a rig that refused again would be reported and not retried in a loop.
  const phase = stepUp.phase;
  useEffect(() => {
    if (phase !== "fresh") return;
    const retry = heldRetry.current;
    if (retry === null) return;
    heldRetry.current = null;
    setHolding(false);
    setErr(null);
    retry();
  }, [phase]);

  // The rows report a failure as a sentence, so a refusal for want of a recent
  // sign-in is recognised by that exact sentence (`peopleModel.errText` is the
  // only place that writes it) and raises the SIGN IN AGAIN form beside it.
  const report = useCallback((message: string) => {
    setErr(message);
    if (isStepUpMessage(message)) markRequired();
  }, [markRequired]);

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

      {onRelay && canAdmin && <StepUpCard stepUp={stepUp} holding={holding} />}

      {err && <Verdict tone="bad" data-testid="users-error">{err}</Verdict>}

      {adding && (
        <AddUserForm
          lockedReason={lockedReason}
          onExplain={onExplain}
          lanOnlyReason={lanOnlyReason}
          onStepUp={hold}
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
              onError={report}
              lockedReason={lockedReason}
              onExplain={onExplain}
              lanOnlyReason={lanOnlyReason}
              onStepUp={hold}
            />
          ))}
        </Card>
      )}
    </Section>
  );
}

/** What the five-minute rule is for, what stays on the LAN, and the SIGN IN
 *  AGAIN form for when the rig has asked. Three states, on `data-state`:
 *
 *  - `idle`: the rule and the scope, and a button that opens the form first for
 *    somebody who would rather not find out by being refused;
 *  - `required`: the rig refused a change for want of a recent sign-in (or the
 *    form was opened) - Google by redirect, a local account in place;
 *  - `fresh`: a local sign-in just worked; the note retires itself a little
 *    before the rig's own window closes (`useStepUp`).
 *
 *  A Google sign-in leaves the page, so the card does not promise to come back
 *  to this form; it asks the rig to return to PEOPLE (`googleStepUpHref`) and, for
 *  as long as the rig lands on the home screen instead, says so. */
function StepUpCard({ stepUp, holding }: { stepUp: UseStepUpResult; holding: boolean }): JSX.Element {
  const [typed, setTyped] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const username = typed ?? stepUp.username;

  const submit = async () => {
    if (stepUp.busy || username.trim() === "" || password === "") return;
    const ok = await stepUp.signInLocal(username, password);
    if (ok) setPassword("");
  };

  return (
    <div data-testid="users-stepup" data-state={stepUp.phase}>
      <Card tone={stepUp.phase === "required" ? "accent" : "default"}>
        <Mono size={11}>{stepUp.phase === "required" ? STEP_UP_TITLE : PEOPLE_RELAY_TITLE}</Mono>
        <Note>{PEOPLE_RELAY_RULE}</Note>
        <Note>{PEOPLE_RELAY_SCOPE}</Note>

        {stepUp.phase === "fresh" && (
          <Verdict tone="good" data-testid="users-stepup-fresh">{STEP_UP_FRESH_HINT}</Verdict>
        )}

        {stepUp.phase === "idle" && (stepUp.local || stepUp.google) && (
          <div className="nx-people-actions">
            <ActionButton
              kind="secondary"
              onPress={stepUp.markRequired}
              data-testid="users-stepup-open"
            >
              {STEP_UP_OPEN}
            </ActionButton>
          </div>
        )}

        {stepUp.phase === "required" && (
          <>
            <Note tone="warn" data-testid="users-stepup-why">
              {holding ? STEP_UP_RETRY_HINT : STEP_UP_REQUIRED_HINT}
            </Note>
            {stepUp.google && (
              <>
                <div className="nx-people-actions">
                  <ActionButton
                    kind="primary"
                    onPress={signInGoogleToPeople}
                    data-testid="users-stepup-google"
                  >
                    {SIGN_IN_GOOGLE}
                  </ActionButton>
                </div>
                <Note>{STEP_UP_GOOGLE_NOTE}</Note>
              </>
            )}
            {stepUp.local && (
              <div className="nx-people-reset" data-testid="users-stepup-form">
                <Field label={STEP_UP_USERNAME}>
                  <TextInput
                    value={username}
                    onChange={setTyped}
                    ariaLabel={STEP_UP_USERNAME}
                    data-testid="users-stepup-username"
                  />
                </Field>
                <Field label={STEP_UP_PASSWORD}>
                  <TextInput
                    type="password"
                    value={password}
                    onChange={setPassword}
                    onEnter={() => { void submit(); }}
                    ariaLabel={STEP_UP_PASSWORD}
                    data-testid="users-stepup-password"
                  />
                </Field>
                {stepUp.failure && (
                  <Verdict tone="bad" data-testid="users-stepup-error">
                    {stepUp.failure === "rate_limited" ? STEP_UP_RATE_LIMITED : STEP_UP_FAILED}
                  </Verdict>
                )}
                <div className="nx-people-actions">
                  <ActionButton
                    kind="primary"
                    onPress={() => { void submit(); }}
                    busy={stepUp.busy}
                    data-testid="users-stepup-submit"
                  >
                    {stepUp.busy ? STEP_UP_BUSY : STEP_UP_SUBMIT}
                  </ActionButton>
                </div>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  );
}
