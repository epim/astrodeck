// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// unplacedLock.test.ts - the pure rules behind a lock the finder could not
// place (#503, #504, #508): the lock card's primary for an UNKNOWN obstruction,
// the window label's absent state, and the catalogue row a `?lock=` link
// carries.
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/next/hubs/sky/__tests__/unplacedLock.test.ts
//   (the stub is needed: `SkyHub` imports the atlas's stylesheet)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The mounted halves live in `defaultSiteFrame.test.tsx` (the held card on a
// default and a saved site) and `searchPickLock.test.tsx` (the row's round trip
// through the hash). These are the rules as rules, where their ORDER can be
// seen:
//
//   * an unknown obstruction is never read as clear, and never as behind the
//     horizon either; what `lockCta` answers BEFORE it asks about the horizon
//     (a satellite's passes, a rig to connect) still wins, because neither
//     claims the object is up;
//   * "window 0m" is a measurement and "-" is its absence;
//   * a link's row is refused as a whole when it is not a position on the
//     sky, so a hand-edited link cannot hold an object at RA 25h.
//
// MUTATION RECORD: see the block at the foot of this file.

/* eslint-disable @typescript-eslint/no-explicit-any */

// The same browser-globals-first convention as `skyCards.test.ts`: the barrels
// pass through `api.ts`, which reads `window.location` at module scope.
{
  const g = globalThis as any;
  if (typeof g.window === "undefined") {
    g.window = {
      location: { pathname: "/", protocol: "http:", host: "test", hash: "" },
      addEventListener() {}, removeEventListener() {},
      matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
    };
  }
  if (typeof g.localStorage === "undefined") {
    const m = new Map<string, string>();
    g.localStorage = {
      getItem: (k: string) => (m.has(k) ? (m.get(k) as string) : null),
      setItem: (k: string, v: string) => { m.set(k, String(v)); },
      removeItem: (k: string) => { m.delete(k); },
      clear: () => { m.clear(); },
    };
  }
  if (typeof g.document === "undefined") {
    g.document = {
      documentElement: {
        classList: { toggle() {}, add() {}, remove() {}, contains() { return false; } },
        style: { setProperty() {}, getPropertyValue() { return ""; } },
      },
    };
  }
}

const { lockCardCta, SITE_CTA_LABEL, UNPLACED_CTA_LABEL } = await import("../cards/LockCard");
const { windowLabel } = await import("../finder/model");
const { LOCK_ROW_KEYS, lockRowFromParams, lockRowParams } = await import("../finder/targets");
const { heldTarget, HELD_STATUS } = await import("../SkyHub");

