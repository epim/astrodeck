// AccountSheet.tsx - Settings > USERS > "Signed in" (plan section C.7.1), and
// the tablet/desktop panel host for the SAME content the USERS screen renders
// inline (route table: "account ... renders inline; the sheet form is the
// tablet/desktop panel host").
//
// AccountPanel is mounted WHOLE and UNEDITED - it already renders the role
// chip, the identity line, the role blurb, sign-in/out and the open-LAN
// explainer. Its own copy still reads "Settings -> Auth", which is stale (the
// route is now Settings -> Users -> Sign-in methods); rather than edit the
// reused panel (out of scope, plan F.4), the section header above it reads
// SIGNED IN and the next section reads SIGN-IN METHODS (UsersScreen.tsx), so
// the pointer resolves visually without touching the panel's own string.
//
// Two more sentences render under the panel (GAP-ANALYSIS #1, plan C.7.1):
// the relay "signed in over the relay" note, and the loopback-trust note for
// the rig's own screen. Both read `deriveBase`/`location.hostname` LIVE at
// render time rather than the frozen `BASE` module singleton (`lib/base.ts`
// computes that once, at first import) - the sentence must be correct for
// whatever origin the page is actually on, not whichever origin happened to
// be current the first time any module touched `lib/base.ts`.
import type { JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Sheet } from "../../../ui";
import { useConfig } from "../../../../store";
import { deriveBase } from "../../../../lib/base";
import AccountPanel from "../../../../components/settings/AccountPanel";

const LOOPBACK_HOSTS = ["localhost", "127.0.0.1", "[::1]"];

const NOTE_STYLE = {
  margin: "6px 0 0",
  fontSize: 11.5,
  lineHeight: 1.45,
  color: "var(--text-faint)",
} as const;

/** The identity panel plus the relay/loopback notes, shared verbatim between
 *  the USERS screen (inline, `UsersScreen.tsx`) and this sheet (the
 *  tablet/desktop panel host, and anywhere else that deep-links
 *  `#/settings/users/account`). */
export function AccountBody(): JSX.Element {
  const config = useConfig();
  const pathname = typeof window === "undefined" ? "/" : window.location.pathname;
  const hostname = typeof window === "undefined" ? "" : window.location.hostname;
  const isRelay = deriveBase(pathname) !== "";
  const isLoopback = LOOPBACK_HOSTS.includes(hostname);
  const trustLoopback = config?.auth?.trust_loopback ?? true;

  return (
    <div data-testid="account-body">
      <AccountPanel />
      {isRelay && (
        <p style={NOTE_STYLE} data-testid="relay-note">
          You are signed in over the relay. This session belongs to this
          browser; signing out here does not sign out the screen on the rig.
        </p>
      )}
      {isLoopback && trustLoopback && (
        <p style={NOTE_STYLE} data-testid="loopback-note">
          This is the rig&apos;s own screen. With no sign-in method enabled it
          is trusted as admin automatically (loopback trust).
        </p>
      )}
    </div>
  );
}

export function AccountSheet(_p: SheetProps): JSX.Element {
  return (
    <Sheet
      data-testid="settings-account"
      title="SIGNED IN"
      icon={<NxIcon name="lock" />}
      onBack={nav.back}
    >
      <AccountBody />
    </Sheet>
  );
}
