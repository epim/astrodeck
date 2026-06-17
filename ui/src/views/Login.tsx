// Login.tsx — the full-screen sign-in gate (W2.6). Shown by App ONLY when a login
// method is enabled AND the caller is not signed in (or a first admin still needs
// creating). When NO method is enabled this is NEVER mounted — the open LAN UI is
// unchanged (App's gate short-circuits on methods == []).
//
// Three stacked affordances, driven by GET /api/auth/methods:
//   - FIRST-RUN create-admin form  (when authMethods.first_run)
//   - local username/password form (when "local" in methods)
//   - "Sign in with Google" button (when "google" in methods && google_configured)
//
// On a successful local login / first-run setup the server sets the ad_session
// cookie; we then re-resolve /api/me + /api/auth/methods + /api/config so the gate
// dissolves and the role chip + cap gates flip live — no page reload. Google
// sign-in is a full-page redirect through the server OAuth dance (NOT a fetch).

import { useState, type FormEvent, type JSX } from "react";
import { useStore, useAuthMethods } from "../store";
import { localLogin, setupLocalAdmin } from "../api/backends";
import { ApiError } from "../api";
import { Icon } from "../components/icons";
import Logo from "../components/Logo";

// Re-resolve identity + gate signals after a credential change. Keep config in
// sync too (its redacted `auth` block backs the Settings panels).
async function refreshSession(): Promise<void> {
  const st = useStore.getState();
  await Promise.all([st.loadPrincipal(), st.loadAuthMethods(), st.loadConfig()]);
}

export default function Login(): JSX.Element {
  const methods = useAuthMethods();
  const enabled = methods?.methods ?? [];
  const localOn = enabled.includes("local");
  const googleOn = enabled.includes("google") && !!methods?.google_configured;
  const firstRun = !!methods?.first_run;

  return (
    <div className="min-h-full w-full flex items-center justify-center p-4 bg-bg">
      <div className="w-full max-w-[380px] flex flex-col gap-5">
        {/* brand lockup */}
        <div className="flex flex-col items-center gap-3 mb-1 select-none">
          <Logo size={44} />
          <h1 className="font-display font-semibold tracking-[0.3em] text-accent text-base">
            ASTRO<span className="text-ink">DECK</span>
          </h1>
          <p className="text-[11px] text-dim text-center">
            {firstRun
              ? "First run — create the initial administrator account."
              : "Sign in to control the rig."}
          </p>
        </div>

        {firstRun ? (
          <FirstRunForm onDone={refreshSession} />
        ) : (
          <>
            {localOn && <LocalForm onDone={refreshSession} />}
            {localOn && googleOn && (
              <div className="flex items-center gap-3 text-[10px] text-faint uppercase tracking-[0.18em]">
                <span className="flex-1 h-px bg-line2" />
                or
                <span className="flex-1 h-px bg-line2" />
              </div>
            )}
            {googleOn && <GoogleButton />}
            {!localOn && !googleOn && (
              <div className="panel p-4 text-center">
                <p className="text-xs text-dim">
                  No sign-in method is available. Ask an administrator, or use the
                  break-glass admin token.
                </p>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------- local login form
function LocalForm({ onDone }: { onDone: () => Promise<void> }): JSX.Element {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setErr(null);
    setBusy(true);
    try {
      await localLogin(username.trim(), password);
      await onDone(); // dissolves the gate (principal now resolves)
    } catch (e) {
      // Generic message by design (the server returns no enumeration oracle).
      const msg =
        e instanceof ApiError
          ? e.status === 401
            ? "Invalid username or password."
            : e.message
          : "Sign-in failed.";
      setErr(msg);
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="panel p-4 flex flex-col gap-3">
      <label className="flex flex-col gap-1">
        <span className="label">Username</span>
        <input
          className="field"
          type="text"
          autoComplete="username"
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
        <span className="label">Password</span>
        <input
          className="field"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={busy}
          required
        />
      </label>
      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
      <button
        type="submit"
        className="btn btn-accent min-h-[44px] inline-flex items-center justify-center gap-2"
        disabled={busy || !username.trim() || !password}
      >
        <Icon name="user" size={15} />
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}

// ------------------------------------------------------------ first-run create-admin
function FirstRunForm({ onDone }: { onDone: () => Promise<void> }): JSX.Element {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const mismatch = confirm.length > 0 && password !== confirm;
  const tooLong = new TextEncoder().encode(password).length > 72;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy || mismatch || tooLong) return;
    setErr(null);
    setBusy(true);
    try {
      await setupLocalAdmin({
        username: username.trim(),
        password,
        email: email.trim() || null,
      });
      await onDone(); // gate dissolves; first_run flips false server-side
    } catch (e) {
      let msg = "Could not create the administrator.";
      if (e instanceof ApiError) {
        if (e.status === 409) msg = "Setup is already complete — an account exists.";
        else if (e.status === 422) msg = "Password is too long (max 72 bytes).";
        else if (e.status === 404) msg = "First-run setup is not available.";
        else msg = e.message || msg;
      }
      setErr(msg);
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="panel p-4 flex flex-col gap-3">
      <div className="inline-flex items-center gap-2 text-[11px] text-accent mb-1">
        <Icon name="shield" size={14} />
        <span className="font-display tracking-[0.16em] uppercase">Create administrator</span>
      </div>
      <label className="flex flex-col gap-1">
        <span className="label">Username</span>
        <input
          className="field"
          type="text"
          autoComplete="username"
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
          autoComplete="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={busy}
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="label">Password</span>
        <input
          className="field"
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={busy}
          required
        />
      </label>
      <label className="flex flex-col gap-1">
        <span className="label">Confirm password</span>
        <input
          className={`field ${mismatch ? "!border-bad" : ""}`}
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          disabled={busy}
          required
        />
      </label>
      {mismatch && <p className="text-xs text-bad">Passwords don't match.</p>}
      {tooLong && <p className="text-xs text-bad">Password is too long (max 72 bytes).</p>}
      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
      <button
        type="submit"
        className="btn btn-accent min-h-[44px] inline-flex items-center justify-center gap-2"
        disabled={busy || !username.trim() || !password || mismatch || tooLong}
      >
        <Icon name="shield" size={15} />
        {busy ? "Creating…" : "Create administrator & sign in"}
      </button>
    </form>
  );
}

// -------------------------------------------------------------------- google button
function GoogleButton(): JSX.Element {
  return (
    <button
      type="button"
      className="btn min-h-[44px] inline-flex items-center justify-center gap-2"
      // Full-page redirect through the server OAuth dance (NOT a fetch).
      onClick={() => {
        window.location.href = "/auth/login";
      }}
    >
      <Icon name="user" size={15} />
      Sign in with Google
    </button>
  );
}
