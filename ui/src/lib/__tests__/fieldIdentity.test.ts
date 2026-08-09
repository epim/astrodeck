// fieldIdentity.test.ts — the three states of the Capture target name (#182).
//
//   Run directly:  npx tsx src/lib/__tests__/fieldIdentity.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The states are a pure function precisely so this file can assert them rather
// than a screenshot, and because the invariant underneath them is not cosmetic:
// `value` is the operator's string and NOTHING may substitute a derived name
// into it. That string names the folder on disk and keys the persisted
// per-target frame counter, so a name the app could change on its own between
// frame 3 and frame 4 would split one night across two directories with two
// overlapping 0001… runs and report nothing.

/* eslint-disable @typescript-eslint/no-explicit-any */

(globalThis as any).window = { location: { pathname: "/" } };

const { describeAge, identifyState, objectCardPreview } =
  await import("../fieldIdentity");
type PreviewField = import("../../types").PreviewField;
type FieldIdentification = import("../../types").FieldIdentification;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const NOW = 1_786_255_200_000;

function ident(over: Partial<FieldIdentification> = {}): FieldIdentification {
  return {
    id: "M 27",
    label: "Dumbbell Nebula",
    kind: "dso",
    type: "Planetary Nebula",
    describe: "Dumbbell Nebula — planetary nebula in Vulpecula · mag 7.4",
    sep_arcmin: 2.1,
    confident: true,
    runner_up: null,
    ...over,
  };
}

function field(over: Partial<PreviewField> = {}): PreviewField {
  return {
    source: "solve",
    solved_at: NOW / 1000 - 30,
    id: ident(),
    objects: [],
    ...over,
  };
}

const base = { perFrameSolving: true, frameType: "Light", nowMs: NOW };

// ------------------------------------------------------------ state 1: nothing

test("no solve, per-frame solving ON: says nothing has solved since the slew", () => {
  const s = identifyState({ ...base, typed: "", field: null });
  assert(s.kind === "none", `kind was ${s.kind}`);
  assert(s.proposal === null, "there is nothing to propose");
  assert(/solve/i.test(s.note), `the note must name the missing thing: ${s.note}`);
  assert(!/unknown/i.test(s.note), `"unknown" says nothing: ${s.note}`);
  assert(s.fix === null, "solving is already on — there is no setting to change");
});

test("no solve, per-frame solving OFF: offers the setting as the fix", () => {
  const s = identifyState({ ...base, perFrameSolving: false, typed: "", field: null });
  assert(s.kind === "none", `kind was ${s.kind}`);
  assert(s.fix !== null, "the one actionable case must offer its action");
  assert(/plate solv/i.test(s.fix!.label), `fix label: ${s.fix!.label}`);
});

test("the two unidentified states do NOT say the same thing", () => {
  // They have different fixes, so a shared sentence would send half the users
  // to a setting that is already on.
  const off = identifyState({ ...base, perFrameSolving: false, typed: "", field: null });
  const on = identifyState({ ...base, typed: "", field: null });
  assert(off.note !== on.note,
    "'plate solving is off' and 'nothing has solved yet' are different problems "
    + "with different fixes and must not share one sentence");
});

test("a dark frame is not a fault", () => {
  const s = identifyState({ ...base, typed: "", field: null, frameType: "Dark" });
  assert(/dark frame has no sky/i.test(s.note), s.note);
  assert(s.fix === null, "there is nothing to fix about a dark");
});

test("solved, nothing catalogued: reads as a fact, not a failure", () => {
  const s = identifyState({ ...base, typed: "", field: field({ id: null }) });
  assert(s.kind === "none", `kind was ${s.kind}`);
  assert(/nothing in the catalogue lies inside it/i.test(s.note), s.note);
});

// ----------------------------------------------------------- state 2: proposed

test("identified and untouched: proposes, and the field's value stays empty", () => {
  const s = identifyState({ ...base, typed: "", field: field() });
  assert(s.kind === "proposed", `kind was ${s.kind}`);
  assert(s.value === "",
    `THE INVARIANT: a derived name was written into the field's value (${s.value}). `
    + "That value names the folder and keys the frame counter.");
  assert(s.proposal!.id === "M 27", "the proposal is the solved object");
  assert(s.adoptable, "one tap must be able to adopt it");
  assert(s.note.includes("2.1′"), `the off-centre distance is a fact: ${s.note}`);
});

