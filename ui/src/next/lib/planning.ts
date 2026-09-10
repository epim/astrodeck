// planning.ts - the quick-plan defaults and the target pool, held on the RIG
// (D-FU-1). One fetch per app session, three consumers, one write path.
//
// WHY THIS IS NOT A localStorage KEY ANY MORE. `server/astrodeck/planning.py`
// makes the argument in full; the short of it is that these two blocks decide
// what tonight shoots - how long each sub runs, which filters are in, which
// targets are on the shortlist - so they are properties of the rig and the
// operator, not of the browser that happened to set them. Per phone they were
// invisible to a second client, invisible to the engine, and gone the moment
// somebody cleared a site's data.
//
// THE STORE SHAPE is `session/now/stackView.ts`'s: module state, a listener
// set, a `useState` bump. Two mounted consumers (the Sky hub and its quick
// sheet, or the Settings sheet) share ONE `GET /api/planning`, and a write from
// either is seen by both immediately.
//
// THE DEGRADE RULE, WHICH IS THE POINT OF THE MODE FIELD. An engine older than
// wave S7 has no `/api/planning` and answers 404. A relay or a proxy can answer
// 200 with something that is not the block. In both cases this store stays in
// `local` mode: every surface renders from the legacy browser keys exactly as
// it does today, writes still persist there, no toast is raised, and NO
// migration flag is set and NO key is deleted. The one way this task could lose
// a user's data is deleting a local key on a rig that cannot store the value,
// and `mode` is the guard.
//
// LOCAL IS THE INITIAL STATE AND THE RIG REPLACES IT, not the other way round.
// The first paint reads the browser's own copy, so no sheet ever renders empty
// or flashes the shipped defaults while a fetch is in flight; when the rig
// answers, its block wins, which is the same "server wins" rule the migration
// applies. A write made in that window is not lost either: the migration runs
// AFTER the GET and carries it up.
//
// THE ONE RENAME lives here, in `QUICK_WIRE_NAME` below, and nowhere else.

import { useEffect, useState } from "react";
import { ApiError } from "../../api";
import { getPlanning, putPlanning } from "../../api/planning";
import { accessPhrase } from "../../lib/caps";
import { useStore } from "../../store";
import type { PlanningConfig, Principal, QuickDefaults } from "../../types";
import {
  LEGACY_RIG_KEYS, coerceQuickPrefs, hasLegacyQuick, readLegacyPool, readLegacyQuick,
  writeLegacyPool, writeLegacyQuick, forgetLegacyQuick, type QuickPrefs,
} from "../hubs/sky/finder/prefs";
import { forgetLegacyKey, migrateKey } from "./storageMigration";

// --------------------------------------------------------------- the rename
//
// THE BROWSER SPELLS ONE FIELD DIFFERENTLY FROM THE WIRE, and this table is the
// only place in the app that knows it. `ditherN` is what a phone has had on
// disk since the quick sheet shipped; `dither_n` is what the server stores,
// because every other field in `astrodeck.json` is snake_case and one camelCase
// key in the middle of it is the kind of thing the next person "fixes",
// silently resetting everybody's dither interval (`planning.py:34-38`).
//
// Both directions are derived from this table, so the pair is written once. The
// type-level assertion under it makes any OTHER divergence a compile error
// rather than a field that silently stops travelling: add `flats` to
// `QuickPrefs` without adding it to `QuickDefaults` and `tsc` fails here, at the
// boundary, instead of in a night that quietly did not dither.
const QUICK_WIRE_NAME = {
  hours: "hours",
  dawn: "dawn",
  on: "on",
  exp: "exp",
  extras: "extras",
  ditherN: "dither_n",
} as const;

type Assert<T extends true> = T;
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;

/** Every local field maps to a wire field of the same name EXCEPT `ditherN`,
 *  and `learned` is the only wire field with no local twin (it is the rig's
 *  answer to "has anything been learned here at all", which a browser key
 *  answered by existing). Exported so `noUnusedLocals` keeps it. */
export type QuickNamesAgree = Assert<Same<
  Exclude<keyof QuickPrefs, "ditherN">,
  Exclude<keyof QuickDefaults, "dither_n" | "learned">
