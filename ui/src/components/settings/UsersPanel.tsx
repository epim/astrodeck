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

  const onRole = (role: PrincipalRole) =>
    run(() => patchUser(user.id, { role }), "Could not change role.");

  const onToggle = (enabled: boolean) =>
    run(() => patchUser(user.id, { enabled }), "Could not change status.");

  const onReset = async () => {
    const pw = window.prompt(`New password for "${user.username}":`);
    if (pw == null || pw === "") return;
    await run(() => resetUserPassword(user.id, pw), "Could not reset password.");
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

      {/* role */}
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
        onClick={onReset}
        disabled={busy}
        title="Reset password"
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
    </li>
  );
}

// ---------------------------------------------------------------- add-user form
function AddUserForm({ onCreated }: { onCreated: () => Promise<void> }): JSX.Element {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<PrincipalRole>("operator");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const tooLong = new TextEncoder().encode(password).length > 72;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || tooLong) return;
    setErr(null);
    setBusy(true);
    try {
      await createUser({
        username: username.trim(),
        password,
        role,
        email: email.trim() || null,
      });
      setUsername("");
      setPassword("");
      setEmail("");
      setRole("operator");
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
          <span className="label">Email (optional)</span>
          <input
            className="field"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            disabled={busy}
          />
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
          disabled={busy || !username.trim() || !password || tooLong}
        >
          <Icon name="plus" size={15} />
          {busy ? "Creating…" : "Create user"}
        </button>
      </div>
    </form>
  );
}
