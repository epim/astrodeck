// peopleModel.ts - every string and every decision the three PEOPLE editors
// share, with no React and no store in the file (wave R7, T-R7-11; plan
// sections 3.F4, 3.F5, 3.F6).
//
// It exists for two reasons.
//
// 1. THE ROLE LIST IS NOT A LITERAL. `components/settings/UsersPanel.tsx:40`
//    and `AuthMethodPanel.tsx:29` each declare their own
//    `["viewer","syncer","operator","admin"]`. Two hand-written copies of the
//    server's role table is two chances to disagree with it, and the disagreement
//    is silent: a role added to `lib/caps.ts` would simply never appear in
//    either picker, and an admin would be unable to grant a role the server
//    already honours. `PRINCIPAL_ROLES` is derived from `ROLE_DESCRIPTIONS`,
//    which is `Record<PrincipalRole, string>` - the same total keyspace
//    `ROLE_CAPS` is keyed by, so the compiler refuses to let either fall behind
//    `PrincipalRole`.
//
// 2. THE COPY IS THE FEATURE. These panels are the only place the product
//    explains what a role can do, what "no method enabled" costs, and what a
//    break-glass token is for. Keeping the sentences here means the editor
//    files read as layout and the sentences can be diffed against the legacy
//    panels row by row.
//
// Legacy copy carried em-dashes throughout; every sentence below uses hyphens
// (ARCHITECTURE.md non-negotiable 5).

import { ROLE_DESCRIPTIONS, accessPhrase } from "../../../../../lib/caps";
import { ApiError } from "../../../../../api";
import type { AuthState, PrincipalRole } from "../../../../../types";

// ---------------------------------------------------------------- the roles

/** Every principal role the app knows about, in ascending privilege order.
 *
 *  Derived, never typed out: `ROLE_DESCRIPTIONS` is declared in `lib/caps.ts`
 *  immediately above `ROLE_CAPS` and shares its `Record<PrincipalRole, ...>`
 *  type, so the two hold exactly the same keys and TypeScript fails the build
 *  if a new role reaches `PrincipalRole` without reaching both. */
export const PRINCIPAL_ROLES: readonly PrincipalRole[] =
  Object.keys(ROLE_DESCRIPTIONS) as PrincipalRole[];

/** `default_role` on the wire is `PrincipalRole | null`; `null` is the refusal
 *  ("nobody who is not on the allowlist gets in"), which the picker has to be
 *  able to express. It is NOT a role - it is the absence of one - so it is
 *  carried as its own value rather than smuggled into the role list. */
export const DENY = "deny" as const;
export type DefaultRole = PrincipalRole | typeof DENY;

/** The default-role picker's options: the refusal, then every real role. */
export const DEFAULT_ROLE_VALUES: readonly DefaultRole[] = [DENY, ...PRINCIPAL_ROLES];

export function roleWord(role: DefaultRole): string {
  return role.toUpperCase();
}

/** What each option in the default-role picker actually does, as a sub-label.
 *  `deny` is the only one that is not simply `ROLE_DESCRIPTIONS`. */
export function defaultRoleBlurb(role: DefaultRole): string {
  return role === DENY
    ? "Refuses anyone who is not named on the allowlist below."
    : ROLE_DESCRIPTIONS[role];
}

// ---------------------------------------------------------------- lock copy

/** The one sentence every locked control in this area states. `admin.users` is
 *  the capability the server enforces on all of `/api/users`, `/api/auth/config`
 *  and the user list itself. */
export const PEOPLE_CAP = "admin.users" as const;
export const PEOPLE_LOCK_SENTENCE = `Managing people and sign-in needs ${accessPhrase(PEOPLE_CAP)}.`;
export const PEOPLE_LIST_HIDDEN_TITLE = "PEOPLE LIST HIDDEN";
export const PEOPLE_LIST_HIDDEN_HINT = `The people list needs ${accessPhrase(PEOPLE_CAP)}. Nothing was requested from the rig.`;
export const METHODS_HIDDEN_TITLE = "SIGN-IN METHODS HIDDEN";
export const METHODS_HIDDEN_HINT = `Reading the sign-in configuration needs ${accessPhrase(PEOPLE_CAP)}. Nothing was requested from the rig.`;

