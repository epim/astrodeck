// filterSettings.test.ts — what a filter pick fills in (#215).
//
//   Run directly:  npx tsx src/lib/__tests__/filterSettings.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The rule these pin, and the reason it is worth pinning: these settings are
// TRI-STATE. `null` means "not pinned"; 0 means "pinned to zero". A gain of 0
// is a real setting on every camera in the product, so the two cannot share a
// representation — and the moment they do, an operator who pins gain 0 gets it
// silently ignored forever with nothing on screen to explain why.

import {
  filterSettingsPatch, filterSettingsPatchByName, filterHasPinnedSettings,
  filterSettingsSummary,
} from "../filterSettings";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  deepEqual(a: unknown, b: unknown, msg?: string) {
    const ja = JSON.stringify(a), jb = JSON.stringify(b);
    if (ja !== jb) throw new Error(msg ?? `${ja} !== ${jb}`);
  },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
  ok(c: unknown, msg?: string) { if (!c) throw new Error(msg ?? "not ok"); },
};

const wheel = {
  names: ["L", "Ha", "OIII", "Dark"],
  exposures: [null, 30, 60, 5] as (number | null)[],
  gains: [null, 100, null, 200] as (number | null)[],
  opaque: [false, false, false, true],
};

test("a pinned filter fills in both halves", () => {
  assert.deepEqual(filterSettingsPatch(wheel, 1), { exposure_s: 30, gain: 100 });
});

test("an unpinned filter changes nothing at all", () => {
  assert.deepEqual(filterSettingsPatch(wheel, 0), {},
    "an empty patch is what leaves the screen's own values alone");
  assert.equal(filterHasPinnedSettings(wheel, 0), false);
});

test("half a pin is half a patch, not a reset of the other half", () => {
  // OIII pins an exposure and no gain. Filling gain with anything here — 0, or
  // the previous filter's — would be inventing a value the operator never set.
  assert.deepEqual(filterSettingsPatch(wheel, 2), { exposure_s: 60 });
});

test("gain 0 is a PIN, and a 0-second exposure is not", () => {
  const w = { exposures: [0], gains: [0], opaque: [false] };
  assert.deepEqual(filterSettingsPatch(w, 0), { gain: 0 },
    "gain 0 is a real setting and must survive; a 0 s frame is not a frame");
});

test("a blackout slot pins nothing, even when the store holds numbers", () => {
  // The server clears these on save for a slot with no light path. This is the
  // other half of that rule: a stale pin arriving from an older store must not
  // be applied just because it is present in the array.
  assert.deepEqual(filterSettingsPatch(wheel, 3), {});
});

test("a missing wheel, a missing array and a silly slot are all just empty", () => {
  assert.deepEqual(filterSettingsPatch(null, 0), {});
  assert.deepEqual(filterSettingsPatch(undefined, 1), {});
  assert.deepEqual(filterSettingsPatch({ names: ["L"] }, 0), {});
  assert.deepEqual(filterSettingsPatch(wheel, -1), {});
  assert.deepEqual(filterSettingsPatch(wheel, 99), {});
  assert.deepEqual(filterSettingsPatch(wheel, 1.5), {});
});

test("the summary says what is about to change, and stays quiet otherwise", () => {
  assert.equal(filterSettingsSummary(wheel, 1), "30s · gain 100");
  assert.equal(filterSettingsSummary(wheel, 2), "60s");
  assert.equal(filterSettingsSummary(wheel, 0), "",
    "an empty summary is what keeps the toast from firing on every pick");
});

test("by NAME is what the plan editor needs, since a step stores a name", () => {
  assert.deepEqual(filterSettingsPatchByName(wheel, "Ha"),
    { exposure_s: 30, gain: 100 });
  assert.deepEqual(filterSettingsPatchByName(wheel, "L"), {});
});

test("a filter this wheel does not have is a guess we refuse to make", () => {
  // Imported plan, or the wheel was swapped between nights. Inventing settings
  // for a filter that is not in the carousel is worse than leaving the step's
  // own numbers alone — the operator can see and fix a wrong filter name, but
  // not an exposure that changed itself for a reason nothing states.
  assert.deepEqual(filterSettingsPatchByName(wheel, "SII"), {});
  assert.deepEqual(filterSettingsPatchByName(wheel, ""), {});
  assert.deepEqual(filterSettingsPatchByName(wheel, null), {});
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`filterSettings.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
