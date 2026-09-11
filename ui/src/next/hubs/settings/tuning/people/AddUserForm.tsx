// AddUserForm.tsx - "create a local account", rebuilt in the design's
// vocabulary (wave R7, T-R7-11; plan section 3.F4).
//
// ONE FORM, TWO CALL SITES, exactly as the legacy
// `components/settings/UsersPanel.tsx`'s exported `AddUserForm` had: the PEOPLE
// editor's ADD USER, and step 1 of the "Secure this server" guided card in
// `AuthMethodsEditor`. `defaultRole` seeds the picker (the guided card asks for
// an admin) and nothing else differs, so the two paths cannot drift into two
// different validations of the same account.
//
// The decision layer is `lib/userCreate.ts` UNCHANGED - `newUserBlocker`,
// `newUserBody`, `signInSummary`, `emailLooksValid`, `passwordTooLong`. That is
// a LOGIC module (plan section 2.1), shared rather than re-derived: two copies
// of an email rule is two chances to disagree about what an address is.
//
// Two behaviours worth naming because they are easy to lose in a re-skin:
//
//  * `created` is kept separate from `err`. Once `createUser` has returned, the
//    account EXISTS; a parent refresh that then throws must not be reported as
//    "could not create the account". The legacy file's `made` variable did this
//    and the sentence it prints ("Created X as operator") is the only thing
//    that explains why the guided card's step 1 is still open after a
//    successful create.
//  * the submit button is honest-disabled on `newUserBlocker`'s SENTENCE, never
//    a bare boolean, so a form that cannot be submitted always says why.

import { useState, type FormEvent, type JSX } from "react";
import { createUser } from "../../../../../api/backends";
import {
  emailLooksValid, newUserBlocker, newUserBody, passwordTooLong, signInSummary,
  type SignInMethod,
} from "../../../../../lib/userCreate";
import { ROLE_DESCRIPTIONS } from "../../../../../lib/caps";
import { useAuthMethods } from "../../../../../store";
import type { PrincipalRole } from "../../../../../types";
import { ActionButton, Card, Field, Segmented, TextInput } from "../../../../ui";
import { Note, ScrollRow, Verdict } from "./PeopleSection";
import {
  ADD_EMAIL, ADD_METHOD, ADD_PASSWORD, ADD_ROLE, ADD_SUBMIT, ADD_SUBMIT_BUSY, ADD_USERNAME,
  EMAIL_INVALID, EMAIL_REQUIRED, GOOGLE_ONLY_PASSWORD_NOTE, METHOD_GOOGLE, METHOD_PASSWORD,
  PASSWORD_TOO_LONG, PRINCIPAL_ROLES, USERS_CREATE_FAILED, createdLine, errText, roleWord,
} from "./peopleModel";