>>;

/** Local -> wire, for the CHANGED KEYS ONLY. `PUT /api/planning` merges `quick`
 *  nested-partially (`planning.py:122-153`), so sending the whole block would
 *  overwrite per-filter exposures this client never knew about. */
export function quickToWire(
  patch: Partial<QuickPrefs> & { learned?: boolean },
): Partial<QuickDefaults> {
  const out: Record<string, unknown> = {};
  for (const [local, wire] of Object.entries(QUICK_WIRE_NAME)) {
    const v = (patch as Record<string, unknown>)[local];
    if (v !== undefined) out[wire] = v;
  }
  if (patch.learned !== undefined) out.learned = patch.learned;
  return out as Partial<QuickDefaults>;
}

/** Wire -> local, through the one parser both sources share, so a rig running
 *  an engine that does not carry every field yet lands on the same defaults an
 *  old phone would. */
export function quickFromWire(q: QuickDefaults | null | undefined): QuickPrefs {
  if (q == null) return coerceQuickPrefs(null);
  const local: Record<string, unknown> = {};
  for (const [localName, wireName] of Object.entries(QUICK_WIRE_NAME)) {
    local[localName] = (q as unknown as Record<string, unknown>)[wireName];
  }
  return coerceQuickPrefs(local as Partial<QuickPrefs>);
}

/** The full local block as the wire wants it, for the ONE write that is
 *  deliberately wholesale: RESET TO THE WHEEL'S DEFAULTS. */
function wholeQuickToWire(q: QuickPrefs, learned: boolean): Partial<QuickDefaults> {
  return quickToWire({ ...q, learned });
}

// ---------------------------------------------------------------- the store

export type PlanningMode = "loading" | "rig" | "local";

interface PlanningState {
  /** The rig's block, verbatim, or null while it has not answered one. */
  planning: PlanningConfig | null;
  /** Always renderable: the rig's copy in `rig` mode, this phone's otherwise. */
  quick: QuickPrefs;
  pool: string[];
  /** Has anything been learned at all - `quick.learned` on the rig, "the key
   *  exists" on a phone. The settings sheet renders a different face for it. */
  learned: boolean;
  mode: PlanningMode;
  /** A request that FAILED for a reason other than "this engine is older" (a
   *  404 is not an error, it is a different rig). Nothing renders it today: the
   *  local fallback is complete, so there is nothing the user has lost and
   *  nothing for them to do. It is here so a surface can say so without
   *  re-deriving it. */
  error: string | null;
  /** A write has been acknowledged by the rig this session. */
  savedOnce: boolean;
}

function localState(mode: PlanningMode): PlanningState {
  return {
    planning: null,
    quick: readLegacyQuick(),
    pool: readLegacyPool(),
    learned: hasLegacyQuick(),
    mode,
    error: null,
    savedOnce: false,
  };
}

let state: PlanningState = localState("loading");
const listeners = new Set<() => void>();
let started = false;
/** Monotonic, so a slow answer to an earlier write cannot land on top of a
 *  later one and put a control back where the user just moved it from. */
let writeSeq = 0;

function publish(next: PlanningState): void {
  state = next;
  for (const fn of listeners) fn();
}

/**
 * Is this a planning block, or just a 200?
 *
 * A body with no `quick` object or no `pool` array is NOT the block this client
 * asked for - an older engine, a proxy, a captive portal. Treating it as one
 * would hand the migration a "the rig has no value" answer it could then act
 * on, which is precisely how a local key gets deleted on a rig that cannot
 * store it. Strict here, permissive per-field in `quickFromWire`.
 */
function parseBlock(body: unknown): PlanningConfig | null {
  if (body == null || typeof body !== "object") return null;
  const b = body as Record<string, unknown>;
  if (b.quick == null || typeof b.quick !== "object") return null;
  if (!Array.isArray(b.pool)) return null;
  return {
    quick: b.quick as QuickDefaults,
    pool: (b.pool as unknown[]).filter((x): x is string => typeof x === "string"),
  };
}

function fromBlock(block: PlanningConfig, savedOnce: boolean): PlanningState {
  return {
    planning: block,
    quick: quickFromWire(block.quick),
    pool: block.pool,
    learned: block.quick?.learned === true,
    mode: "rig",
    error: null,
    savedOnce,
  };
}

