// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// UsersPanel.tsx — admin User Management (W2.6), inside Settings. Gated to the
// `admin.users` capability by SettingsView (this panel assumes it's mounted only
// for an admin). Lists local users (User.to_public() — no password_hash ever),
// and offers: add user, change role, enable/disable, reset password, delete.
//
// Server contract (auth/local_routes.py): GET/POST /api/users, PATCH
// /api/users/{id}, POST /api/users/{id}/password, DELETE /api/users/{id} — all
// admin.users-gated. The store enforces "last admin" protection: a 409 means the
// edit would remove the last enabled admin; a 409 on create/rename means a
// duplicate username. We surface those inline. Delete uses the shared danger
// confirmDialog (mode:"hold") to match the W2.5 destructive-action pattern.
//
// OVER THE RELAY (#685). The rig used to refuse all of /api/users to a relayed
// session; it now answers the list, and four changes (add a Google-only viewer
// or operator, move someone between viewer and operator or enable/disable them,
// delete a non-admin) behind a sign-in under five minutes old. A "Sign in again"
// notice appears on that origin saying what the rule is for, and a refusal for
// want of a recent sign-in (`step_up_required`) opens its form.
//
// What the rig STILL refuses over the relay (403 `local_only`) is locked here
// before anything is typed, with the shared LAN sentence, because a locked
// control sends nothing. That matters most for passwords: the reset (and a
// password on a new account) would otherwise cross the relay on its way to being
// refused, and the relay is the one party the rule keeps passwords away from. So
// over the relay RESET is locked, ADD USER is Google-only and offers viewer and
// operator, an administrator's row (role, enabled, delete) is locked, and the
// admin and syncer options are off on every other row.
//
// F7 #6a: email is required CLIENT-SIDE ONLY on create — the server (POST
// /api/users) still accepts a null email unchanged (no server change in this
// task); this form simply stops offering that path.
// F7 #6b: ROLE_DESCRIPTIONS (lib/caps.ts, sourced from the server's
// role->capability table) is shown at both role-assignment points — creating a
// user and changing an existing one's role — so the choice isn't a guess.

import { useEffect, useState, type FormEvent, type JSX } from "react";
import type { PrincipalRole, User } from "../../types";
import {
  listUsers,
  createUser,
  patchUser,
  resetUserPassword,
  deleteUser,
} from "../../api/backends";
import { ApiError } from "../../api";
import { usePrincipal, useAuthMethods, useStore } from "../../store";
import {
  emailLooksValid, newUserBlocker, newUserBody, passwordTooLong, signInSummary,
  type SignInMethod,
} from "../../lib/userCreate";
import { ROLE_DESCRIPTIONS } from "../../lib/caps";
import { useOnRelay, useStepUp, type UseStepUpResult } from "../../next/lib/gateHook";
import { LOCAL_ONLY_REASON } from "../../next/lib/gate";
import {
  PEOPLE_RELAY_NO_GOOGLE, PEOPLE_RELAY_ROLES, PEOPLE_RELAY_RULE, PEOPLE_RELAY_SCOPE, STEP_UP_BUSY, STEP_UP_FAILED, STEP_UP_FRESH_HINT,
  STEP_UP_GOOGLE_NOTE, STEP_UP_MESSAGE, STEP_UP_OPEN, STEP_UP_PASSWORD, STEP_UP_RATE_LIMITED,
  STEP_UP_REQUIRED_HINT, STEP_UP_SUBMIT, STEP_UP_TITLE, STEP_UP_USERNAME, SIGN_IN_GOOGLE,
  isStepUpMessage, isStepUpRequired,
} from "../../next/hubs/settings/tuning/people/peopleModel";
import { Panel, EmptyState, Toggle } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";

const ROLES: PrincipalRole[] = ["viewer", "syncer", "operator", "admin"];

function errText(e: unknown, fallback: string): string {
  // Before the status branches: a step-up refusal is a 403 like any other, and
  // the panel above recognises it by this exact sentence (`isStepUpMessage`).
  if (isStepUpRequired(e)) return STEP_UP_MESSAGE;
  if (e instanceof ApiError) {
    if (e.status === 409) return e.message || "Conflict.";
    if (e.status === 422) return "Password is too long (max 72 bytes).";
    if (e.status === 404) return "User no longer exists.";
    return e.message || fallback;
  }
  return fallback;
}