// ------------------------------------------------------------- error mapping

/** The four statuses these routes actually return, each said as the thing that
 *  happened rather than as a number. Verbatim in meaning from
 *  `UsersPanel.tsx:42-51`; 409 keeps the server's own message because that is
 *  where "last admin" and "duplicate username" are told apart. */
export function errText(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return e.message || "That change conflicts with what the rig already has.";
    if (e.status === 422) return "Password is too long (max 72 bytes).";
    if (e.status === 404) return "That account no longer exists.";
    return e.message || fallback;
  }
  return fallback;
}

// ------------------------------------------------------------ UsersEditor copy

export const USERS_EYEBROW = "PEOPLE";
export const USERS_INTRO =
  "Local accounts stored on this rig. A role decides what each person may do; disabling an account signs them out and keeps the account.";
export const USERS_ADD = "ADD USER";
export const USERS_ADD_CANCEL = "CANCEL";
export const USERS_LOADING = "Reading the account list from the rig";
export const USERS_EMPTY_TITLE = "NO USERS YET";
export const USERS_EMPTY_HINT =
  "Add a local account so people can sign in to control the rig. Until one exists, sign-in cannot be turned on.";
export const USERS_LOAD_FAILED = "Could not load the account list.";
export const USERS_ROLE_FAILED = "Could not change that role.";
export const USERS_STATUS_FAILED = "Could not change that account's status.";
export const USERS_DELETE_FAILED = "Could not delete that account.";
export const USERS_RESET_FAILED = "Could not reset that password.";
export const USERS_CREATE_FAILED = "Could not create the account.";
export const PASSWORD_TOO_LONG = "Password is too long (max 72 bytes).";

export const YOU_BADGE = "YOU";
export const DISABLED_BADGE = "DISABLED";
export const NO_EMAIL = "no email on this account";

/** The three confirms, kept together so it is obvious there are exactly three
 *  and what each one is protecting against. All three are `mode: "hold"` - the
 *  house pattern for a press that fires a destructive action. */
export const CONFIRM_SELF_DEMOTE = {
  title: "Remove your own admin access?",
  body:
    "Changing your own role away from admin drops your access to People and sign-in methods and makes this session read-only. Another admin, or the server CLI, would be needed to restore it.",
  confirmLabel: "Change my role",
} as const;

export const CONFIRM_SELF_DISABLE = {
  title: "Disable your own account?",
  body:
    "This signs you out and removes your access. Another admin, or the server CLI, would be needed to re-enable you.",
  confirmLabel: "Disable my account",
} as const;

export function confirmDelete(username: string): { title: string; body: string; confirmLabel: string } {
  return {
    title: `Delete "${username}"?`,
    body: "This permanently removes the account. They are signed out and can no longer sign in.",
    confirmLabel: "Delete user",
  };
}

// ------------------------------------------------------- AuthMethodsEditor copy

export const AUTH_EYEBROW = "SIGN-IN METHODS";
export const AUTH_INTRO =
  "Choose how people sign in. Both methods can be on at once. With every method off, the server trusts the local network and grants every client admin, and no sign-in screen is shown.";
export const AUTH_LOADING = "Reading the sign-in configuration from the rig";

export const LOCAL_TITLE = "Local username and password";
export const LOCAL_LABEL = "Enable local accounts";
export const LOCAL_BLURB =
  "Offline accounts stored on the rig, so a phone or tablet can sign in with no internet. Add them under PEOPLE.";

export const GOOGLE_TITLE = "Google sign-in";
export const GOOGLE_LABEL = "Enable Google sign-in";
export const GOOGLE_BLURB_READY =
  "OAuth sign-in through Google. The client credentials are provisioned on the server and are already set.";
export const GOOGLE_BLURB_UNSET =
  "OAuth sign-in through Google. Set google_client_id, google_client_secret and google_redirect_uri on the server before this can be turned on.";
