// AccountPanel.tsx — the account / sign-in surface inside Settings (W2.5). Shows
// the resolved principal (role + email) and a "View-only" badge for viewers, and
// hosts the Google sign-in / sign-out affordance.
//
// The actual OAuth affordance is the shared <SignInButton/> (the same component the
// header mounts), so there's ONE source of truth for the sign-in/out behavior and
// its visibility rule (renders only when config.auth.provider === "google" &&
// google_configured). Under the default `none` provider every caller is admin and
// SignInButton renders nothing — this panel then explains the open-LAN posture.

import { useState, type JSX } from "react";
import { usePrincipal, useConfig, useStore } from "../../store";
import { useIsViewer, usePrincipalRole } from "../../lib/caps";
import { Panel, EmptyState } from "../ui";
import { Icon } from "../icons";
import SignInButton from "../SignInButton";
import { logout } from "../../api/backends";

const ROLE_BLURB: Record<string, string> = {
  admin: "Full control — connect rigs, configure safety, drive the mount and power.",
  operator: "Can capture and guide, but not change backends, safety, or mount/power.",
  viewer: "Read-only. You can watch status and previews; controls are hidden.",
};

export default function AccountPanel(): JSX.Element {
  const principal = usePrincipal();
  const config = useConfig();
  const role = usePrincipalRole();
  const isViewer = useIsViewer();

  const auth = config?.auth;
  // Multi-method aware (W2.6): the source of truth is `methods`. Empty ⇒ open LAN.
  const methods = auth?.methods ?? [];
  const googleReady = methods.includes("google") && !!auth?.google_configured;
  const localReady = methods.includes("local");
  // No method enabled (or before config lands) ⇒ every caller is admin on the LAN.
  const openLan = !auth || methods.length === 0;

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Account">
        <div className="flex items-center gap-3 mb-3">
          <span
            className={`inline-flex items-center justify-center w-10 h-10 border
              ${isViewer ? "border-line2 text-dim" : "border-accent text-accent"}`}
            aria-hidden
          >
            <Icon name={isViewer ? "eye" : "user"} size={18} />
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-display text-sm tracking-wide text-ink uppercase">
                {role}
              </span>
              {isViewer && (
                <span className="mono text-[9px] tracking-[0.18em] uppercase text-warn border border-warn/50 px-1.5 py-0.5">
                  View-only
                </span>
              )}
            </div>
            <div className="text-[11px] text-dim truncate">
              {principal?.email ??
                (openLan ? "Local network — no sign-in required" : "Not signed in")}
            </div>
          </div>
        </div>

        <p className="text-[11px] text-dim leading-relaxed max-w-xl">
          {ROLE_BLURB[role] ?? ""}
        </p>

        {/* The shared Google sign-in/out affordance (renders nothing unless Google
            is configured), a generic sign-out for a signed-in local user, plus the
            open-LAN explainer when no method is enabled. */}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <SignInButton />
          {/* Local accounts mint the SAME ad_session cookie as Google; the Google
              SignInButton only shows its sign-out for google. Offer a generic
              sign-out when a local user is signed in (has an identity, local-on). */}
          {!googleReady && localReady && !!principal?.email && <LocalSignOut />}
          {openLan && (
            <p className="text-[11px] text-faint max-w-xl">
              Sign-in is disabled — this server trusts the local network and grants
              every client admin. Enable a sign-in method under Settings → Auth to
              gate by account.
            </p>
          )}
          {!openLan && !googleReady && !localReady && (
            <p className="text-[11px] text-warn">
              A sign-in method is selected but isn't fully configured on the server,
              so sign-in is unavailable.
            </p>
          )}
        </div>
      </Panel>

      {/* Viewer read-only explainer — makes the hidden-controls behavior legible
          (W2.5: controls are HIDDEN/disabled, never 403-on-tap). */}
      {isViewer && (
        <Panel title="Read-only session">
          <EmptyState
            icon="lock"
            title="You're viewing in read-only mode"
            hint="Mount, power, capture and configuration controls are locked for your role. Sign in as an operator for capture and guiding, or as an admin for full control."
          />
        </Panel>
      )}
    </div>
  );
}

// Generic sign-out for a signed-in LOCAL user (the Google SignInButton owns its
// own sign-out). POST /auth/logout clears the cookie + revokes the jti; we then
// re-resolve identity + the login signal so the role chip flips and (if a method
// is enabled) App re-raises the Login gate.
function LocalSignOut(): JSX.Element {
  const [busy, setBusy] = useState(false);
  const showToast = useStore((s) => s.showToast);
  const onSignOut = async () => {
    setBusy(true);
    try {
      await logout();
      const st = useStore.getState();
      await Promise.all([st.loadPrincipal(), st.loadAuthMethods(), st.loadConfig()]);
    } catch (e) {
      showToast("error", (e as Error).message || "Sign-out failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <button
      type="button"
      className="btn !py-1 !px-2.5 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
      onClick={onSignOut}
      disabled={busy}
    >
      <Icon name="logout" size={14} />
      <span>{busy ? "Signing out…" : "Sign out"}</span>
    </button>
  );
}