export default function UsersPanel(): JSX.Element {
  const me = usePrincipal();
  const [users, setUsers] = useState<User[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const onRelay = useOnRelay();
  const stepUp = useStepUp();
  const { markRequired } = stepUp;

  // The rows report a failure as a sentence, so a refusal for want of a recent
  // sign-in is recognised by that sentence and raises the sign-in form.
  const report = (m: string) => {
    setErr(m);
    if (isStepUpMessage(m)) markRequired();
  };

  const refresh = async () => {
    try {
      setUsers(await listUsers());
      setErr(null);
    } catch (e) {
      setErr(errText(e, "Could not load users."));
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Panel
      title="Users"
      right={
        <button
          type="button"
          className="btn btn-accent !py-1 !px-2.5 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
          onClick={() => setAdding((v) => !v)}
        >
          <Icon name={adding ? "x" : "plus"} size={14} />
          {adding ? "Cancel" : "Add user"}
        </button>
      }
    >
      {onRelay && <StepUpNotice stepUp={stepUp} />}

      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5 mb-3">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}

      {adding && (
        <AddUserForm
          onStepUp={markRequired}
          onCreated={async () => {
            setAdding(false);
            await refresh();
          }}
        />
      )}

      {users == null ? (
        <p className="text-xs text-dim">Loading users…</p>
      ) : users.length === 0 ? (
        <EmptyState
          icon="user"
          title="No users yet"
          hint="Add a local account so people can sign in to control the rig."
          size="inline"
        />
      ) : (
        <ul className="flex flex-col divide-y divide-line">
          {users.map((u) => (
            <UserRow
              key={u.id}
              user={u}
              isSelf={!!me?.email && me.email === u.email}
              onChanged={refresh}
              onError={report}
            />
          ))}
        </ul>
      )}
    </Panel>
  );
}

// --------------------------------------------------------------------- one row
function UserRow({
  user,
  isSelf,
  onChanged,
  onError,
}: {
  user: User;
  isSelf: boolean;
  onChanged: () => Promise<void>;
  onError: (m: string) => void;
}): JSX.Element {
  const showToast = useStore((s) => s.showToast);
  const onRelay = useOnRelay();
  // Over the relay the rig refuses a password reset outright and any change to an
  // administrator (`_REMOTE_LOCAL_ONLY_PATTERNS` and `local_routes._relay_refuses`),
  // so those controls are locked instead of armed: nothing typed into them is
  // sent. The admin and syncer role options are off on the other rows below.
  const resetLock = onRelay ? LOCAL_ONLY_REASON : undefined;
  const adminLock = onRelay && user.role === "admin" ? LOCAL_ONLY_REASON : undefined;
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [pw, setPw] = useState("");
  // The reset's own failure, kept in the reset row rather than at the top of
  // the panel — see submitReset.
  const [pwErr, setPwErr] = useState<string | null>(null);
  const pwTooLong = new TextEncoder().encode(pw).length > 72;

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      await onChanged();
    } catch (e) {
      onError(errText(e, fallback));
    } finally {
      setBusy(false);
    }
  };

  // UX-45: demoting/disabling YOUR OWN account silently drops your access (no
  // last-admin 409 fires when another admin exists), so guard those two self
  // actions with the same danger hold-confirm Delete already uses.
  const onRole = async (role: PrincipalRole) => {
    if (isSelf && user.role === "admin" && role !== "admin") {
      const ok = await confirmDialog({
        title: "Remove your own admin access?",
        body: "Changing your own role away from admin drops your access to Users and Auth and makes this session read-only. Another admin (or the server CLI) would be needed to restore it.",
        confirmLabel: "Change my role",
        tone: "danger",
        mode: "hold",
      });
      if (!ok) return;
    }
    await run(() => patchUser(user.id, { role }), "Could not change role.");
  };

  const onToggle = async (enabled: boolean) => {
    if (isSelf && !enabled) {
      const ok = await confirmDialog({
        title: "Disable your own account?",
        body: "This signs you out and removes your access. Another admin (or the server CLI) would be needed to re-enable you.",
        confirmLabel: "Disable my account",
        tone: "danger",
        mode: "hold",
      });
      if (!ok) return;
    }
    await run(() => patchUser(user.id, { enabled }), "Could not change status.");
  };

  // UX-39: themed inline reset — masked field + client-side 72-byte guard,
  // replacing window.prompt (bright OS dialog that breaks night-mode/the dimmer
  // and echoes the password in cleartext).
  // A failed reset used to end EXACTLY like a successful one — field cleared,
  // row collapsed — because it went through `run()`, whose catch reports to the
  // panel-wide error line above every other user's row. So the one place the
  // failure was stated was the one place it didn't look like it was about this
  // account, and the password was gone. Now the row stays open with what was
  // typed still in it and the reason under the field, and success says so.
  const submitReset = async () => {
    if (busy || pw === "" || pwTooLong) return;
    setPwErr(null);
    setBusy(true);
    try {
      await resetUserPassword(user.id, pw);
      // No list refresh: a password is not part of User.to_public(), so nothing
      // on this row can have changed.
      setPw("");
      setResetting(false);
      showToast("success", `New password set for ${user.username}.`);
    } catch (e) {
      setPwErr(errText(e, "Could not reset password."));
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async () => {
    const ok = await confirmDialog({
      title: `Delete "${user.username}"?`,
      body: "This permanently removes the account. They will be signed out and can no longer sign in.",
      confirmLabel: "Delete user",
      tone: "danger",
      mode: "hold",
    });
    if (!ok) return;
    await run(() => deleteUser(user.id), "Could not delete user.");
  };

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
      {/* identity */}
      <span
        className={`inline-flex items-center justify-center w-8 h-8 border shrink-0
          ${user.enabled ? "border-line2 text-dim" : "border-line text-faint"}`}
        aria-hidden
      >
        <Icon name={user.role === "admin" ? "shield" : user.enabled ? "user" : "eye"} size={15} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-sm text-ink truncate">{user.username}</span>
          {isSelf && (
            <span className="mono text-[9px] tracking-[0.16em] uppercase text-accent border border-accent/40 px-1.5 py-0.5">
              You
            </span>
          )}
          {!user.enabled && (
            <span className="mono text-[9px] tracking-[0.16em] uppercase text-warn border border-warn/50 px-1.5 py-0.5">
              Disabled
            </span>
          )}
        </div>
        {user.email && <div className="text-[11px] text-dim truncate">{user.email}</div>}
      </div>

      {/* role — inline capability description under the select so a role
          change is never a guess (F7 #6b). */}
      <div className="flex flex-col gap-0.5">
        <select
          className="field !py-1 text-xs w-[110px]"
          value={user.role}
          disabled={busy || !!adminLock}
          title={adminLock}
          onChange={(e) => onRole(e.target.value as PrincipalRole)}
          aria-label={`Role for ${user.username}`}
        >
          {ROLES.map((r) => {
            const refused = onRelay && r !== user.role && !PEOPLE_RELAY_ROLES.includes(r);
            return (
              <option key={r} value={r} disabled={refused} title={refused ? LOCAL_ONLY_REASON : undefined}>
                {r}
              </option>
            );
          })}
        </select>
        <span className="text-[10px] text-dim leading-tight max-w-[220px]">
          {ROLE_DESCRIPTIONS[user.role]}
        </span>
      </div>

      {/* enabled toggle */}
      <span className="inline-flex items-center gap-1.5" title={adminLock ?? (user.enabled ? "Enabled" : "Disabled")}>
        <Toggle
          checked={user.enabled}
          onChange={onToggle}
          disabled={busy || !!adminLock}
          label={`${user.enabled ? "Disable" : "Enable"} ${user.username}`}
        />
      </span>

      {/* reset password */}
      <button
        type="button"
        className="btn !py-1 !px-2 min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5 text-[10px]"
        onClick={() => setResetting((v) => !v)}
        disabled={busy || !!resetLock}
        title={resetLock ?? "Reset password"}
        aria-expanded={resetting}
      >
        <Icon name="key" size={14} />
        <span className="hidden lg:inline">Reset</span>
      </button>

      {/* delete */}
      <button
        type="button"
        className="btn btn-danger !py-1 !px-2 min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5 text-[10px]"
        onClick={onDelete}
        disabled={busy || !!adminLock}
        title={adminLock ?? "Delete user"}
      >
        <Icon name="trash" size={14} />
        <span className="hidden lg:inline">Delete</span>
      </button>

      {/* UX-39: inline themed password reset (masked, dimmer-safe) */}
      {resetting && (
        <div className="basis-full flex flex-wrap items-end gap-2 border-t border-line pt-3 mt-1">
          <label className="flex flex-col gap-1 flex-1 min-w-[180px]">
            <span className="label">New password for {user.username}</span>
            <input
              className={`field ${pwTooLong ? "!border-bad" : ""}`}
              type="password"
              autoComplete="new-password"
              value={pw}
              onChange={(e) => { setPw(e.target.value); setPwErr(null); }}
              disabled={busy}
              autoFocus
              aria-invalid={pwTooLong || !!pwErr}
            />
          </label>
          <button
            type="button"
            className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
            onClick={submitReset}
            disabled={busy || pw === "" || pwTooLong}
          >
            <Icon name="check" size={14} />
            {busy ? "Saving…" : "Set password"}
          </button>
          <button
            type="button"
            className="btn min-h-[44px] sm:min-h-0"
            onClick={() => { setResetting(false); setPw(""); setPwErr(null); }}
            disabled={busy}
          >
            Cancel
          </button>
          {pwTooLong && (
            <p className="basis-full text-xs text-bad">Password is too long (max 72 bytes).</p>
          )}
          {pwErr && !pwTooLong && (
            <p className="basis-full text-xs text-bad inline-flex items-center gap-1.5">
              <Icon name="alert" size={13} className="shrink-0" />
              {pwErr} — {user.username}&apos;s password is unchanged.
            </p>
          )}
        </div>
      )}
    </li>
  );
}

// ---------------------------------------------------------------- add-user form
// Exported (I4, 2026-07-17 decisions wave): the guided "Secure this server"
// setup card (AuthMethodPanel.tsx) inlines this SAME form for its "Create an
// admin account" step rather than re-implementing account creation — same F7
// email-required validation, same createUser() call, same error surfacing.
// `defaultRole` only seeds the initial <select> value (still editable); the
// guided card passes "admin" so step 1 defaults to what it asks for, while
// UsersPanel's own "Add user" call site is unchanged (defaults to "operator").
export function AddUserForm({
  onCreated,
  defaultRole = "operator",
  onStepUp,
}: {
  onCreated: () => Promise<void>;
  defaultRole?: PrincipalRole;
  /** Called when the rig refuses the create for want of a recent sign-in, so
   *  the panel can open its sign-in form. Optional: the guided setup card never
   *  runs on the relay (first-run setup is LAN-only) and omits it. */
  onStepUp?: () => void;
}): JSX.Element {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);
  const [method, setMethod] = useState<SignInMethod>("password");
  const [role, setRole] = useState<PrincipalRole>(defaultRole);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Over the relay the rig accepts one kind of new account: Google-only (no
  // password, which the relay would see in the request) and viewer or operator.
  // The form offers exactly that, so nothing typed here is sent to be refused.
  const onRelay = useOnRelay();
  const shownMethod: SignInMethod = onRelay ? "google" : method;
  const roleChoices: readonly PrincipalRole[] = onRelay ? PEOPLE_RELAY_ROLES : ROLES;
  const shownRole: PrincipalRole = roleChoices.includes(role) ? role : "operator";
  // What the last create actually made. The guided "Secure this server" card
  // keeps this form mounted unless the new account is an ENABLED ADMIN, so
  // "created, but as an operator" is the one fact that explains why step 1 is
  // still open — a bare "Created." would leave that unanswerable.
  const [created, setCreated] = useState<string | null>(null);

  // Offering Google-only while Google is off would mint an account that
  // cannot sign in at all, and nothing would say so until they tried.
  const authMethods = useAuthMethods();
  const googleEnabled = !!authMethods?.methods?.includes("google");
  const draft = { username, email, password: onRelay ? "" : password, method: shownMethod };
  const blocker = onRelay && !googleEnabled
    ? PEOPLE_RELAY_NO_GOOGLE
    : newUserBlocker(draft, googleEnabled);
  const tooLong = passwordTooLong(password);

  // F7 #6a: required client-side only (server contract unchanged — see the
  // file-header note). Basic shape check catches an obvious typo without
  // pretending to be a full RFC 5322 validator.
  const emailTrimmed = email.trim();
  const emailMissing = emailTrimmed === "";
  // ONE email rule, shared with the submit gate (lib/userCreate). Two copies
  // of a validation regex is two chances to disagree about what is valid.
  const emailInvalid = !emailMissing && !emailLooksValid(emailTrimmed);
  const emailErrorText = emailMissing ? "Email is required." : emailInvalid ? "Enter a valid email address." : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setEmailTouched(true);
    if (busy || blocker) return;
    setErr(null);
    setCreated(null);
    setBusy(true);
    // `made` splits the two failures this block can see. Only the create call's
    // failure belongs to this form; once the account exists, a parent refresh
    // that throws must not be reported as "Could not create user."
    let made: string | null = null;
    try {
      await createUser({ ...newUserBody(draft), role: shownRole });
      made = `Created "${username.trim()}" as ${shownRole}.`;
      setCreated(made);
      setUsername("");
      setPassword("");
      setEmail("");
      setMethod("password");
      setEmailTouched(false);
      setRole(defaultRole);
      await onCreated();
    } catch (e) {
      if (!made) {
        setErr(errText(e, "Could not create user."));
        if (isStepUpRequired(e)) onStepUp?.();
      }
    } finally {
      // UsersPanel's own call site unmounts this form on success, which is what
      // hid the missing clear; the guided setup card does NOT — it keeps the
      // form up until an enabled admin exists, so creating an operator there
      // froze every field with the button stuck on "Creating…".
      setBusy(false);
    }
  };

  return (
    <form
      onSubmit={submit}
      className="border border-line2 bg-raise/40 p-3 mb-4 flex flex-col gap-3"
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1">
          <span className="label">Username</span>
          <input
            className="field"
            type="text"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            disabled={busy}
            required
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="label">Email</span>
          <input
            className={`field ${emailTouched && (emailMissing || emailInvalid) ? "!border-bad" : ""}`}
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => setEmailTouched(true)}
            disabled={busy}
            required
            aria-required="true"
            aria-invalid={emailTouched && (emailMissing || emailInvalid)}
          />
          {emailTouched && emailErrorText && (
            <span className="text-[11px] text-bad">{emailErrorText}</span>
          )}
        </label>
        {/* ONE account form, two ways in. Demanding a password from someone
            who will only ever arrive through Google means inventing a
            credential nobody uses and everybody could leak. */}
        <label className="flex flex-col gap-1">
          <span className="label">Sign-in method</span>
          <select
            className="field"
            value={shownMethod}
            onChange={(e) => setMethod(e.target.value as SignInMethod)}
            disabled={busy}
          >
            <option value="password" disabled={onRelay} title={onRelay ? LOCAL_ONLY_REASON : undefined}>
              Password
            </option>
            <option value="google">Google sign-in only</option>
          </select>
        </label>
        {shownMethod === "password" ? (
          <label className="flex flex-col gap-1">
            <span className="label">Password</span>
            <input
              className={`field ${tooLong ? "!border-bad" : ""}`}
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={busy}
            />
          </label>
        ) : (
          <div className="flex flex-col gap-1">
            <span className="label">Password</span>
            <p className="text-[11px] text-dim leading-snug">
              Not set — Google does the authenticating. Nothing is stored here
              that could be stolen.
            </p>
          </div>
        )}
        <label className="flex flex-col gap-1">
          <span className="label">Role</span>
          <select
            className="field"
            value={shownRole}
            onChange={(e) => setRole(e.target.value as PrincipalRole)}
            disabled={busy}
          >
            {roleChoices.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      </div>
      {/* F7 #6b: what each role can actually do, right where it's picked. */}
      <ul className="flex flex-col gap-0.5 text-[11px]">
        {roleChoices.map((r) => (
          <li key={r} className={r === shownRole ? "text-ink" : "text-dim"}>
            <span className="mono uppercase tracking-wide">{r}</span> — {ROLE_DESCRIPTIONS[r]}
          </li>
        ))}
      </ul>
      {/* The consequence of the choice, said plainly where it is made. */}
      <p className="text-[11px] text-dim leading-snug">{signInSummary(shownMethod, email)}</p>
      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
      {created && !err && (
        <p className="text-xs text-good inline-flex items-center gap-1.5">
          <Icon name="check" size={13} className="shrink-0" />
          {created}
        </p>
      )}
      <div className="flex justify-end">
        <button
          type="submit"
          className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
          aria-disabled={!!blocker || busy || undefined}
          disabled={busy}
          title={blocker ?? undefined}
          onClick={(e) => { if (blocker) { e.preventDefault(); setEmailTouched(true); setErr(blocker); } }}
        >
          <Icon name="plus" size={15} />
          {busy ? "Creating…" : "Create user"}
        </button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------- sign in again
// What the five-minute rule is for, what stays on the LAN, and the form for when
// the rig has asked. Same three states as the new UI's card (`idle`, `required`,
// `fresh`), driven by the same `useStepUp`, so the two screens cannot disagree.
function StepUpNotice({ stepUp }: { stepUp: UseStepUpResult }): JSX.Element {
  const [typed, setTyped] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const username = typed ?? stepUp.username;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (stepUp.busy || username.trim() === "" || password === "") return;
    if (await stepUp.signInLocal(username, password)) setPassword("");
  };

  return (
    <div
      className="border border-line2 bg-raise/40 p-3 mb-4 flex flex-col gap-2 text-[11px] text-dim leading-snug"
      data-testid="users-stepup"
      data-state={stepUp.phase}
    >
      <span className="label">{stepUp.phase === "required" ? STEP_UP_TITLE : "Changes need a recent sign-in"}</span>
      <p>{PEOPLE_RELAY_RULE}</p>
      <p>{PEOPLE_RELAY_SCOPE}</p>

      {stepUp.phase === "fresh" && (
        <p className="text-xs text-good" data-testid="users-stepup-fresh">{STEP_UP_FRESH_HINT}</p>
      )}

      {stepUp.phase === "idle" && (stepUp.local || stepUp.google) && (
        <div>
          <button
            type="button"
            className="btn min-h-[44px] sm:min-h-0"
            onClick={stepUp.markRequired}
            data-testid="users-stepup-open"
          >
            {STEP_UP_OPEN}
          </button>
        </div>
      )}

      {stepUp.phase === "required" && (
        <>
          <p className="text-xs text-warn" data-testid="users-stepup-why">{STEP_UP_REQUIRED_HINT}</p>
          {stepUp.google && (
            <div className="flex flex-col gap-1.5">
              <div>
                <button
                  type="button"
                  className="btn btn-accent min-h-[44px] sm:min-h-0"
                  onClick={stepUp.signInGoogle}
                  data-testid="users-stepup-google"
                >
                  {SIGN_IN_GOOGLE}
                </button>
              </div>
              <p>{STEP_UP_GOOGLE_NOTE}</p>
            </div>
          )}
          {stepUp.local && (
            <form onSubmit={submit} className="flex flex-wrap items-end gap-2" data-testid="users-stepup-form">
              <label className="flex flex-col gap-1 flex-1 min-w-[160px]">
                <span className="label">{STEP_UP_USERNAME}</span>
                <input
                  className="field"
                  type="text"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  value={username}
                  onChange={(e) => setTyped(e.target.value)}
                  data-testid="users-stepup-username"
                />
              </label>
              <label className="flex flex-col gap-1 flex-1 min-w-[160px]">
                <span className="label">{STEP_UP_PASSWORD}</span>
                <input
                  className="field"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  data-testid="users-stepup-password"
                />
              </label>
              <button
                type="submit"
                className="btn btn-accent min-h-[44px] sm:min-h-0"
                data-testid="users-stepup-submit"
              >
                {stepUp.busy ? STEP_UP_BUSY : STEP_UP_SUBMIT}
              </button>
              {stepUp.failure && (
                <p className="basis-full text-xs text-bad" data-testid="users-stepup-error">
                  {stepUp.failure === "rate_limited" ? STEP_UP_RATE_LIMITED : STEP_UP_FAILED}
                </p>
              )}
            </form>
          )}
        </>
      )}
    </div>
  );
}