export const GOOGLE_NOT_CONFIGURED = "NOT CONFIGURED";
export const GOOGLE_LOCK_REASON =
  "Google client credentials are not set on the server, so this method would have nothing to sign in against.";

export const TTL_LABEL = "Session length (hours)";
export const TTL_HINT =
  "How long a sign-in lasts before it has to be repeated. Applies to local and Google alike.";
export const TTL_ARIA = "Session length, hours";
export const TTL_MIN = 1;
export const TTL_MAX = 720;
export const DEFAULT_TTL_S = 28800;

export const DEFAULT_ROLE_LABEL = "Default role";
export const DEFAULT_ROLE_HINT =
  "What an authenticated person gets when their address is not on the allowlist. Anything above viewer also needs a pinned Google Workspace domain on the server.";

export const FIRST_RUN_TITLE = "First-run admin setup";
export const FIRST_RUN_LABEL = "Allow first-run setup";
export const FIRST_RUN_BLURB =
  "While local sign-in is on and no accounts exist, let the first admin be created from the sign-in screen. It closes itself once any account exists.";

export const LOOPBACK_TITLE = "Trust this machine (loopback) as admin";
export const LOOPBACK_LABEL = "Trust loopback as admin";
export const LOOPBACK_BLURB =
  "With no sign-in method on, a browser on the rig itself (127.0.0.1) gets admin automatically. Turn it off as a strict mode: an unauthenticated loopback client is then refused like any other.";

export const ALLOWLIST_TITLE = "Who may sign in with Google";
export const ALLOWLIST_BLURB =
  "Each address gets exactly the role set here. Anyone who signs in but is not listed falls to the default role above. Re-checked on every request, so removing someone takes effect at once.";
export const ALLOWLIST_EMPTY = "Nobody listed. Every Google sign-in falls to the default role.";
export const ALLOWLIST_ADD = "ADD ADDRESS";
export const ALLOWLIST_MALFORMED =
  "An entry without an @ can never match a Google account. Blank and malformed rows are dropped when this is saved.";

export const SAVE_LABEL = "SAVE METHODS";
export const SAVE_OPEN_LABEL = "SAVE AND OPEN THE SERVER";
export const SAVE_FAILED = "Could not save the sign-in configuration.";
export const DIRTY_NOTE = "Unsaved - these switches are not on the rig yet";
export const SAVED_NOTE = "Saved";
export const AUTH_ENABLED_TOAST = "Authentication enabled - every client must now sign in.";

export const OPEN_WARNING =
  "No method is enabled. Saving OPENS the server: every client on the network becomes admin and the sign-in screen disappears.";
export const OPEN_WARNING_TOKEN = "The break-glass admin token still works.";
export const LOCKOUT_WARNING =
  "This also locks THIS browser out immediately on loopback: no method is enabled, so there is no session to fall back on. Recover by setting trust_loopback back to true in the server's config file and restarting, or with python -m astrodeck create-admin.";

// ------------------------------------------------------------- break-glass

export const BREAKGLASS_SUMMARY = "BREAK-GLASS ADMIN TOKEN";
export const BREAKGLASS_SET = "SET ON THE RIG";
export const BREAKGLASS_UNSET = "NOT SET";
export const BREAKGLASS_BLURB =
  "A bearer token that always grants admin, whatever the methods above say. It is the recovery path if you are ever locked out: present it as an Authorization: Bearer header, or as ASTRODECK_TOKEN on the server. A local admin can also be reseeded with python -m astrodeck create-admin <username>.";
export const BREAKGLASS_LABEL = "New break-glass token";
export const BREAKGLASS_ARIA = "New break-glass admin token (write-only)";
export const BREAKGLASS_HINT_SET =
  "A token is already stored. Leave this empty to keep it; type a new one to replace it.";
export const BREAKGLASS_HINT_UNSET =
  "Sent once and never read back: the rig stores it and the app can never show it again.";
