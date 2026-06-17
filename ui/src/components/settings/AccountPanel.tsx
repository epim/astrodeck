// AccountPanel.tsx — the account / sign-in surface inside Settings (W2.5). Shows
// the resolved principal (role + email) and a "View-only" badge for viewers, and
// hosts the Google sign-in / sign-out affordance.
//
// The actual OAuth affordance is the shared <SignInButton/> (the same component the
// header mounts), so there's ONE source of truth for the sign-in/out behavior and
// its visibility rule (renders only when config.auth.provider === "google" &&
// google_configured). Under the default `none` provider every caller is admin and
// SignInButton renders nothing — this panel then explains the open-LAN posture.

import { type JSX } from "react";
import { usePrincipal, useConfig } from "../../store";
import { useIsViewer, usePrincipalRole } from "../../lib/caps";
import { Panel, EmptyState } from "../ui";
import { Icon } from "../icons";
import SignInButton from "../SignInButton";

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
  const googleReady = auth?.provider === "google" && auth.google_configured;
  // Under the `none` provider (or before config lands) every caller is admin on the
  // LAN — no sign-in to show.
  const openLan = !auth || auth.provider === "none";

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

        {/* The shared sign-in/out affordance (renders nothing unless Google is
            configured), plus the open-LAN explainer when there's no provider. */}
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <SignInButton />
          {openLan && (
            <p className="text-[11px] text-faint max-w-xl">
              Sign-in is disabled — this server trusts the local network and grants
              every client admin. Configure a Google provider on the server to gate
              by account.
            </p>
          )}
          {!openLan && !googleReady && (
            <p className="text-[11px] text-warn">
              A provider is set but Google isn't fully configured on the server, so
              sign-in is unavailable.
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
            hint="Mount, power, capture and configuration controls are hidden for your role. Sign in with an operator or admin account to take control."
          />
        </Panel>
      )}
    </div>
  );
}
