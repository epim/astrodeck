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
import type { AuthState, PrincipalRole } from "../../types";
import { setAuthConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { reconnectWs } from "../../ws";
import { Panel, Field, Toggle } from "../ui";
import { Icon } from "../icons";

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
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const seedKey = useMemo(
    () =>
      auth
        ? JSON.stringify([
            auth.methods,
            auth.session_ttl_s,
            auth.local_enabled_first_run,
            auth.trust_loopback,
            auth.default_role,
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
