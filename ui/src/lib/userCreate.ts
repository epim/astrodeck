// userCreate.ts — the decision layer behind "add a user".
//
// The account form used to demand a password, full stop. But this rig can sign
// people in two ways, and only one of them involves a password we store:
//
//   * a LOCAL password, which we hash and keep;
//   * Google, where the identity provider does the authenticating and the only
//     thing we hold is the email that identifies them.
//
// Forcing a password on someone who will only ever arrive through Google means
// inventing a credential nobody uses and everybody could leak. So the form asks
// WHICH, and the answer decides whether a password field exists at all.
//
// Email is mandatory in both cases, because it is the identity: it is what the
// Google assertion is matched against, and without it a Google-only account can
// never be linked to a real person.
//
// The backend already supports this — auth/users.py create() stores an empty
// password_hash, verify() explicitly refuses to authenticate against one, and
// `can_sign_in_locally` reports which accounts have a usable password.

/** How a new account will authenticate. */
export type SignInMethod = "password" | "google";

export interface NewUserDraft {
  username: string;
  email: string;
  password: string;
  method: SignInMethod;
}

/** bcrypt truncates past 72 BYTES, so the limit is bytes, not characters. */
export const MAX_PASSWORD_BYTES = 72;

export function passwordTooLong(pw: string): boolean {
  return new TextEncoder().encode(pw).length > MAX_PASSWORD_BYTES;
}

/** Shape check only — enough to catch a typo, not pretending to be RFC 5322. */
export function emailLooksValid(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());
}

/**
 * Why this draft cannot be submitted yet, or null when it can.
 *
 * Returns the SENTENCE, never a bare boolean: the caller then has nothing to
 * render but the reason, so it cannot ship a dead grey button with no stated
 * cause (house rule §11.8).
 */
export function newUserBlocker(d: NewUserDraft, googleEnabled: boolean): string | null {
  if (!d.username.trim()) return "Pick a username.";
  const email = d.email.trim();
  if (!email) return "Email is required — it is how this person is identified.";
  if (!emailLooksValid(email)) return "That email address does not look right.";
  if (d.method === "google") {
    // Offering Google-only while Google is off would create an account that
    // cannot sign in at all, and nothing would say so until they tried.
    if (!googleEnabled) {
      return "Google sign-in is not configured, so this account would have no "
           + "way to sign in. Set a password instead, or enable Google first.";
    }
    return null;                       // no password by design
  }
  if (!d.password) return "Set a password, or switch this account to Google sign-in.";
  if (passwordTooLong(d.password)) {
    return `That password is too long (max ${MAX_PASSWORD_BYTES} bytes).`;
  }
  return null;
}

/** The body to POST. A Google-only account sends an EMPTY password, which the
 *  server stores as "no local credential" rather than as the hash of "". */
export function newUserBody(d: NewUserDraft): {
  username: string; email: string; password: string;
} {
  return {
    username: d.username.trim(),
    email: d.email.trim(),
    password: d.method === "google" ? "" : d.password,
  };
}

/** What this account will actually be able to do, said plainly under the
 *  choice — the consequence is the part people get wrong. */
export function signInSummary(method: SignInMethod, email: string): string {
  const who = email.trim() || "this address";
  return method === "google"
    ? `They sign in with Google as ${who}. No password is stored, and they `
      + "cannot sign in locally."
    : `They sign in with a username and password. They can also use Google `
      + `later if ${who} matches their Google account.`;
}