let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${JSON.stringify(want)}, got ${JSON.stringify(got)}`);
}

const M31_ROW = {
  id: "M31", name: "Andromeda Galaxy", type: "Galaxy", kind: "dso",
  ra_hours: 0.712305, dec_deg: 41.26917, size_arcmin: 190,
};

// ---------------------------------------------------------- the held target
test("with no placement, the held target has no altitude, no window and an UNKNOWN obstruction", () => {
  const t = heldTarget(M31_ROW, null, null);
  eq(t.altNow, null, "altitude:");
  eq(t.azNow, null, "azimuth:");
  eq(t.windowMinutes, null, "window:");
  eq(t.obstructed, null, "obstruction:");
  eq(t.statusTxt, HELD_STATUS, "status chip:");
  eq(t.name, "Andromeda Galaxy", "name:");
});

test("with a placement, the held target carries the model's figures, a 0 window included", () => {
  const t = heldTarget(M31_ROW, { altDeg: 28.4, azDeg: 41.4, obstructed: false, windowMinutes: 300 }, null);
  eq(t.altNow, 28.4, "altitude:");
  eq(t.windowMinutes, 300, "the model's window:");
  eq(t.obstructed, false, "a computed verdict:");
  const never = heldTarget(M31_ROW, { altDeg: -44, azDeg: 300, obstructed: true, windowMinutes: 0 }, null);
  eq(never.windowMinutes, 0, "a computed zero window:");
  eq(never.obstructed, true, "a computed verdict:");
});

test("a held solar-system body is titled by its label, not its describe sentence", () => {
  const t = heldTarget({
    id: "Jupiter", name: "Jupiter is the largest planet, now in Gemini.", type: "Planet", kind: "solar_system",
    ra_hours: 7.1, dec_deg: 22.5,
  }, null, null);
  eq(t.name, "Jupiter", "the card's title:");
  eq(t.kind, "planet", "the kind:");
});

// ------------------------------------------------------ the unknown's primary
const UNPLACED = heldTarget(M31_ROW, null, null);

test("an unknown obstruction on a default site asks for a site - never IMAGE, never BEHIND OBSTRUCTION", () => {
  const cta = lockCardCta(UNPLACED, true, false);
  eq(cta.kind, "site", "kind:");
  eq(cta.label, SITE_CTA_LABEL, "label:");
});

test("an unknown obstruction for a role that cannot see a saved site is the hidden-position case", () => {
  const cta = lockCardCta(UNPLACED, true, true);
  eq(cta.kind, "unplaced", "kind:");
  eq(cta.label, UNPLACED_CTA_LABEL, "label:");
});

test("CONNECT still comes first for an unknown, as it does for every non-satellite", () => {
  eq(lockCardCta(UNPLACED, false, false).kind, "connect", "no rig, no site:");
  eq(lockCardCta(UNPLACED, false, true).kind, "connect", "no rig, hidden site:");
});

test("control: a placed lock's primary is still lockCta's, verdict and all", () => {
  const up = heldTarget(M31_ROW, { altDeg: 28, azDeg: 41, obstructed: false, windowMinutes: 300 }, null);
  const down = heldTarget(M31_ROW, { altDeg: -44, azDeg: 300, obstructed: true, windowMinutes: 0 }, null);
  eq(lockCardCta(up, true, true).kind, "image", "a placed, clear object:");
  eq(lockCardCta(down, true, true).kind, "obstructed", "a placed, hidden object:");
  eq(lockCardCta(up, true, false).kind, "image", "siteSaved is read only for an unknown:");
});

// ------------------------------------------------------------- the window
test("the window label prints '-' for a window nobody walked, and '0m' for a computed zero", () => {
  eq(windowLabel(null), "-", "absent:");
  eq(windowLabel(0), "0m", "a computed zero:");
  eq(windowLabel(160), "2h 40m", "a real window:");
});

// ---------------------------------------------------- the row in the hash
test("a catalogue row survives the hash: id, name, type, kind, position and extent", () => {
  const params = lockRowParams(M31_ROW);
  const back = lockRowFromParams(Object.fromEntries(new URLSearchParams(new URLSearchParams(params).toString())));
  assert(back != null, "the row did not come back");
  eq(back!.id, "M31", "id:");
  eq(back!.name, "Andromeda Galaxy", "name:");
  eq(back!.type, "Galaxy", "type:");
  eq(back!.kind, "dso", "kind:");
  eq(back!.ra_hours, 0.712305, "RA:");
  eq(back!.dec_deg, 41.26917, "Dec:");
  eq(back!.size_arcmin, 190, "extent:");
  for (const k of Object.keys(params)) {
    assert(k === "lock" || (LOCK_ROW_KEYS as readonly string[]).includes(k),
      `the row travels in a key the hub does not clear: ${k}`);
  }
});

test("a bare ?lock=<id> carries no row", () => {
  eq(lockRowFromParams({ lock: "m101" }), null, "a bare id:");
});

test("a link whose row is not a position on the sky carries no row", () => {
  const base = { lock: "M31", lockRa: "0.7", lockDec: "41.2" };
  assert(lockRowFromParams(base) != null, "control: the well-formed row was refused");
  eq(lockRowFromParams({ ...base, lockRa: "25" }), null, "RA 25h:");
  eq(lockRowFromParams({ ...base, lockRa: "-1" }), null, "RA -1h:");
  eq(lockRowFromParams({ ...base, lockDec: "91" }), null, "Dec +91:");
  eq(lockRowFromParams({ ...base, lockDec: "-91" }), null, "Dec -91:");
  eq(lockRowFromParams({ ...base, lockRa: "abc" }), null, "RA not a number:");
});

// MUTATION RECORD, 2026-09-29 (H4-USKY), each mutant run in a private scratch
// copy of ui/ (the session scratchpad's H4-USKY-mut, never the shared tree,
// #254), from a byte backup restored with its sha256 checked. Output verbatim.
//
//   MUTANT "obstructed: false placeholder" (SkyHub.tsx `heldTarget`, the
//   unplaced lock's obstruction written `false`). Observed
//   ("unplacedLock.test: 8/11 passed"):
//     x with no placement, the held target has no altitude, no window and an UNKNOWN obstruction: obstruction: expected null, got false
//     x an unknown obstruction on a default site asks for a site - never IMAGE, never BEHIND OBSTRUCTION: kind: expected "site", got "image"
//     x an unknown obstruction for a role that cannot see a saved site is the hidden-position case: kind: expected "unplaced", got "image"
//
//   MUTANT "heldTarget passes 0" (the placed lock's window written `0`).
//   Observed ("10/11"):
//     x with a placement, the held target carries the model's figures, a 0 window included: the model's window: expected 300, got 0
//   (It first SURVIVED, 11/11, while this case handed in a window of 0, which
//   a mutant writing 0 reproduces; the case now carries 300 and a separate 0.)
//   MUTANT "heldTarget passes 0" on the UNPLACED lock. Observed ("10/11"):
//     x with no placement, the held target has no altitude, no window and an UNKNOWN obstruction: window: expected null, got 0
//
//   MUTANT (control) "heldTarget passes null" (the placed lock's window
//   written `null`). Observed ("10/11"):
//     x with a placement, the held target carries the model's figures, a 0 window included: the model's window: expected 300, got null
//
//   MUTANT "windowLabel folds null into 0m" (finder/model.ts `windowLabel`:
//   `reachWindowLabel(minutes ?? 0)`). Observed ("10/11"):
//     x the window label prints '-' for a window nobody walked, and '0m' for a computed zero: absent: expected "-", got "0m"
//
//   MUTANT "unplaced reads SET A SITE" (cards/LockCard.tsx `lockCardCta`:
//   `siteSaved` read as false). Observed ("10/11"):
//     x an unknown obstruction for a role that cannot see a saved site is the hidden-position case: kind: expected "unplaced", got "site"
//
//   MUTANT "a row off the sky is still a row" (finder/targets.ts
//   `lockRowFromParams`: the RA/Dec range check removed). Observed ("10/11"):
//     x a link whose row is not a position on the sky carries no row: RA 25h: expected null, got {"id":"M31","name":null,"type":null,"kind":null,"ra_hours":25,"dec_deg":41.2,"size_arcmin":null}

const total = passed + failed;
console.log(`unplacedLock.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
