// profileDelete.ts — the decision layer behind Profiles → Delete.
//
// Deleting a profile is the only irreversible action on that row, so the two
// judgements it needs are pulled out of the JSX and pinned by tests:
//
//   1. `profileDeleteLock(canConfigBackend)` — may this principal delete at
//      all, and if not, WHAT DO WE TELL THEM? Returns the sentence, never a
//      bare boolean, so no call site can render a dead grey control with no
//      stated cause (house rule §11.8).
//
//   2. `profileDeleteConfirm(row)` — the confirm copy + friction level. The
//      copy is grounded in what the server ACTUALLY does, which was read
//      before it was written (server/astrodeck/profiles.py::ProfileLibrary.
//      delete + api/app.py::delete_profile):
//
//        - it unlinks `profiles/<id>.json` and nothing else;
//        - it does NOT tear down the live rig (no hub teardown on that route),
//          so the hardware you are connected to stays connected;
//        - it does NOT touch captured frames, calibration or plans (separate
//          stores, never referenced by the delete path);
//        - it does NOT clear `AppConfig.active_profile_id`. So deleting the
//          ACTIVE profile leaves that pointer dangling, and the next boot's
//          `hub.connect_active()` -> `profiles.get(pid)` raises KeyError,
//          which app.py's `_boot_connect` swallows into a log line. The
//          user-visible consequence is precise: the rig silently stops
//          auto-connecting on boot.
//
// That last case is the one most likely to hurt, so it escalates the dialog
// from a tap-confirm to a HOLD-confirm and says the boot consequence out loud.
// It is deliberately NOT blocked: a user whose only profile is (by definition)
// the active one would then have no way to remove it — there is no "deactivate"
// affordance anywhere in the UI — and an unresolvable dead end is a worse
// defect than a well-explained irreversible action.

import { accessPhrase } from "./caps";

/** Confirm-dialog spec — the subset of `ConfirmOpts` this decision produces.
 *  Kept structural (not an import of ConfirmOpts) so this module stays free of
 *  React types and runs under a bare `tsx` test. */
export interface ProfileDeleteConfirmSpec {
  title: string;
  body: string;
  /** "confirm" = tap OK/Cancel · "hold" = press-and-hold to proceed. */
  mode: "confirm" | "hold";
  tone: "danger";
  confirmLabel: string;
  cancelLabel: string;
}

/**
 * Why this principal cannot delete profiles, or `null` when they can.
 *
 * Returning the SENTENCE (not a boolean) is the point: the caller has nothing
 * to render but the reason, so it cannot accidentally ship a dimmed control
 * with no explanation. The phrase is derived from the role table
 * (`accessPhrase`), so it names the same policy the server enforces on
 * `DELETE /api/profiles/{id}` (CAP_CONFIG_BACKEND) rather than a hand-written
 * guess at which roles hold it.
 */
export function profileDeleteLock(canConfigBackend: boolean): string | null {
  if (canConfigBackend) return null;
  return `Deleting a profile needs ${accessPhrase("config.backend")}.`;
}

/**
 * Confirm copy + friction for deleting `row`.
 *
 * The body states what deleting does AND what it does not do, because the
 * frightening reading ("will this drop my rig / bin tonight's subs?") is the
 * one a tired user will assume at 2am. The affirmative is never the default:
 * `ConfirmHost` focuses Cancel, Escape and backdrop both resolve false, and we
 * leave `confirmPrimary` unset so Delete renders as the danger-outline
 * secondary rather than the filled primary.
 */
export function profileDeleteConfirm(row: { name: string; active: boolean }): ProfileDeleteConfirmSpec {
  const base =
    `"${row.name}" is a saved set of devices and where to find them. ` +
    `Deleting it removes that saved set permanently — there is no undo. ` +
    `It does not disconnect the rig you are running now, and it does not ` +
    `touch captured frames, calibration or plans.`;
  const activeTail =
    ` This is the ACTIVE profile, so nothing will auto-connect when AstroDeck ` +
    `restarts until you activate another one.`;
  return {
    title: `Delete "${row.name}"?`,
    body: row.active ? base + activeTail : base,
    mode: row.active ? "hold" : "confirm",
    tone: "danger",
    confirmLabel: "Delete profile",
    cancelLabel: "Keep it",
  };
}
