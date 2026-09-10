// AccountIdentity.tsx - SIGNED IN, rebuilt in the design's vocabulary (wave R7,
// T-R7-11; plan section 3.F6). Replaces `components/settings/AccountPanel.tsx`
// at its one mount inside the new UI, `settings/sheets/AccountSheet.tsx` (which
// the USERS screen also renders inline). The legacy file is untouched and still
// serves `#/classic`.
//
// ONE SIGN-OUT, NOT TWO. The legacy panel mounted `components/SignInButton`
// (which renders its own sign-out, but only when the LEGACY single-provider
// field says `provider === "google"`) and then added a second, separate
// `LocalSignOut` for the case that button refused to cover. Two components
// deciding whether you can sign out, from two different fields, is how a
// signed-in local user under a rig that also has Google configured ends up with
// no sign-out at all. Here there is one rule: an identity exists (the principal
// carries an email), so it can be ended. `POST /auth/logout` clears the same
// cookie whichever way it was minted.
//
// Sign-IN stays a full-page REDIRECT, not a fetch: the server 302s to Google,
// sets an HttpOnly cookie on the callback and comes back. It is offered only
// when Google is an ENABLED method AND the client credentials are configured -
// otherwise the button would lead somewhere that cannot complete.

import { useState, type JSX } from "react";
import { logout } from "../../../../../api/backends";
import { u } from "../../../../../lib/base";
import { ROLE_DESCRIPTIONS, useIsViewer, usePrincipalRole } from "../../../../../lib/caps";
import { useConfig, usePrincipal, useStore } from "../../../../../store";
import { ActionButton, Card, EmptyCard, Mono } from "../../../../ui";
import { EyeGlyph, PersonGlyph, SignOutGlyph } from "./glyphs";
import { Note, Section } from "./PeopleSection";
import {
  ACCOUNT_EYEBROW, METHOD_BROKEN_BLURB, NOT_SIGNED_IN, OPEN_LAN_BLURB, OPEN_LAN_LINE,
  READ_ONLY_HEAD, READ_ONLY_HINT, READ_ONLY_TITLE, SIGN_IN_GOOGLE, SIGN_OUT, SIGN_OUT_BUSY,
  SIGN_OUT_FAILED, VIEW_ONLY_BADGE,
} from "./peopleModel";

export function AccountIdentity(): JSX.Element {
  const principal = usePrincipal();
  const config = useConfig();
  const role = usePrincipalRole();
  const isViewer = useIsViewer();
  const showToast = useStore((s) => s.showToast);
  const [busy, setBusy] = useState(false);

  const auth = config?.auth;
  // `methods` is the source of truth (W2.6); the legacy `provider` field is a
  // migration leftover. Empty methods means the open LAN.
  const methods = auth?.methods ?? [];
  const googleReady = methods.includes("google") && !!auth?.google_configured;
  const localReady = methods.includes("local");
  const openLan = !auth || methods.length === 0;
  const signedIn = !!principal?.email;

  const onSignOut = async () => {
    setBusy(true);
    try {
      await logout();
      const st = useStore.getState();
      // Re-resolve identity, the login signal and the redacted config together,
      // so the role chip flips and (if a method is on) the Login gate raises
      // without a reload.
      await Promise.all([st.loadPrincipal(), st.loadAuthMethods(), st.loadConfig()]);
    } catch (e) {
      showToast("error", (e as Error).message || SIGN_OUT_FAILED, { verbatim: true });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section eyebrow={ACCOUNT_EYEBROW} data-testid="account-identity">
      <Card>
        <div className="nx-people-id">
          <span className="nx-people-idtile" data-viewer={isViewer ? "true" : "false"} aria-hidden="true">
            {isViewer ? <EyeGlyph size={20} /> : <PersonGlyph size={20} />}
          </span>
          <div className="nx-people-idtext">
            <div className="nx-people-idrole">
              <span className="nx-people-rolelabel">{role.toUpperCase()}</span>
              {isViewer && <span className="nx-people-badge" data-tone="warn">{VIEW_ONLY_BADGE}</span>}
            </div>
            <div className="nx-people-idmail" data-testid="account-identity-line">
              {principal?.email ?? (openLan ? OPEN_LAN_LINE : NOT_SIGNED_IN)}
            </div>
          </div>
        </div>

        <Note>{ROLE_DESCRIPTIONS[role] ?? ""}</Note>

        <div className="nx-people-actions">
          {signedIn && (
            <ActionButton
              kind="secondary"
              glyph={<SignOutGlyph />}
              onPress={() => { void onSignOut(); }}
              busy={busy}
              data-testid="account-signout"
            >
              {busy ? SIGN_OUT_BUSY : SIGN_OUT}
            </ActionButton>
          )}
          {!signedIn && googleReady && (
            <ActionButton
              kind="primary"
              onPress={() => { window.location.href = u("/auth/login"); }}
              data-testid="account-signin-google"
            >
              {SIGN_IN_GOOGLE}
            </ActionButton>
          )}
        </div>

        {openLan && <Note data-testid="account-openlan">{OPEN_LAN_BLURB}</Note>}
        {!openLan && !googleReady && !localReady && (
          <Note tone="warn" data-testid="account-method-broken">{METHOD_BROKEN_BLURB}</Note>
        )}
      </Card>

      {isViewer && (
        <>
          <Mono size={11} data-testid="account-readonly-title">{READ_ONLY_TITLE}</Mono>
          <EmptyCard title={READ_ONLY_HEAD} hint={READ_ONLY_HINT} data-testid="account-readonly" />
        </>
      )}
    </Section>
  );
}