export const BREAKGLASS_PLACEHOLDER = "leave empty to keep the stored token";
export const BREAKGLASS_OPEN_WARNING =
  "With no sign-in method enabled, saving a token makes that token the ONLY way in: this browser and every other client is refused until it presents the token.";

/** The server refuses a bearer credential below 32 bytes outright
 *  (`server/astrodeck/config.py:_validate_bearer_token`, MIN_BEARER_TOKEN_BYTES),
 *  and `TokenAdminProvider` fails closed on one that slipped in earlier. Saying
 *  so here turns a 400 into a sentence before the round trip. */
export const MIN_TOKEN_BYTES = 32;

export function tokenBytes(raw: string): number {
  return new TextEncoder().encode(raw.trim()).length;
}

/** Why this token cannot be saved yet, or null. Empty is always fine - it means
 *  "leave the stored one alone". */
export function breakGlassBlocker(raw: string): string | null {
  const n = tokenBytes(raw);
  if (n === 0) return null;
  if (n < MIN_TOKEN_BYTES) {
    return `A break-glass token needs at least ${MIN_TOKEN_BYTES} bytes of secret material; this one is ${n}. The rig refuses anything shorter.`;
  }
  return null;
}

// ------------------------------------------------------------- setup card

export const SETUP_TITLE = "SECURE THIS SERVER";
export const SETUP_INTRO =
  "This server is open right now: every client on the network is admin and there is no sign-in screen. Follow these steps, in order, to lock it down.";
export const SETUP_DISMISS = "Dismiss setup guide";
export const SETUP_REOPEN = "Setup guide";
export const SETUP_STEP1 = "Create an admin account";
export const SETUP_STEP1_OPEN =
  "This is the account you will sign in as once local sign-in is on. Create it here, or from PEOPLE at any time.";
export const SETUP_STEP1_DONE = "An enabled admin account exists. Manage accounts from PEOPLE at any time.";
export const SETUP_STEP2 = "Enable local sign-in";
export const SETUP_STEP2_LOCKED =
  "Unlocks once step 1's admin account is confirmed enabled - creating a user alone does not turn sign-in on.";
export const SETUP_STEP2_ARMED = "Armed. Press SAVE METHODS below to finish.";
export const SETUP_STEP2_OPEN = "Turns on the local username and password method below.";
export const SETUP_STEP2_BTN = "ENABLE LOCAL SIGN-IN";
export const SETUP_STEP2_BTN_DONE = "ENABLED - SAVE BELOW";
export const SETUP_STEP2_LOCK_REASON =
  "Create and enable an admin account first, or saving would turn on a sign-in screen nobody can get through.";
export const SETUP_STEP2_ON_REASON = "Local sign-in is already armed; press SAVE METHODS to persist it.";
export const SETUP_STEP3 = "Saving signs you out too";
export const SETUP_STEP3_BLURB =
  "Saving signs every client out, including this one. You land on the sign-in page and sign in with the account from step 1.";

// ------------------------------------------------------------ AddUserForm copy

export const ADD_USERNAME = "Username";
export const ADD_EMAIL = "Email";
export const ADD_METHOD = "Sign-in method";
export const ADD_PASSWORD = "Password";
export const ADD_ROLE = "Role";
export const ADD_SUBMIT = "CREATE USER";
export const ADD_SUBMIT_BUSY = "CREATING";
export const METHOD_PASSWORD = "Password";
export const METHOD_GOOGLE = "Google only";
export const GOOGLE_ONLY_PASSWORD_NOTE =
  "Not set - Google does the authenticating. Nothing is stored here that could be stolen.";
export const EMAIL_REQUIRED = "Email is required.";
export const EMAIL_INVALID = "Enter a valid email address.";

export function createdLine(username: string, role: PrincipalRole): string {
  return `Created "${username}" as ${role}.`;
}

// ----------------------------------------------------------- AccountIdentity

