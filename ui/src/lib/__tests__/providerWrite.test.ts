// providerWrite.test.ts — WHERE a provider save lands, and what the guide
// dropdown is allowed to offer (#132 + the guide-eligibility fix).
//
// Run with:  npx tsx src/lib/__tests__/providerWrite.test.ts   (from ui/)
//
// The property under test is not "does it return an object". It is: does a save
// made under a profile pin reach the PROFILE, and does the user learn that
// before they commit? The bug being fixed is not a crash — it is a save that
// succeeds, reports success, and changes nothing that runs. Nothing about the
// old behaviour looked wrong from the outside, which is exactly why the branch
// needs a test rather than an inspection.
import type { EffectiveEntry } from "../../types";
import {
  DEFAULT_PROVIDERS,
  blockedSelectionNote,
  globalProvidersBody,
  guideProviderLabel,
  guideProviderRows,
  providerWriteNote,
  providerWriteTarget,
} from "../providerWrite";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}
function eq(a: unknown, b: unknown, msg = ""): void {
  if (!Object.is(a, b)) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const entry = (p: Partial<EffectiveEntry>): EffectiveEntry => ({
  value: null, layer: "default", profile: null, config: null, default: null,
  profile_id: null, profile_name: null, reason: null, ...p,
});

// ------------------------------------------------------------- write target

test("a profile-pinned capability writes back to THE PROFILE", () => {
  const t = providerWriteTarget(
    entry({ layer: "profile", value: "sim", config: "astrodeck",
            profile_id: "p1", profile_name: "Backyard rig" }),
  );
  eq(t.layer, "profile", "target layer:");
  eq(t.profileId, "p1", "addressed record:");
  eq(t.profileName, "Backyard rig", "named for the copy:");
});

test("a profile pin holding the SAME value as global still writes the profile", () => {
  // The trap the whole feature turns on: identical values render identically,
  // and a value-based decision would route this save to global, where it would
  // keep being shadowed. The LAYER is the input, never the value.
  const t = providerWriteTarget(
    entry({ layer: "profile", value: "astap", config: "astap",
            profile_id: "p1", profile_name: "Rig1" }),
  );
  eq(t.layer, "profile", "layer decides, not equality:");
});

test("config / default / camera layers all write global, as before", () => {
  for (const layer of ["config", "default", "camera"] as const) {
    eq(providerWriteTarget(entry({ layer })).layer, "config", `${layer}:`);
  }
});

test("no provenance block at all writes global — the pre-#132 behaviour", () => {
  // The WS `hello` bootstrap carries no `effective`. Degrading to the known
  // (imperfect) global write beats guessing at a profile that may not exist.
  eq(providerWriteTarget(null).layer, "config", "null entry:");
});

test("a profile layer with no addressable id degrades instead of throwing", () => {
  // Server-side this cannot happen — provenance always sets profile_id beside a
  // profile layer. If it ever did, an unaddressable write must fall back, not
  // crash the panel.
  eq(providerWriteTarget(entry({ layer: "profile", profile_id: null })).layer, "config", "");
});

// ------------------------------------------------------------------- copy

test("the profile case states the target by name", () => {
  const note = providerWriteNote({
    layer: "profile", profileId: "p1", profileName: "Rig1",
  });
  assert(!!note && note.includes("Rig1"), `names the profile: ${note}`);
  assert(
    !!note && note.includes("not the global setting"),
    `contrasts it with the layer the user assumes: ${note}`,
  );
});

test("the global case says NOTHING — it is the mental model the user has", () => {
  // A line under every row saying "this saves the setting" is copy describing
  // what is on screen, and copy like that trains people to skip the lines that
  // do carry something.
  eq(providerWriteNote({ layer: "config", profileId: null, profileName: null }),
     null, "global note:");
});

// ------------------------------------------------------------- global body

test("a global body carries all four capabilities from the RAW global block", () => {
  const body = globalProvidersBody(
    { autofocus: "astrodeck", polar_align: "auto", solve: "astap", guide: "auto" },
    "solve", "sim");
  eq(body.solve, "sim", "the edited key:");
  eq(body.autofocus, "astrodeck", "untouched keys survive:");
  eq(Object.keys(body).length, 4, "the server model has no optional keys:");
});

test("a missing global block still produces a complete body", () => {
  const body = globalProvidersBody(null, "guide", "backend");
  eq(body.guide, "backend", "");
  eq(body.polar_align, DEFAULT_PROVIDERS.polar_align, "defaults fill the rest:");
});

// -------------------------------------------------------- guide option rows

const OPTIONS = [
  { value: "auto", eligible: true, reason: null },
  { value: "astrodeck", eligible: false,
    reason: "AstroDeck native needs a guide camera assigned and connected" },
  { value: "backend", eligible: true, reason: null },
];

test("a BLOCKED provider is kept as a row, carrying its reason", () => {
  // The correctness half. Dropping the row is what taught a user the product
  // could not guide at all; the reason is what tells them to assign a camera.
  const rows = guideProviderRows(OPTIONS, ["auto", "backend"], "auto");
  eq(rows.length, 3, "every value renders:");
  const native = rows.find((r) => r.value === "astrodeck")!;
  eq(native.eligible, false, "marked unavailable:");
  assert(
    native.reason!.includes("guide camera"),
    `states what to do: ${native.reason}`,
  );
});

test("labels are human, not slugs", () => {
  eq(guideProviderLabel("astrodeck"), "AstroDeck native", "");
  eq(guideProviderLabel("backend"), "PHD2 / NINA bridge", "");
  // An unknown value prints literally: a pin naming something we cannot resolve
  // is exactly when the user most needs to see the raw string.
  eq(guideProviderLabel("nina-1a2b"), "nina-1a2b", "");
});

test("an old server sending only `eligible` still renders a usable control", () => {
  const rows = guideProviderRows(undefined, ["auto", "backend"], "auto");
  eq(rows.length, 2, "degrades to the list:");
  assert(rows.every((r) => r.eligible), "with nothing claimed unavailable");
});

test("pre-first-poll offers Auto only, and never invents eligibility", () => {
  const rows = guideProviderRows(undefined, undefined, "auto");
  eq(rows.length, 1, "");
  eq(rows[0].value, "auto", "");
});

test("a stored value the rig no longer offers stays listed and selected-able", () => {
  // The sticky-option rule. A legacy "sim" pin must not silently vanish — the
  // control would then show a value the profile does not hold.
  const rows = guideProviderRows(OPTIONS, ["auto", "backend"], "sim");
  const sticky = rows.find((r) => r.value === "sim")!;
  assert(!!sticky, "the stored value renders");
  eq(sticky.sticky, true, "marked as stored-not-offered:");
  eq(sticky.eligible, true, "re-picking the current value is a no-op, not a block:");
  assert(!!sticky.reason, "and it says why it looks different");
});

test("a stored choice that cannot run here says so, in one sentence", () => {
  // Without it the row contradicts itself: the override disclosure says
  // "Running AstroDeck native", the resolver line says "using the PHD2 bridge",
  // and the badge says PHD2. All three are true; together they read as a bug.
  const rows = guideProviderRows(OPTIONS, ["auto", "backend"], "astrodeck");
  const note = blockedSelectionNote(rows, "astrodeck");
  assert(!!note && note.includes("AstroDeck native"), `names the pin: ${note}`);
  assert(!!note && note.includes("stays saved"), `and says the pin survives: ${note}`);
});

test("a runnable stored choice gets NO such note", () => {
  const rows = guideProviderRows(OPTIONS, ["auto", "backend"], "backend");
  eq(blockedSelectionNote(rows, "backend"), null, "ordinary case:");
  eq(blockedSelectionNote(rows, "nonexistent"), null, "unknown value:");
});

test("a blocked option with no server reason still gets a sentence", () => {
  const rows = guideProviderRows(
    [{ value: "astrodeck", eligible: false, reason: null }], null, "auto");
  const native = rows.find((r) => r.value === "astrodeck")!;
  assert(!!native.reason, "a dim row with no sentence is the dead end being fixed");
});

// ----------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nproviderWrite.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