test("a proposal carries its provenance and its age", () => {
  const s = identifyState({ ...base, typed: "", field: field() });
  assert(s.proposal!.source === "solve", "provenance rides the proposal");
  assert(s.proposal!.ageS === 30, `age was ${s.proposal!.ageS}`);
});

test("an unconfident proposal names the runner-up in the note", () => {
  const s = identifyState({
    ...base, typed: "",
    field: field({ id: ident({ confident: false, runner_up: "NGC 6853" }) }),
  });
  assert(s.note.includes("NGC 6853"),
    `"not confident" is not actionable; the other candidate is: ${s.note}`);
});

test("a POINTING-derived answer is worded as the mount's claim, not the sky's", () => {
  const s = identifyState({
    ...base, typed: "", field: field({ source: "pointing" }),
  });
  assert(/mount reports/i.test(s.note), s.note);
  assert(!/solves as/i.test(s.note),
    `a mount that has been found 50° from where it claimed must not be quoted `
    + `as if it were a measurement: ${s.note}`);
  assert(s.proposal!.source === "pointing", "provenance must survive to the pixel");
});

// -------------------------------------------------------------- state 3: typed

test("the operator's string is never overwritten", () => {
  const s = identifyState({ ...base, typed: "Veil east", field: field() });
  assert(s.kind === "typed", `kind was ${s.kind}`);
  assert(s.value === "Veil east", `value was ${s.value}`);
  assert(s.proposal!.id === "M 27", "the disagreement stays visible beside it");
  assert(s.note.includes("M 27"), `the note offers the machine's answer: ${s.note}`);
  assert(/your name is what gets written/i.test(s.note),
    `the note must say which one wins: ${s.note}`);
});

test("a typed name that matches the solve is CONFIRMED, not re-offered", () => {
  const s = identifyState({ ...base, typed: "m 27", field: field() });
  assert(s.kind === "typed", `kind was ${s.kind}`);
  assert(!s.adoptable, "offering to replace a name with itself is noise");
  assert(/confirmed/i.test(s.note), s.note);
  assert(s.note.includes("2.1′"), `and it still carries a new fact: ${s.note}`);
});

test("typing over an unidentified field still leaves the name alone", () => {
  const s = identifyState({ ...base, typed: "Panel 3", field: null });
  assert(s.kind === "typed" && s.value === "Panel 3", `${s.kind}/${s.value}`);
});

// --------------------------------------------------------------- the evidence

test("a mount/plate disagreement is stated, in degrees", () => {
  const s = identifyState({
    ...base, typed: "", field: field({ pointing_disagrees_deg: 4.2 }),
  });
  assert(s.warning !== null, "the condition that cost this rig a night was silent");
  assert(s.warning!.includes("4.2"), s.warning!);
  assert(/trust the solve/i.test(s.warning!), s.warning!);
});

test("a degraded catalogue is stated rather than answered from 64 objects", () => {
  const s = identifyState({
    ...base, typed: "", field: field({ id: null, catalog_degraded: true }),
  });
  assert(s.warning !== null && /did not load/i.test(s.warning!), String(s.warning));
});

// ------------------------------------------------- what actually reaches OBJECT

test("objectCardPreview mirrors the server's adoption rule exactly", () => {
  const typed = identifyState({ ...base, typed: "Veil east", field: field() });
  assert(objectCardPreview(typed) === "Veil east", "the operator's string wins");

  const conf = identifyState({ ...base, typed: "", field: field() });
  assert(objectCardPreview(conf) === "M 27", "empty + confident + solve => adopted");

  const unconf = identifyState({
    ...base, typed: "", field: field({ id: ident({ confident: false }) }),
  });
  assert(objectCardPreview(unconf) === "",
    "an unconfident guess must not be stamped as THE target — an absent card "
    + "beats a wrong one, and every stacker downstream reads this one");

  const pointing = identifyState({
    ...base, typed: "", field: field({ source: "pointing" }),
  });
  assert(objectCardPreview(pointing) === "",
    "a pointing-derived name must never reach a header");

  const none = identifyState({ ...base, typed: "", field: null });
  assert(objectCardPreview(none) === "", "nothing known, nothing written");
});

// --------------------------------------------------------------------- wording

test("describeAge keeps seconds where seconds matter", () => {
  assert(describeAge(0) === "1s ago", describeAge(0));
  assert(describeAge(45) === "45s ago", describeAge(45));
  assert(describeAge(600) === "10 min ago", describeAge(600));
  assert(describeAge(7200) === "2h ago", describeAge(7200));
});

const total = passed + failed;
console.log(`fieldIdentity.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