export const ACCOUNT_EYEBROW = "SIGNED IN";
export const VIEW_ONLY_BADGE = "VIEW-ONLY";
export const SIGN_OUT = "SIGN OUT";
export const SIGN_OUT_BUSY = "SIGNING OUT";
export const SIGN_IN_GOOGLE = "SIGN IN WITH GOOGLE";
export const SIGN_OUT_FAILED = "Sign-out failed.";
export const OPEN_LAN_LINE = "Local network - no sign-in required";
export const NOT_SIGNED_IN = "Not signed in";
export const OPEN_LAN_BLURB =
  "Sign-in is off, so this server trusts the local network and grants every client admin. Turn on a method under SIGN-IN METHODS to gate by account.";
export const METHOD_BROKEN_BLURB =
  "A sign-in method is selected but is not fully configured on the server, so nobody can actually sign in.";
export const READ_ONLY_TITLE = "Read-only session";
export const READ_ONLY_HEAD = "You're viewing in read-only mode";
export const READ_ONLY_HINT =
  "Mount, power, capture and configuration controls are locked for your role. Sign in as an operator for capture and guiding, or as an admin for full control.";

// ---------------------------------------------------------------- auth draft

/** The editable half of `config.auth`. Everything else in the block is echoed
 *  back untouched on save (secrets arrive blank and blank means unchanged -
 *  `server/astrodeck/api/app.py:_preserve_auth_secrets`). */
export interface AuthDraft {
  localOn: boolean;
  googleOn: boolean;
  ttlH: number;
  firstRun: boolean;
  trustLoopback: boolean;
  defaultRole: DefaultRole;
  /** ORDERED PAIRS, not the `Record` it is on the wire: two rows mid-edit can
   *  transiently share an empty key, and an object would silently merge them
   *  (or reorder rows as the key changes under the cursor). */
  allowlist: [string, string][];
}

export function ttlHoursOf(auth: AuthState | null | undefined): number {
  return Math.max(TTL_MIN, Math.round((auth?.session_ttl_s ?? DEFAULT_TTL_S) / 3600));
}

/** The draft the server currently holds, i.e. what "not dirty" means. */
export function draftOf(auth: AuthState): AuthDraft {
  const methods = auth.methods ?? [];
  return {
    localOn: methods.includes("local"),
    googleOn: methods.includes("google"),
    ttlH: ttlHoursOf(auth),
    firstRun: auth.local_enabled_first_run ?? true,
    trustLoopback: auth.trust_loopback ?? true,
    defaultRole: (auth.default_role as PrincipalRole | null) ?? DENY,
    allowlist: Object.entries(auth.role_allowlist ?? {}),
  };
}

export function methodsOf(d: AuthDraft): string[] {
  return [...(d.localOn ? ["local"] : []), ...(d.googleOn ? ["google"] : [])];
}

/** Whether the draft differs from what the rig holds. Order-independent on
 *  `methods` (the server may return them in either order) and exact on the
 *  allowlist rows, which ARE ordered in the editor. */
export function authDirty(d: AuthDraft, auth: AuthState, tokenDraft: string): boolean {
  if (tokenBytes(tokenDraft) > 0) return true;
  const seed = draftOf(auth);
  const sorted = (m: string[]) => [...m].sort().join(",");
  return (
    sorted(methodsOf(d)) !== sorted(methodsOf(seed)) ||
    d.ttlH !== seed.ttlH ||
    d.firstRun !== seed.firstRun ||
    d.trustLoopback !== seed.trustLoopback ||
    d.defaultRole !== seed.defaultRole ||
    JSON.stringify(d.allowlist) !== JSON.stringify(seed.allowlist)
  );
}

/** The allowlist as the server wants it: trimmed, lower-cased, and with every
 *  row that could never match a Google account dropped rather than persisted as
 *  access somebody appears to have. Last write wins on a duplicate address. */
export function allowlistBody(rows: [string, string][]): Record<string, string> {
  return Object.fromEntries(
    rows
      .map(([e, r]) => [e.trim().toLowerCase(), r] as [string, string])
      .filter(([e]) => e.includes("@")),
  );
}

export function hasMalformedAddress(rows: [string, string][]): boolean {
  return rows.some(([e]) => e.trim() !== "" && !e.includes("@"));
}
