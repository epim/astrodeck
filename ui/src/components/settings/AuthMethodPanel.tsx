// AuthMethodPanel.tsx — admin auth-method configuration (W2.6), inside Settings.
// Gated to `admin.users` by SettingsView. Toggles the enabled login methods
// (local / google), sets the shared session lifetime, the first-run flag, and the
// default role for authenticated users. Saves via POST /api/auth/config.
//
// NON-BREAKING (LOUD): when BOTH methods are off, `methods == []` re-opens the
// server to admin-for-all on the LAN and REMOVES the login screen. The panel
// states this explicitly so an admin can never disable auth by accident without
// seeing the consequence.
//
// SECRETS: the panel reads the REDACTED auth block (config.auth) — every secret
// is already blanked there. It echoes those blanks back on save; the server reads
// a blank secret as "unchanged" (mirrors the alerts "empty token" contract), so
// toggling a method never wipes a stored Google secret / signing key. Google
// client credentials are provisioned on the server (env / config file), so this
// panel surfaces their *configured* state rather than editing the secret here.

import { useEffect, useMemo, useState, type JSX } from "react";
import type { AuthState, PrincipalRole, User } from "../../types";
import { setAuthConfig, listUsers } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { reconnectWs } from "../../ws";
import { Panel, Field, Toggle } from "../ui";
import { Icon } from "../icons";
import { AddUserForm } from "./UsersPanel";
import { setupCardState, SETUP_CARD_DISMISSED_KEY } from "../../lib/setupCard";

const ROLES: (PrincipalRole | "deny")[] = ["deny", "viewer", "operator", "admin"];

