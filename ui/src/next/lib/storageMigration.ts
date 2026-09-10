// storageMigration.ts - moving ONE browser key onto the rig, once, in a way
// that cannot lose it (D-FU-1, the T-U7a-K contract).
//
// WHAT THIS IS FOR. Four `astrodeck-next-` keys held RIG data in the phone:
// the quick-plan defaults, the target pool, the last-chosen site id and the
// optics aux fields. Rig data in a browser is data the rig cannot read, a
// second client cannot see and a cleared origin destroys - so each one now has
// a typed home on the server (`server/astrodeck/planning.py`, `PUT /api/optics`)
// and this module is the one-way door between the two.
//
// THE DEGRADE RULE, AND IT IS THE ONLY P0 IN THIS TASK. The migration must
// NEVER be able to delete a local key on a rig that cannot store the value. An
// engine older than wave S7 answers `GET /api/planning` with a 404; a proxy can
// answer 200 with something that is not the block at all. In both cases the
// caller passes `supported: false` and this function touches NOTHING - no PUT,
// no `removeItem`, no flag - so the next session tries again and the phone
// keeps rendering from its own copy in the meantime. Every other outcome is
// recoverable; that one is somebody's learned per-filter exposures, gone.
//
// THE FLAG IS WRITTEN AFTER THE WRITE LANDS, never before. `astrodeck-next-
// migrated-<key>` means "this key has already been dealt with", so setting it
// optimistically and then failing the PUT would leave the value in the browser
// AND the migration marked done - the one state where nothing ever moves it and
// nothing ever says so. A failed PUT therefore leaves the key and no flag: the
// next session retries. A succeeded one never runs again, even if a user pastes
// the old key back by hand.
//
// SERVER WINS ON A CONFLICT, and the local copy is deleted rather than merged.
// Merging two half-truths produces a third value nobody chose (the classic
// "your settings changed by themselves" bug); the rig's copy is the one a second
// client can see and the one the engine reads, so it is the one that survives.
// The caller decides what "the rig already has a value" means for its key -
// `planning.quick.learned`, a non-empty pool, `aperture_mm > 0` - because that
// answer is per-key and is not guessable from JSON.
//
// NO REACT, NO STORE, NO FETCH. The caller passes the reader, the writer and
// the principal, so this file is testable in plain Node with a Map behind
// `localStorage`. The capability check is written out here rather than imported
// from `lib/caps.ts`'s `capAllowed` for exactly that reason: that module reaches
// `lib/base.ts`, which reads `window.location` at import.

import type { Capability, Principal } from "../../types";

/** Every migration flag is this prefix plus the key it is about, so one glance
 *  at a phone's storage says which moves have happened. */
export const MIGRATION_FLAG_PREFIX = "astrodeck-next-migrated-";

export function migrationFlagKey(key: string): string {
  return `${MIGRATION_FLAG_PREFIX}${key}`;
}

/**
 * What one `migrateKey` call did. Returned rather than logged so a caller can
 * assert on it and a future surface can say what happened.
 *
 *   unsupported   the rig cannot store this value - NOTHING was touched
 *   not-allowed   this role cannot write it - NOTHING was touched
 *   already-done  the flag was set in an earlier session
 *   absent        no local value to move; the flag is set so we stop looking
 *   migrated      the local value is on the rig and the key is gone
 *   server-wins   the rig already had one; the local copy was deleted
 *   failed        the write failed; the key AND the retry both survive
 */
export type MigrationOutcome =
  | "unsupported"
  | "not-allowed"
  | "already-done"
  | "absent"
  | "migrated"
  | "server-wins"
  | "failed";