/**
 * The three legacy keys, once, after the rig has answered with a real block.
 *
 * Runs AFTER the GET on purpose: `serverHasValue` is the rig's own answer, and
 * without it a migration cannot tell "the rig has never had a quick plan" from
 * "the rig has one and this phone's is older". `learned` is that answer for the
 * quick block (`config.py:1124-1128`) and a non-empty list is that answer for
 * the pool.
 *
 * The migrated quick block carries `learned: true`, because it WAS learned -
 * on this phone, over however many nights. Sending it without the flag would
 * move the values up and still leave the settings sheet saying nothing has ever
 * been learned.
 */
async function migrateLegacy(
  principal: Principal | null,
  block: PlanningConfig,
): Promise<PlanningConfig> {
  let latest = block;

  await migrateKey<QuickPrefs>({
    key: LEGACY_RIG_KEYS.quick,
    cap: "control.capture",
    principal,
    supported: true,
    serverHasValue: block.quick?.learned === true,
    read: () => readLegacyQuick(),
    put: async (local) => {
      latest = await putPlanning({ quick: wholeQuickToWire(local, true) });
    },
    // No toast for either of these: moving a preference the user never knew
    // was local is not news, and one per key on first launch would be three.
    conflictToast: null,
  });

  await migrateKey<string[]>({
    key: LEGACY_RIG_KEYS.pool,
    cap: "control.capture",
    principal,
    supported: true,
    serverHasValue: latest.pool.length > 0,
    // An empty stored pool is nothing to move. It is left under its key rather
    // than deleted, on the same principle as any value this build cannot read.
    read: () => {
      const p = readLegacyPool();
      return p.length > 0 ? p : null;
    },
    put: async (local) => { latest = await putPlanning({ pool: local }); },
    conflictToast: null,
  });

  // The orphan. Nothing in this app has ever read it back; the server has held
  // the pointer as `AppConfig.active_location_id` since
  // `POST /api/locations/{id}/apply` landed. Dropped only in `rig` mode, like
  // every other delete here.
  forgetLegacyKey(LEGACY_RIG_KEYS.site);

  return latest;
}

async function start(principal: Principal | null): Promise<void> {
  if (started) return;
  started = true;
  let block: PlanningConfig | null;
  try {
    block = parseBlock(await getPlanning());
  } catch (e) {
    // A 404 is an older engine, not a fault: no error line, no toast, and the
    // browser keys keep working. Anything else is recorded.
    const status = e instanceof ApiError ? e.status : 0;
    publish({
      ...localState("local"),
      error: status === 404 ? null : (e as Error).message,
    });
    return;
  }
  if (block == null) {
    // A 200 that is not the block. Same treatment as the 404 - the rig has not
    // said it can store this, so nothing local is touched.
    publish(localState("local"));
    return;
  }
  publish(fromBlock(block, false));
  // A write made WHILE the migration is in flight is the newer truth: its own
  // answer is the whole block back from the server, so publishing the
  // migration's copy on top of it would put a control the user just moved back
  // where it was. `writeSeq` is the only evidence that happened.
  const seq = writeSeq;
  const merged = await migrateLegacy(principal, block);
  if (seq === writeSeq) publish(fromBlock(merged, state.savedOnce));
}

// ------------------------------------------------------------- the writers

function sendQuick(
  patch: Partial<QuickPrefs> & { learned?: boolean },
  canWrite: boolean,
): void {
  const { learned: learnedFlag, ...localPatch } = patch;
  const merged = coerceQuickPrefs({ ...state.quick, ...localPatch });

  if (state.mode !== "rig") {
    // The rig cannot hold it (or has not answered yet). Today's behaviour,
    // unchanged, which is the whole of the degrade rule.
    writeLegacyQuick(merged);
    publish({ ...state, quick: merged, learned: true });
    return;
  }
  // A role without `control.capture` never writes. The sheet renders the same
  // controls with a `LockNote` carrying the reason, so this is not a silent
  // refusal - it is the second half of one.
  if (!canWrite) return;

  const before = state;
  const seq = ++writeSeq;
  publish({
    ...state,
    quick: merged,
    learned: learnedFlag ?? state.learned,
    error: null,
  });
  void putPlanning({ quick: quickToWire(patch) }).then(
    (block) => {
      if (seq !== writeSeq) return;
      const parsed = parseBlock(block);
      if (parsed) publish(fromBlock(parsed, true));
    },
    (e: Error) => { publish({ ...before, error: e.message }); },
  );
}