export default function AuthMethodPanel(): JSX.Element {
  const config = useConfig();
  const auth = config?.auth;

  // Draft mirrors the redacted server block; reset whenever the server value lands
  // (e.g. after a save broadcasts a fresh config). Secrets stay blank — we never
  // edit them here, and a blank echo means "unchanged" on the server.
  const [localOn, setLocalOn] = useState(false);
  const [googleOn, setGoogleOn] = useState(false);
  const [ttlH, setTtlH] = useState(8);
  const [firstRun, setFirstRun] = useState(true);
  const [trustLoopback, setTrustLoopback] = useState(true);
  const [defaultRole, setDefaultRole] = useState<PrincipalRole | "deny">("deny");
  // Held as ORDERED PAIRS rather than the Record it is on the wire: two rows
  // mid-edit can transiently share an empty key, and an object would silently
  // merge them (or reorder rows as keys change under the cursor).
  const [allowlist, setAllowlist] = useState<[string, string][]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // ---- I4 guided "Secure this server" card (2026-07-17 decisions wave) ----
  // `setupUsers` is read from the SAME source UsersPanel uses (listUsers() ->
  // GET /api/users, admin.users-gated same as this whole tab) — no server
  // change, just this second panel reading the same list so step 1's
  // done-state ("an ENABLED admin-role local user exists") isn't a guess.
  const [setupUsers, setSetupUsers] = useState<User[] | null>(null);
  const [setupDismissed, setSetupDismissed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SETUP_CARD_DISMISSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  const refreshSetupUsers = async () => {
    try {
      setSetupUsers(await listUsers());
    } catch {
      // Leave setupUsers as-is; the guided card just stays honest about what
      // it could actually confirm (no confirmed enabled admin -> step 1
      // stays open rather than claiming "done" on a failed fetch).
    }
  };

  const dismissSetupCard = () => {
    setSetupDismissed(true);
    try {
      localStorage.setItem(SETUP_CARD_DISMISSED_KEY, "1");
    } catch {
      /* quota / unavailable — dismissal just won't survive reload */
    }
  };
  const reopenSetupCard = () => {
    setSetupDismissed(false);
    try {
      localStorage.setItem(SETUP_CARD_DISMISSED_KEY, "0");
    } catch {
      /* quota / unavailable — keep in-memory */
    }
  };

  const seedKey = useMemo(
    () =>
      auth
        ? JSON.stringify([
            auth.methods,
            auth.session_ttl_s,
            auth.local_enabled_first_run,
            auth.trust_loopback,
            auth.default_role,
            auth.role_allowlist,
          ])
        : "",
    [auth],
  );

  useEffect(() => {
    if (!auth) return;
    setLocalOn((auth.methods ?? []).includes("local"));
    setGoogleOn((auth.methods ?? []).includes("google"));
    setTtlH(Math.max(1, Math.round((auth.session_ttl_s ?? 28800) / 3600)));
    setFirstRun(auth.local_enabled_first_run ?? true);
    setTrustLoopback(auth.trust_loopback ?? true);
    setDefaultRole((auth.default_role as PrincipalRole) ?? "deny");
    setAllowlist(Object.entries(auth.role_allowlist ?? {}));
    // I4: only worth fetching the user list while auth is still open
    // (methods==[]) — once a method is enabled the guided card can never
    // show again (see setupCardState), so skip the extra admin.users
    // GET /api/users call in steady state.
    if ((auth.methods ?? []).length === 0) void refreshSetupUsers();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seedKey]);

  if (!auth) {
    return (
      <Panel title="Sign-in methods">
        <p className="text-xs text-dim">Loading auth configuration…</p>
      </Panel>
    );
  }

  const googleConfigured = !!auth.google_configured;
  const adminTokenSet = !!auth.admin_token_configured;
  const methods: string[] = [
    ...(localOn ? ["local"] : []),
    ...(googleOn ? ["google"] : []),
  ];
  const openWarning = methods.length === 0;

  // I4 guided card: keyed off the SERVER-persisted methods (not the draft
  // toggles above) so the card doesn't vanish the instant an admin flips a
  // checkbox that hasn't been saved yet — see setupCard.ts's doc comment.
  const methodsEnabledPersisted = (auth.methods ?? []).length > 0;
  const hasEnabledAdmin = (setupUsers ?? []).some((u) => u.role === "admin" && u.enabled);
  const setup = setupCardState(methodsEnabledPersisted, hasEnabledAdmin, setupDismissed);

  const save = async () => {
    if (busy) return;
    setErr(null);
    setBusy(true);
    // The auth-boundary transition (R4B-AUTH-02): computed against the SERVER
    // state before this save, so the message fires exactly when the save flips
    // the server from open to authenticated.
    const wasEnabled = (auth.methods ?? []).length > 0;
    try {
      // Echo the redacted block (secrets blank => "unchanged" server-side) with
      // our edits applied. `provider` stays as the redacted value (legacy/migrate
      // field — `methods` is the source of truth).
      const body: Partial<AuthState> & Record<string, unknown> = {
        ...auth,
        methods,
        session_ttl_s: Math.max(1, ttlH) * 3600,
        local_enabled_first_run: firstRun,
        trust_loopback: trustLoopback,
        default_role: defaultRole === "deny" ? null : defaultRole,
        // Blank and malformed rows are dropped rather than sent: an entry with
        // no @ can never match a Google account, so persisting it would only
        // look like access somebody has. Last write wins on a duplicate address.
        role_allowlist: Object.fromEntries(
          allowlist
            .map(([e, r]) => [e.trim().toLowerCase(), r] as [string, string])
            .filter(([e]) => e.includes("@"))),
      };
      const next = await setAuthConfig(body);
      // Deterministic transition message (R4B-AUTH-02): when this save ENABLED
      // authentication, say exactly what just changed — every client (including
      // this tab, which lands on Login next) must now sign in. Sticky (ttl 0)
      // so the Login transition can't swallow it; Toasts stay mounted there.
      if (!wasEnabled && methods.length > 0) {
        useStore.getState().enqueueToast({
          level: "info",
          title: "Authentication enabled — every client must now sign in.",
          ttl: 0,
        });
      }
      // Re-hydrate config + the login-screen signal AND the principal so
      // RoleBadge/gates/login flip. loadPrincipal is the H1 fix for the SAVING
      // tab: enabling a method live-flips the provider, so THIS browser (which
      // has no session) now gets 401 from /api/me → the viewer sentinel → App
      // raises the Login gate. Without re-resolving the principal the tab kept a
      // stale admin identity and rendered contradictory panels over a dead link.
      await Promise.all([
        useStore.getState().loadConfig(),
        useStore.getState().loadAuthMethods(),
        useStore.getState().loadPrincipal(),
      ]);
      // Re-sync the socket to the new auth posture immediately (the server also
      // closes the old socket on its next re-auth, but that can be up to a minute
      // away). Enabling a method → the reconnect is rejected (no session) and the
      // tab is already on Login; disabling all methods → it reconnects open.
      reconnectWs();
      void next;
      setSavedAt(Date.now());
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.code === "invalid_auth"
            ? e.message
            : e.message || "Could not save."
          : "Could not save.";
      setErr(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* I4 guided "Secure this server" card — only while auth is open (no
          methods enabled) and not dismissed. Enforces the enable-method-first
          ORDER from docs/guide/remote-access-and-roles.md's verification
          procedure: step 2 stays locked until step 1's admin account is
          confirmed ENABLED, not merely created. */}
      {setup.visible && (
        <Panel
          title="Secure this server"
          right={
            <button
              type="button"
              className="btn !py-1 !px-2 min-h-[44px] sm:min-h-0 inline-flex items-center"
              onClick={dismissSetupCard}
              aria-label="Dismiss setup guide"
              title="Dismiss"
            >
              <Icon name="x" size={14} />
            </button>
          }
        >
          <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-3">
            This server is open right now — every client on the network is
            admin and there&apos;s no sign-in screen. Follow these steps, in
            order, to lock it down.
          </p>
          <ol className="flex flex-col divide-y divide-line">
            {/* step 1 */}
            <li className="flex items-start gap-3 py-3">
              <StepBadge n={1} state={setup.step1Done ? "done" : "active"} />
              <div className="min-w-0 flex-1">
                <div className="text-sm text-ink inline-flex items-center gap-2">
                  Create an admin account
                  {setup.step1Done && (
                    <span className="text-[11px] text-good inline-flex items-center gap-1.5">
                      <Icon name="check" size={13} /> Done
                    </span>
                  )}
                </div>
                {!setup.step1Done ? (
                  <>
                    <p className="text-[11px] text-dim max-w-md mb-2">
                      This is the account you&apos;ll sign in as once Local
                      sign-in is on. Create it here, or manage accounts any
                      time from the Users tab above.
                    </p>
                    <AddUserForm defaultRole="admin" onCreated={refreshSetupUsers} />
                  </>
                ) : (
                  <p className="text-[11px] text-dim max-w-md">
                    An enabled admin account exists. Manage accounts any time
                    from the Users tab above.
                  </p>
                )}
              </div>
            </li>

            {/* step 2 */}
            <li className="flex items-start gap-3 py-3">
              <StepBadge n={2} state={!setup.step2Enabled ? "locked" : "active"} />
              <div className="min-w-0 flex-1">
                <div className="text-sm text-ink">Enable Local sign-in</div>
                <p className="text-[11px] text-dim max-w-md mb-2">
                  {!setup.step2Enabled
                    ? "Unlocks once step 1's admin account is confirmed enabled — creating a user alone doesn't turn on sign-in."
                    : localOn
                      ? "Armed — press Save methods below to finish."
                      : "Turns on the Local username & password method below."}
                </p>
                <button
                  type="button"
                  className="btn btn-accent !py-1 !px-2.5 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
                  disabled={!setup.step2Enabled || busy || localOn}
                  onClick={() => setLocalOn(true)}
                >
                  <Icon name="unlock" size={13} />
                  {localOn ? "Enabled — save below" : "Enable local sign-in"}
                </button>
              </div>
            </li>

            {/* step 3 — explanatory only, no button (composes with the
                post-save toast below + the Login routing it hands off to). */}
            <li className="flex items-start gap-3 py-3">
              <StepBadge n={3} state="active" />
              <div className="min-w-0 flex-1">
                <div className="text-sm text-ink">Saving signs you out too</div>
                <p className="text-[11px] text-dim max-w-md">
                  Saving signs every client out — including this one.
                  You&apos;ll land on the sign-in page.
                </p>
              </div>
            </li>
          </ol>
        </Panel>
      )}

      {/* Reappears only while auth is open AND it was dismissed — the un-dismiss
          affordance (spec: "small 'Setup guide' affordance to un-dismiss"). */}
      {!methodsEnabledPersisted && setupDismissed && (
        <button
          type="button"
          className="self-start text-[11px] text-dim underline decoration-dotted inline-flex items-center gap-1.5"
          onClick={reopenSetupCard}
        >
          <Icon name="info" size={12} />
          Setup guide
        </button>
      )}

      <Panel
        title="Sign-in methods"
        right={
          <span className="inline-flex items-center gap-1.5 text-[11px] text-dim">
            <Icon name="shield" size={14} />
            RBAC
          </span>
        }
      >
        <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-4">
          Choose how people sign in. Both methods can be enabled at once. With every
          method off, the server trusts the local network and grants every client
          admin — no sign-in is shown.
        </p>

        {/* local */}
        <div className="flex items-start justify-between gap-3 py-3 border-t border-line">
          <div className="min-w-0">
            <div className="text-sm text-ink">Local username &amp; password</div>
            <p className="text-[11px] text-dim max-w-md">
              Offline accounts stored on the rig — sign in straight from a phone or
              tablet with no internet. Manage accounts in the Users panel.
            </p>
          </div>
          <Toggle checked={localOn} onChange={setLocalOn} disabled={busy} label="Enable local accounts" showState />
        </div>

        {/* google */}
        <div className="flex items-start justify-between gap-3 py-3 border-t border-line">
          <div className="min-w-0">
            <div className="text-sm text-ink inline-flex items-center gap-2">
              Google sign-in
              {!googleConfigured && (
                <span className="mono text-[9px] tracking-[0.16em] uppercase text-warn border border-warn/50 px-1.5 py-0.5">
                  Not configured
                </span>
              )}
            </div>
            <p className="text-[11px] text-dim max-w-md">
              OAuth sign-in via Google. Client credentials are provisioned on the
              server.{" "}
              {googleConfigured
                ? "Client is configured."
                : "Set google_client_id / secret / redirect on the server to use this."}
            </p>
          </div>
          <Toggle
            checked={googleOn}
            onChange={setGoogleOn}
            disabled={busy || !googleConfigured}
            label="Enable Google sign-in"
            showState
          />
        </div>

        {/* shared session + auth knobs */}
        <div className="grid gap-3 sm:grid-cols-2 pt-4 border-t border-line mt-1">
          <Field label="Session length (hours)" hint="How long a sign-in lasts before it must be repeated. Applies to both local and Google logins.">
            <input
              className="field"
              type="number"
              min={1}
              max={720}
              value={ttlH}
              onChange={(e) => setTtlH(Math.max(1, Number(e.target.value) || 1))}
              disabled={busy}
            />
          </Field>
          <Field label="Default role" hint="Role granted to an authenticated user not named in the allowlist. 'deny' refuses anyone not explicitly listed. Above 'viewer' requires a pinned Workspace domain on the server.">
            <select
              className="field"
              value={defaultRole}
              onChange={(e) => setDefaultRole(e.target.value as PrincipalRole | "deny")}
              disabled={busy}
            >
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </Field>
        </div>

        {/* --------------------------------------------------- Google allowlist
            The Default role field above tells you what happens to "an
            authenticated user not named in the allowlist" — and until now there
            was no way to see or edit that allowlist from anywhere. It only
            governs Google sign-in (local accounts carry their own role), so it
            appears with Google. */}
        {googleOn && (
          <div className="py-3 border-t border-line">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="text-sm text-ink">Who may sign in with Google</div>
                <p className="text-[11px] text-dim max-w-md">
                  Each address gets exactly the role you give it here. Anyone who
                  signs in successfully but is not on this list gets the default
                  role above — which is <span className="text-ink">deny</span>{" "}
                  unless you changed it. Re-checked on every request, so removing
                  someone takes effect immediately.
                </p>
              </div>
              <button
                type="button"
                className="btn min-h-11 !px-3 text-[11px] shrink-0"
                disabled={busy}
                onClick={() => setAllowlist((p) => [...p, ["", "viewer"]])}
              >
                Add
              </button>
            </div>

            {allowlist.length === 0 ? (
              <p className="text-[11px] text-dim mt-2">
                Nobody listed. Every Google sign-in falls to the default role.
              </p>
            ) : (
              <div className="mt-3 flex flex-col gap-2">
                {allowlist.map(([email, role], i) => (
                  <div key={i} className="grid grid-cols-[1fr_7rem_2.75rem] gap-2 items-center">
                    <input
                      className="field"
                      type="email"
                      inputMode="email"
                      placeholder="name@example.com"
                      aria-label={`Allowed address ${i + 1}`}
                      value={email}
                      disabled={busy}
                      onChange={(e) =>
                        setAllowlist((p) =>
                          p.map((row, j) => (j === i ? [e.target.value, row[1]] : row)))
                      }
                    />
                    <select
                      className="field"
                      aria-label={`Role for ${email || `address ${i + 1}`}`}
                      value={role}
                      disabled={busy}
                      onChange={(e) =>
                        setAllowlist((p) =>
                          p.map((row, j) => (j === i ? [row[0], e.target.value] : row)))
                      }
                    >
                      {ROLES.filter((r) => r !== "deny").map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="btn min-h-11 !px-0 text-[11px]"
                      aria-label={`Remove ${email || `address ${i + 1}`}`}
                      disabled={busy}
                      onClick={() => setAllowlist((p) => p.filter((_, j) => j !== i))}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            )}
            {allowlist.some(([e]) => e.trim() && !e.includes("@")) && (
              <p className="text-[11px] text-warn mt-2">
                An entry without an @ can never match a Google account. Blank and
                malformed rows are dropped on save.
              </p>
            )}
          </div>
        )}

        <div className="flex items-center justify-between gap-3 py-3">
          <div className="min-w-0">
            <div className="text-sm text-ink">First-run admin setup</div>
            <p className="text-[11px] text-dim max-w-md">
              While local is on and no users exist, allow creating the first admin
              from the sign-in screen. Auto-closes once any account exists.
            </p>
          </div>
          <Toggle checked={firstRun} onChange={setFirstRun} disabled={busy} label="Allow first-run setup" showState />
        </div>

        {/* trust_loopback (G4) — test-mode knob for verifying role gating locally */}
        <div className="flex items-start justify-between gap-3 py-3 border-t border-line">
          <div className="min-w-0">
            <div className="text-sm text-ink">Trust this machine (loopback) as admin</div>
            <p className="text-[11px] text-dim max-w-md">
              With no sign-in method enabled, a browser on this same machine
              (127.0.0.1) gets admin automatically. To verify operator/viewer
              gating locally, enable local accounts above and sign in as a
              disposable user — that works regardless of this switch. Turn this
              OFF only as a strict mode: it removes the automatic admin fallback
              so an unauthenticated loopback client is denied like any other.
            </p>
          </div>
          <Toggle
            checked={trustLoopback}
            onChange={setTrustLoopback}
            disabled={busy}
            label="Trust loopback as admin"
            showState
          />
        </div>

        {/* LOUD open-server warning */}
        {openWarning && (
          <div className="flex items-start gap-3 border border-warn/50 bg-warn/10 px-3 py-2 text-xs mt-1">
            <Icon name="alert" size={14} className="text-warn shrink-0 mt-0.5" />
            <span className="text-ink">
              No method enabled — saving will OPEN the server: every client on the
              network becomes admin and the sign-in screen disappears.{" "}
              {adminTokenSet ? (
                <span className="text-dim">The break-glass admin token still works.</span>
              ) : null}
            </span>
          </div>
        )}

        {/* LOUD lockout warning: open server + loopback untrusted = no way back in
            for THIS browser (no session cookie exists under the open provider). */}
        {openWarning && !trustLoopback && (
          <div className="flex items-start gap-3 border border-bad/50 bg-bad/10 px-3 py-2 text-xs mt-1">
            <Icon name="alert" size={14} className="text-bad shrink-0 mt-0.5" />
            <span className="text-ink">
              This will also lock THIS browser out immediately on loopback — no
              method is enabled, so there is no session to fall back on. Recover
              by editing <span className="mono">trust_loopback</span> back to true
              in the server&apos;s config file and restarting, or with{" "}
              <span className="mono">python -m astrodeck create-admin</span>.
            </span>
          </div>
        )}

        {err && (
          <p className="text-xs text-bad inline-flex items-center gap-1.5 mt-3">
            <Icon name="alert" size={13} className="shrink-0" />
            {err}
          </p>
        )}

        <div className="flex items-center gap-3 mt-4">
          <button
            type="button"
            className={`btn min-h-[44px] sm:min-h-0 inline-flex items-center gap-2 ${openWarning ? "btn-danger" : "btn-accent"}`}
            onClick={save}
            disabled={busy}
          >
            <Icon name="check" size={15} />
            {busy ? "Saving…" : openWarning ? "Save (open the server)" : "Save methods"}
          </button>
          {savedAt && !busy && !err && (
            <span className="text-[11px] text-good inline-flex items-center gap-1.5">
              <Icon name="check" size={13} /> Saved
            </span>
          )}
        </div>
      </Panel>

      {/* break-glass token note — anti-lockout #1, always present */}
      <Panel title="Break-glass admin token">
        <p className="text-[11px] text-dim leading-relaxed max-w-xl">
          The <span className="mono text-ink">ASTRODECK_TOKEN</span> environment
          variable (or the stored admin token) ALWAYS grants admin, independent of
          the methods above. It is the recovery path if you are ever locked out —
          set it on the server and present it as a bearer token. You can also reseed
          a local admin from the server CLI:{" "}
          <span className="mono text-ink">python -m astrodeck create-admin &lt;username&gt;</span>.
        </p>
        <div className="mt-3 inline-flex items-center gap-2 text-[11px]">
          <Icon
            name={adminTokenSet ? "check" : "info"}
            size={14}
            className={adminTokenSet ? "text-good" : "text-dim"}
          />
          <span className={adminTokenSet ? "text-good" : "text-dim"}>
            {adminTokenSet
              ? "An admin token is configured on the server."
              : "No admin token is configured — the CLI and first-run setup remain as recovery paths."}
          </span>
        </div>
      </Panel>
    </div>
  );
}

// ------------------------------------------------------- I4 guided-card badge
// Shape (icon) + short numeral, never color alone — mirrors the rest of this
// file's idioms (the "Saved" check, the open/lockout alert icons).
function StepBadge({ n, state }: { n: number; state: "done" | "locked" | "active" }): JSX.Element {
  if (state === "done") {
    return (
      <span
        className="inline-flex items-center justify-center w-6 h-6 border border-good/50 text-good shrink-0 mt-0.5"
        aria-hidden
      >
        <Icon name="check" size={13} />
      </span>
    );
  }
  if (state === "locked") {
    return (
      <span
        className="inline-flex items-center justify-center w-6 h-6 border border-line2 text-faint shrink-0 mt-0.5"
        aria-hidden
      >
        <Icon name="lock" size={12} />
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center justify-center w-6 h-6 border border-line2 text-ink mono text-[11px] shrink-0 mt-0.5"
      aria-hidden
    >
      {n}
    </span>
  );
}