export interface MigrateKeySpec<T> {
  /** The legacy `localStorage` key, e.g. `astrodeck-next-sky-quick`. */
  key: string;
  /** The capability the WRITE needs (`control.capture` for planning,
   *  `config.site_optics` for optics). A role without it keeps reading its
   *  local copy and the surface says so - it does not silently lose the key. */
  cap: Capability;
  /** Who is asking. Fail-closed: an unresolved principal holds nothing. */
  principal: Principal | null;
  /** Can this rig store the value at all? FALSE means do nothing whatsoever
   *  (see the degrade rule in the header). */
  supported: boolean;
  /** Does the rig already carry a value for this key? Per-key and not
   *  guessable here: `quick.learned`, a non-empty pool, `aperture_mm > 0`. */
  serverHasValue: boolean;
  /** Parse the stored string. Return null for anything unusable - it is then
   *  treated as absent and LEFT IN PLACE, because a value this build cannot
   *  read may still be a value some other build wrote. */
  read: (raw: string) => T | null;
  /** The one write. Resolve to move the key, reject to keep it. */
  put: (value: T) => Promise<unknown>;
  /** One info toast, shown only on the `server-wins` path, only when the phone
   *  actually had a value to lose. Planning passes none: moving a preference
   *  nobody knew was local is not news, and three keys would be three toasts. */
  conflictToast?: string | null;
  /** How to show it. The caller owns the channel (this module has no store). */
  onToast?: (message: string) => void;
}

function readRaw(key: string): string | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage.getItem(key);
  } catch {
    // A private window or a WebView that throws on storage has nothing to
    // migrate, which is the same answer as an empty key.
    return null;
  }
}

function removeRaw(key: string): void {
  try {
    if (typeof localStorage !== "undefined") localStorage.removeItem(key);
  } catch {
    /* forgetting is best-effort; the rig already has the value */
  }
}

function writeFlag(key: string): void {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(migrationFlagKey(key), "1");
    }
  } catch {
    /* without the flag the next session retries, which is the safe direction */
  }
}

/** Drop a key nothing reads any more. Used for `astrodeck-next-sky-site`, whose
 *  server home (`AppConfig.active_location_id`) is written by
 *  `POST /api/locations/{id}/apply` and which no UI has ever read back. */
export function forgetLegacyKey(key: string): void {
  removeRaw(key);
}

/** Has this key already been migrated on this phone? */
export function migrationDone(key: string): boolean {
  return readRaw(migrationFlagKey(key)) != null;
}

/**
 * Move one key to the rig, at most once per phone.
 *
 * The order of the checks is the contract: unsupported and not-allowed come
 * first because they must leave the phone exactly as they found it, and the
 * flag is written last on every path that ends with the key gone.
 */
export async function migrateKey<T>(spec: MigrateKeySpec<T>): Promise<MigrationOutcome> {
  if (!spec.supported) return "unsupported";
  if (migrationDone(spec.key)) return "already-done";
  if (!(spec.principal?.caps ?? []).includes(spec.cap)) return "not-allowed";

  const raw = readRaw(spec.key);
  if (raw == null) {
    // Nothing here, and there never will be: the writers are gone. Flag it so
    // no later session re-reads a key that cannot come back.
    writeFlag(spec.key);
    return "absent";
  }

  // A reader that THROWS is the same answer as one that returns null, and it
  // must not take the mount with it: this runs inside an effect on the first
  // screen the app opens, so an old shape that trips a parser would otherwise
  // be an unhandled rejection over the whole hub.
  let value: T | null;
  try {
    value = spec.read(raw);
  } catch {
    value = null;
  }
  if (value == null) {
    writeFlag(spec.key);
    return "absent";
  }

  if (spec.serverHasValue) {
    removeRaw(spec.key);
    writeFlag(spec.key);
    if (spec.conflictToast) spec.onToast?.(spec.conflictToast);
    return "server-wins";
  }

  try {
    await spec.put(value);
  } catch {
    // The key stays, the flag stays unset, the next session tries again. The
    // surface has already rendered from the local copy and is not wrong.
    return "failed";
  }
  removeRaw(spec.key);
  writeFlag(spec.key);
  return "migrated";
}