export function AddUserForm({
  onCreated, defaultRole = "operator", lockedReason = null, onExplain,
}: {
  /** Called after a successful create so the caller can re-read its own list.
   *  Its failure is the caller's, not this form's - see the header note. */
  onCreated: () => Promise<void>;
  defaultRole?: PrincipalRole;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
}): JSX.Element {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);
  const [method, setMethod] = useState<SignInMethod>("password");
  const [role, setRole] = useState<PrincipalRole>(defaultRole);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  // Offering "Google only" while Google is off would mint an account that can
  // never sign in, and nothing would say so until somebody tried.
  const authMethods = useAuthMethods();
  const googleEnabled = !!authMethods?.methods?.includes("google");
  const draft = { username, email, password, method };
  const blocker = newUserBlocker(draft, googleEnabled);
  const tooLong = passwordTooLong(password);

  const emailTrimmed = email.trim();
  const emailMissing = emailTrimmed === "";
  const emailInvalid = !emailMissing && !emailLooksValid(emailTrimmed);
  const emailError = emailTouched
    ? (emailMissing ? EMAIL_REQUIRED : emailInvalid ? EMAIL_INVALID : null)
    : null;

  const submit = async (e?: FormEvent) => {
    e?.preventDefault();
    setEmailTouched(true);
    if (busy) return;
    setErr(null);
    setCreated(null);
    setBusy(true);
    let made = false;
    try {
      await createUser({ ...newUserBody(draft), role });
      made = true;
      setCreated(createdLine(username.trim(), role));
      setUsername("");
      setPassword("");
      setEmail("");
      setMethod("password");
      setEmailTouched(false);
      setRole(defaultRole);
      await onCreated();
    } catch (e2) {
      if (!made) setErr(errText(e2, USERS_CREATE_FAILED));
    } finally {
      // The PEOPLE editor unmounts this form on success; the guided setup card
      // does NOT (it stays up until an enabled admin exists), and that is where
      // a missing clear froze every field with the button stuck on CREATING.
      setBusy(false);
    }
  };

  // The group lock (no admin.users) outranks the draft's own blocker: telling
  // somebody their email is missing is noise when nothing on the form can be
  // submitted at all.
  const submitLock = lockedReason ?? blocker;

  return (
    <Card className="nx-people-form" data-testid="users-add-form">
      <form
        onSubmit={submit}
        className="nx-people-grid"
        // A native submit would reload the page under jsdom and on a phone
        // keyboard's Go key alike; `submit` preventDefaults, and the button
        // below is the only other way in.
      >
        <Field label={ADD_USERNAME}>
          <TextInput
            value={username}
            onChange={setUsername}
            ariaLabel="New account username"
            lockedReason={lockedReason}
            data-testid="users-add-username"
          />
        </Field>

        <Field label={ADD_EMAIL} hint={emailError ?? undefined}>
          <TextInput
            type="email"
            value={email}
            onChange={setEmail}
            onBlur={() => setEmailTouched(true)}
            ariaLabel="New account email address"
            lockedReason={lockedReason}
            data-testid="users-add-email"
          />
        </Field>

        <Field label={ADD_METHOD}>
          <ScrollRow>
            <Segmented<SignInMethod>
              label="Sign-in method for the new account"
              value={method}
              onChange={setMethod}
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid="users-add-method"
              options={[
                { value: "password", label: METHOD_PASSWORD },
                {
                  value: "google",
                  label: METHOD_GOOGLE,
                  lockedReason: googleEnabled ? null
                    : "Google sign-in is off, so an account with no password could never sign in.",
                },
              ]}
            />
          </ScrollRow>
        </Field>

        {method === "password" ? (
          <Field label={ADD_PASSWORD} hint={tooLong ? PASSWORD_TOO_LONG : undefined}>
            <TextInput
              type="password"
              value={password}
              onChange={setPassword}
              ariaLabel="New account password"
              lockedReason={lockedReason}
              data-testid="users-add-password"
            />
          </Field>
        ) : (
          <Field label={ADD_PASSWORD}>
            <Note>{GOOGLE_ONLY_PASSWORD_NOTE}</Note>
          </Field>
        )}

        <Field label={ADD_ROLE} hint={ROLE_DESCRIPTIONS[role]} className="nx-people-span2">
          <ScrollRow>
            <Segmented<PrincipalRole>
              label="Role for the new account"
              value={role}
              onChange={setRole}
              lockedReason={lockedReason}
              onExplain={onExplain}
              data-testid="users-add-role"
              options={PRINCIPAL_ROLES.map((r) => ({ value: r, label: roleWord(r) }))}
            />
          </ScrollRow>
        </Field>
      </form>

      {/* The consequence of the method choice, said where it is made. */}
      <Note data-testid="users-add-summary">{signInSummary(method, email)}</Note>

      {err && <Verdict tone="bad" data-testid="users-add-error">{err}</Verdict>}
      {created && !err && <Verdict tone="good" data-testid="users-add-created">{created}</Verdict>}

      <div className="nx-people-actions">
        <ActionButton
          kind="primary"
          onPress={() => { void submit(); }}
          busy={busy}
          lockedReason={submitLock}
          onExplain={(r) => { setEmailTouched(true); onExplain?.(r); }}
          data-testid="users-create"
        >
          {busy ? ADD_SUBMIT_BUSY : ADD_SUBMIT}
        </ActionButton>
      </div>
    </Card>
  );
}