function sendPool(next: string[], canWrite: boolean): void {
  if (state.mode !== "rig") {
    writeLegacyPool(next);
    publish({ ...state, pool: next });
    return;
  }
  if (!canWrite) return;

  const before = state;
  const seq = ++writeSeq;
  publish({ ...state, pool: next, error: null });
  // The WHOLE array, because the server replaces it whole: a list has no field
  // names to merge by and the order is the shortlist's running order.
  void putPlanning({ pool: next }).then(
    (block) => {
      if (seq !== writeSeq) return;
      const parsed = parseBlock(block);
      if (parsed) publish(fromBlock(parsed, true));
    },
    (e: Error) => { publish({ ...before, error: e.message }); },
  );
}

function forgetQuick(canWrite: boolean): void {
  const cleared = coerceQuickPrefs(null);
  if (state.mode !== "rig") {
    forgetLegacyQuick();
    publish({ ...state, quick: cleared, learned: false });
    return;
  }
  if (!canWrite) return;

  const before = state;
  const seq = ++writeSeq;
  publish({ ...state, quick: cleared, learned: false, error: null });
  // The ONE deliberately wholesale quick write. "Forget these defaults" is a
  // request to replace every field, so a nested partial would be the wrong
  // shape here - it would leave the learned exposures in place under a
  // `learned: false` flag, which is the state the sheet cannot render.
  void putPlanning({ quick: wholeQuickToWire(cleared, false) }).then(
    (block) => {
      if (seq !== writeSeq) return;
      const parsed = parseBlock(block);
      if (parsed) publish(fromBlock(parsed, true));
    },
    (e: Error) => { publish({ ...before, error: e.message }); },
  );
}

// ---------------------------------------------------------------- the hook

export interface PlanningRead extends PlanningState {
  /** True while the first `GET /api/planning` is still out. */
  loading: boolean;
  /** Why the writers refuse, or null. Set ONLY when the rig carries the block
   *  and this role cannot write it: in `local` mode the phone remembers, so
   *  there is nothing to refuse and nothing to say. */
  lockedReason: string | null;
  /** Send the CHANGED KEYS. `learned: true` belongs to the sheet that learns -
   *  the route never sets it implicitly (`planning.py:190-198`). */
  putQuick: (patch: Partial<QuickPrefs> & { learned?: boolean }) => void;
  /** Send the whole shortlist, in the user's order. */
  putPool: (next: string[]) => void;
  /** Back to the shipped defaults, `learned` cleared. */
  forgetQuick: () => void;
}

export function usePlanning(): PlanningRead {
  const [, bump] = useState(0);
  const principal = useStore((s) => s.principal);

  useEffect(() => {
    const fn = () => bump((n) => n + 1);
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  }, []);

  // ONE fetch per app session, and not before there is a principal: the GET
  // needs `view.status`, and firing it while `/api/me` is still out would spend
  // the single attempt on a 401 and leave every surface on the local copy for
  // the rest of the session.
  useEffect(() => {
    if (principal) void start(principal);
  }, [principal]);

  const canWrite = (principal?.caps ?? []).includes("control.capture");
  const lockedReason = state.mode === "rig" && !canWrite
    ? `needs ${accessPhrase("control.capture")}`
    : null;

  return {
    ...state,
    loading: state.mode === "loading",
    lockedReason,
    putQuick: (patch) => sendQuick(patch, canWrite),
    putPool: (next) => sendPool(next, canWrite),
    forgetQuick: () => forgetQuick(canWrite),
  };
}

/** Test hatch, matching `resetStackViewForTests`: drop the module's memory so a
 *  file can grade a second first-mount. */
export function resetPlanningForTests(): void {
  state = localState("loading");
  listeners.clear();
  started = false;
  writeSeq = 0;
}
