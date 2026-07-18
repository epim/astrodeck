// setupCard.ts — pure state logic for the guided "Secure this server" setup
// card (Settings → Auth, 2026-07-17 decisions wave I4). Extracted out of
// AuthMethodPanel.tsx so the step-ORDERING rule is unit-testable without
// mounting React or the store.
//
// The ordering is the whole point of this card: the campaign's external
// reviewers tripped on it twice (see docs/guide/remote-access-and-roles.md's
// "disposable way to verify this yourself" procedure) — creating a local
// user does NOT by itself let anyone sign in. Enabling the `local` auth
// METHOD is the step that actually turns on logins. So step 2 ("Enable Local
// sign-in") must stay disabled/locked until step 1 ("Create an admin
// account") is actually satisfied by an ENABLED admin-role local user —
// not merely "a user exists".

export const SETUP_CARD_DISMISSED_KEY = "astrodeck-auth-setup-dismissed";

export interface SetupCardState {
  /** Render the card at all: auth is still open AND it hasn't been dismissed. */
  visible: boolean;
  /** Step 1 ("Create an admin account") is satisfied. */
  step1Done: boolean;
  /** Step 2 ("Enable Local sign-in") may be interacted with. */
  step2Enabled: boolean;
}

/**
 * @param methodsEnabled - true when the SERVER-persisted auth config has at
 *   least one sign-in method on (`(auth.methods ?? []).length > 0`). The card
 *   only ever makes sense while this is false (the server is open — no
 *   sign-in screen, every client is admin). Deliberately keyed off the
 *   persisted config rather than an in-progress draft toggle, so the card
 *   doesn't vanish out from under the admin the instant they flip a checkbox
 *   that hasn't been saved yet.
 * @param hasEnabledAdmin - true when an ENABLED admin-role local user exists
 *   (both AuthMethodPanel and UsersPanel read this from the same
 *   `GET /api/users` / `listUsers()` source — see AuthMethodPanel.tsx).
 * @param dismissed - the persisted (localStorage) dismiss flag for this card.
 */
export function setupCardState(
  methodsEnabled: boolean,
  hasEnabledAdmin: boolean,
  dismissed: boolean,
): SetupCardState {
  return {
    visible: !methodsEnabled && !dismissed,
    step1Done: hasEnabledAdmin,
    // Same predicate as step1Done today, but kept as its own field (rather
    // than callers reading step1Done directly) so the ordering rule has a
    // single named seam if step 2's unlock condition ever needs to diverge
    // from step 1's done-state (e.g. an extra precondition added later).
    step2Enabled: hasEnabledAdmin,
  };
}
