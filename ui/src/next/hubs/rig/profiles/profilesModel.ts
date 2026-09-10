// profilesModel.ts - every string and every pure decision the PROFILES area
// makes (wave R7, T-R7-17; plan section 3.F20).
//
// The strings live here rather than inline for the reason the legacy panel's
// own history gives: the same sentence is quoted by a toast, by a confirm body
// and by a test, and three copies of it drifted into three different dashes.
// One export each means the reason a control gives when pressed is word for
// word the reason the list prints when the same thing fails.
//
// Every one is re-punctuated to the house rule: hyphens, never em-dashes. The
// legacy panel carries U+2014 in nine of them (`ProfileList.tsx` :215 :218 :223
// :288 :322 :335 :340 :520 :552), which is a copy defect this rebuild fixes
// rather than transcribes.
//
// Pure: no React, no store, no fetch, so it type-checks and runs under a bare
// `tsx` test.

import { ApiError } from "../../../../api";
import type { ProfileRow } from "../../../../types";

/** The capability EVERY write on this surface needs. The server enforces it on
 *  all six routes (`POST /api/profiles`, `/capture`, `/{id}/activate`,
 *  `PATCH /{id}`, `DELETE /{id}`), so the sheet names it once and gates
 *  everything on it. */
export const PROFILES_CAP = "config.backend" as const;

// ------------------------------------------------------------------ headings

export const PROFILES_EYEBROW = "SAVED PROFILES";
export const SAVE_EYEBROW = "SAVE CURRENT RIG";

export const SAVE_BLURB =
  "Snapshot the rig you are connected to right now into a reusable profile. "
  + "Activate it later to connect the same set of devices - and have it "
  + "auto-connect on boot.";

// --------------------------------------------------------------- list states

/** The load failure title. Asserted verbatim by the parity test, because a
 *  fetch that failed must never render as "No profiles yet": the two states
 *  look identical and only one of them means the library is empty. */
export const LOAD_FAILED = "Couldn't load profiles";
export const LOADING = "Reading the profile library...";
export const EMPTY_TITLE = "No profiles yet";
export const EMPTY_HINT =
  "Connect a rig from the devices screen, then save it below - or import one "
  + "exported from another AstroDeck.";
export const RETRY_LABEL = "RETRY";

// ------------------------------------------------------------------- buttons

export const IMPORT_LABEL = "IMPORT";
export const REFRESH_LABEL = "REFRESH";
export const ACTIVATE_LABEL = "ACTIVATE";
export const RECONNECT_LABEL = "RECONNECT";
export const CONNECTING_LABEL = "CONNECTING...";
export const RENAME_LABEL = "RENAME";
export const RENAME_SAVE_LABEL = "SAVE NAME";
export const RENAME_CANCEL_LABEL = "CANCEL";
export const UPDATE_LABEL = "UPDATE FROM RIG";
export const EXPORT_LABEL = "EXPORT";
export const DELETE_LABEL = "DELETE";
export const SAVE_BUTTON = "SAVE RIG";
export const SAVE_BUSY = "SAVING...";

export const NAME_LABEL = "Profile name";
export const NAME_PLACEHOLDER = "Backyard SCT";
/** What an unnamed capture is filed under. The legacy panel's default, kept:
 *  a nameless profile is worse than a generically named one. */
export const DEFAULT_CAPTURE_NAME = "Captured rig";

// -------------------------------------------------------------------- badges

export const ACTIVE_BADGE = "ACTIVE";
export const BOOT_NOTE = "auto-connects on boot";
export const OVERRIDES_WORD = "OVERRIDES";

/** What the OVERRIDES block means depends entirely on whether the profile is
 *  the active one - the same pinned provider is a fact about the running rig or
 *  a warning about the next activate. #129: this is the block that kept the
 *  polar aligner simulated for twelve days with nothing anywhere to see it by. */
export function overridesTail(active: boolean): string {
  return active
    ? " - in force on this rig now."
    : " - these take over when you activate it.";
}

// ------------------------------------------------------------------- toasts

export const CAPTURE_NEEDS_RIG = "Connect a rig first, then save it as a profile.";
export const CAPTURE_FAILED = "Could not save the current rig as a profile";
export const UPDATE_NEEDS_RIG = "Connect a rig first, then update the profile from it.";
export const UPDATE_FAILED = "Could not update the profile from the current rig";
export const EXPORT_FAILED = "Could not export the profile";
export const RENAME_FAILED = "Could not rename the profile";
export const DELETE_FAILED = "Could not delete the profile";
export const ACTIVATE_FAILED = "Could not activate the profile";
export const IMPORTED = "Profile imported";

/** The uncoded 409 from `_spawn_connect`'s own lane guard. It is raised BEFORE
 *  `force` is read, so a force retry there 409s again - which is why this is a
 *  sentence and not another dialog. */
