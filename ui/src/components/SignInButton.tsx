// SignInButton.tsx — the Google sign-in / sign-out affordance (W2.5).
//
// VISIBILITY RULE (hard): this renders ONLY when a Google auth provider is
// actually configured — `config.auth.provider === "google" && google_configured`.
// Under the default `provider: "none"` the server makes every caller admin, so
// there is no identity to show and NO login UI appears — the LAN tablet is
// unchanged. (When `config.auth` is absent — e.g. the WS `hello` bootstrap config
// that omits it — we also render nothing until the first `config` event lands it.)
//
// Sign-IN is a BROWSER REDIRECT, not a fetch: the server's /auth/login 302s to
// Google, sets an HttpOnly cookie on callback, and redirects back. Sign-OUT is a
// POST (clears cookie + revokes the jti); after it we re-resolve /api/me + config
// so the role chip + gates flip to viewer without a reload.

import { useState } from "react";
import { Icon } from "./icons";
import { useStore, useConfig, usePrincipal } from "../store";
import { logout } from "../api/backends";
import { u } from "../lib/base";

export default function SignInButton() {
  const config = useConfig();
  const principal = usePrincipal();
  const loadPrincipal = useStore((s) => s.loadPrincipal);
  const loadConfig = useStore((s) => s.loadConfig);
  const showToast = useStore((s) => s.showToast);
  const [busy, setBusy] = useState(false);

  const auth = config?.auth;
  // Gate the WHOLE affordance: no Google provider configured => render nothing.
  if (!auth || auth.provider !== "google" || !auth.google_configured) return null;

  // A resolved principal with an email is "signed in"; a viewer sentinel (or a
  // null/empty email under Google) means anonymous — offer sign-in.
  const email = principal?.email ?? null;
  const signedIn = !!email;

  const onSignIn = () => {
    // Full-page redirect through the server OAuth dance (NOT a fetch).
    window.location.href = u("/auth/login");
  };

  const onSignOut = async () => {
    setBusy(true);
    try {
      await logout();
      // Re-resolve identity + redacted config so the role chip and every cap gate
      // flip to the post-logout (viewer/anon) posture immediately.
      await Promise.all([loadPrincipal(), loadConfig()]);
    } catch (e) {
      showToast("error", (e as Error).message || "Sign-out failed");
    } finally {
      setBusy(false);
    }
  };

  if (signedIn) {
    return (
      <span className="inline-flex items-center gap-2 min-w-0">
        <span className="inline-flex items-center gap-1.5 text-xs text-dim min-w-0">
          <Icon name="user" size={14} className="shrink-0" />
          <span className="truncate max-w-[180px]" title={email!}>{email}</span>
        </span>
        <button
          type="button"
          className="btn !py-1 !px-2.5 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
          onClick={onSignOut}
          disabled={busy}
        >
          <Icon name="logout" size={14} />
          <span>{busy ? "Signing out…" : "Sign out"}</span>
        </button>
      </span>
    );
  }

  return (
    <button
      type="button"
      className="btn btn-accent !py-1 !px-3 text-[10px] min-h-[44px] sm:min-h-0 inline-flex items-center gap-1.5"
      onClick={onSignIn}
    >
      <Icon name="user" size={14} />
      Sign in with Google
    </button>
  );
}
