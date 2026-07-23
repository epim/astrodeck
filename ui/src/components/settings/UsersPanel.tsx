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
import { usePrincipal } from "../../store";
import { ROLE_DESCRIPTIONS } from "../../lib/caps";
import { Panel, EmptyState, Toggle } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";

const ROLES: PrincipalRole[] = ["viewer", "operator", "admin"];

function errText(e: unknown, fallback: string): string {
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
      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5 mb-3">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}

      {adding && (
        <AddUserForm
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
              onError={(m) => setErr(m)}
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
  const [busy, setBusy] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [pw, setPw] = useState("");
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
  const submitReset = async () => {
    if (busy || pw === "" || pwTooLong) return;
    await run(() => resetUserPassword(user.id, pw), "Could not reset password.");
    setPw("");
    setResetting(false);
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
          disabled={busy}
          onChange={(e) => onRole(e.target.value as PrincipalRole)}
          aria-label={`Role for ${user.username}`}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <span className="text-[10px] text-dim leading-tight max-w-[220px]">
          {ROLE_DESCRIPTIONS[user.role]}
        </span>
      </div>

      {/* enabled toggle */}
      <span className="inline-flex items-center gap-1.5" title={user.enabled ? "Enabled" : "Disabled"}>
        <Toggle
          checked={user.enabled}
          onChange={onToggle}
          disabled={busy}
          label={`${user.enabled ? "Disable" : "Enable"} ${user.username}`}
        />
      </span>

      {/* reset password */}
      <button
        type="button"
        className="btn !py-1 !px-2 min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5 text-[10px]"
        onClick={() => setResetting((v) => !v)}
        disabled={busy}
        title="Reset password"
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
        disabled={busy}
        title="Delete user"
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
              onChange={(e) => setPw(e.target.value)}
              disabled={busy}
              autoFocus
              aria-invalid={pwTooLong}
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
            onClick={() => { setResetting(false); setPw(""); }}
            disabled={busy}
          >
            Cancel
          </button>
          {pwTooLong && (
            <p className="basis-full text-xs text-bad">Password is too long (max 72 bytes).</p>
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
}: {
  onCreated: () => Promise<void>;
  defaultRole?: PrincipalRole;
}): JSX.Element {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [emailTouched, setEmailTouched] = useState(false);
  const [role, setRole] = useState<PrincipalRole>(defaultRole);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const tooLong = new TextEncoder().encode(password).length > 72;

  // F7 #6a: required client-side only (server contract unchanged — see the
  // file-header note). Basic shape check catches an obvious typo without
  // pretending to be a full RFC 5322 validator.
  const emailTrimmed = email.trim();
  const emailMissing = emailTrimmed === "";
  const emailInvalid = !emailMissing && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(emailTrimmed);
  const emailErrorText = emailMissing ? "Email is required." : emailInvalid ? "Enter a valid email address." : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setEmailTouched(true);
    if (busy || tooLong || emailMissing || emailInvalid) return;
    setErr(null);
    setBusy(true);
    try {
      await createUser({
        username: username.trim(),
        password,
        role,
        email: emailTrimmed,
      });
      setUsername("");
      setPassword("");
      setEmail("");
      setEmailTouched(false);
      setRole(defaultRole);
      await onCreated();
    } catch (e) {
      setErr(errText(e, "Could not create user."));
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
        <label className="flex flex-col gap-1">
          <span className="label">Password</span>
          <input
            className={`field ${tooLong ? "!border-bad" : ""}`}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
            required
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="label">Role</span>
          <select
            className="field"
            value={role}
            onChange={(e) => setRole(e.target.value as PrincipalRole)}
            disabled={busy}
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      </div>
      {/* F7 #6b: what each role can actually do, right where it's picked. */}
      <ul className="flex flex-col gap-0.5 text-[11px]">
        {ROLES.map((r) => (
          <li key={r} className={r === role ? "text-ink" : "text-dim"}>
            <span className="mono uppercase tracking-wide">{r}</span> — {ROLE_DESCRIPTIONS[r]}
          </li>
        ))}
      </ul>
      {tooLong && <p className="text-xs text-bad">Password is too long (max 72 bytes).</p>}
      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
      <div className="flex justify-end">
        <button
          type="submit"
          className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
          disabled={busy || !username.trim() || !password || tooLong || emailMissing || emailInvalid}
        >
          <Icon name="plus" size={15} />
          {busy ? "Creating…" : "Create user"}
        </button>
      </div>
    </form>
  );
}