export const LANE_BUSY =
  "Another profile connect is still running - wait for it to finish before switching again.";

/** Why every OTHER row's ACTIVATE is locked while one connect is in flight: the
 *  controller runs exactly one profile connect at a time. */
export const OTHER_CONNECTING =
  "another profile is connecting, and the controller does one at a time";

/** Why the rest of a row is locked while one of its own actions is in flight. */
export const ROW_BUSY = "an action on this profile is still running";

export function importFailed(detail: string): string {
  return `Import failed: ${detail}`;
}
export function exported(filename: string): string {
  return `Exported ${filename}`;
}
export function activating(name: string): string {
  return `Activating "${name}" - connecting the rig...`;
}
export function activated(name: string): string {
  return `"${name}" is active - and is what boots next time`;
}
/** The honest answer to a connect that has not landed inside the budget. We
 *  cannot tell "still connecting" from "failed" without a busy lane, and the
 *  activate route deliberately publishes none, so the sentence says both and
 *  points at the list, which is the server's own answer. */
export function notActiveYet(name: string): string {
  return `"${name}" is not active yet - the connect is still running, or it failed. `
    + "The list shows which profile the controller currently has active.";
}
export function deleted(name: string): string {
  return `Deleted "${name}" - the profile only; the rig is untouched`;
}
export function alreadyGone(name: string): string {
  return `"${name}" was already gone - list refreshed`;
}
export function updated(name: string): string {
  return `Updated "${name}" from the current rig`;
}
export function rowBusyToast(name: string): string {
  return `"${name}" is busy - wait for the current action to finish`;
}

// ------------------------------------------------------------------ confirms

/** The coded 409 is the sequence / capture-loop / polar guard, and `force` is
 *  exactly what bypasses it (it aborts the engine first). Offered once, never
 *  twice, or a server that keeps saying "running" becomes a dialog loop. */
export const FORCE_CONFIRM = {
  title: "Rig is busy",
  body: "A connect or sequence is already running. Force-activate this profile anyway?",
  mode: "confirm" as const,
  tone: "danger" as const,
  confirmLabel: "Force activate",
  cancelLabel: "Leave it running",
};

/** UPDATE FROM RIG overwrites stored state - the profile's device intent - so
 *  it carries the same friction as DELETE. The legacy panel used a bare
 *  hold-button whose only explanation was a `title` attribute, which never
 *  fires on touch; a hold CONFIRM says what is about to be overwritten and what
 *  survives. */
export function updateConfirm(row: { name: string }): {
  title: string; body: string; mode: "hold"; tone: "danger";
  confirmLabel: string; cancelLabel: string;
} {
  return {
    title: `Update "${row.name}" from the current rig?`,
    body:
      `This replaces the devices "${row.name}" stores with whatever is connected `
      + "right now, under the same name. Its optics, its pinned providers and its "
      + "PHD2 and NINA ports are kept. There is no undo.",
    mode: "hold",
    tone: "danger",
    confirmLabel: "Overwrite devices",
    cancelLabel: "Keep what it stores",
  };
}

// -------------------------------------------------------------------- rows

const MODE_LABEL: Record<ProfileRow["mode"], string> = {
  alpaca: "Native / Alpaca",
  nina: "NINA bridge",
  mixed: "Mixed backends",
  empty: "Empty",
};

/** The one line under a profile's name: what it talks to, how many devices it
 *  stores, and the site it was saved at. `devices_count` is the profile's own
 *  number, never the live rig's - the card is about the saved record. */
export function profileSubline(row: ProfileRow): string {
  const parts = [
    MODE_LABEL[row.mode] ?? row.mode,
    `${row.devices_count} device${row.devices_count === 1 ? "" : "s"}`,
  ];
  if (row.site_name) parts.push(row.site_name);
  if (row.active) parts.push(BOOT_NOTE);
  return parts.join(" · ");
}

/** The one error-to-sentence rule for this area. An `ApiError` carries the
 *  server's own detail, which is usually the most specific thing anyone has;
 *  anything else falls back to the caller's named failure rather than
 *  "[object Object]". */
export function errText(e: unknown, fallback: string): string {
  if (e instanceof ApiError && e.message) return e.message;
  if (e instanceof Error && e.message) return e.message;
  return fallback;
}

/** True when the error is the coded 409 the force retry answers. */
export function isRunningConflict(e: unknown): boolean {
  return e instanceof ApiError && e.code === "running";
}

/** True when the error is an uncoded 409 - the connect lane's own guard. */
export function isLaneConflict(e: unknown): boolean {
  return e instanceof ApiError && e.status === 409 && e.code !== "running";
}

/** True when the record is already gone. The action the user wanted has
 *  happened, so it is reported truthfully rather than as a failure. */
export function isGone(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}
