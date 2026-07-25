// Unit tests for the NOV-3 difficulty display helpers (design spec §1.3/§8).
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite build`),
// so these use the same tiny inline-assert harness as foundation.test.ts /
// eta.test.ts. They compile under `tsc -b` and run directly with a TS-aware
// runner, e.g.  npx tsx src/lib/__tests__/difficulty.test.ts

import {
  difficultyLabel, difficultyGlyph, difficultyTone, difficultyHint,
  isBeginnerFriendly, BEGINNER_TIERS, tierForTargetName, type DifficultyTier,
} from "../difficulty";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ---------------------------------------------------------------- tests
const TIERS: DifficultyTier[] = ["easy", "moderate", "hard"];

test("label maps every tier to a capitalized word", () => {
  eq(difficultyLabel("easy"), "Easy");
  eq(difficultyLabel("moderate"), "Moderate");
  eq(difficultyLabel("hard"), "Hard");
});

test("glyph/tone/hint are total over the tier union (no empty output)", () => {
  for (const t of TIERS) {
    assert(difficultyGlyph(t).length > 0, `glyph ${t}`);
    assert(["good", "warn", "bad"].includes(difficultyTone(t)), `tone ${t}`);
    assert(difficultyHint(t).length > 0, `hint ${t}`);
  }
});

test("glyphs are distinct per tier (shape is the night-mode channel)", () => {
  const g = TIERS.map(difficultyGlyph);
  eq(new Set(g).size, 3, "three distinct glyphs");
});

test("tone ramps good -> warn -> bad", () => {
  eq(difficultyTone("easy"), "good");
  eq(difficultyTone("moderate"), "warn");
  eq(difficultyTone("hard"), "bad");
});

test("beginner filter admits easy+moderate, excludes hard", () => {
  eq(isBeginnerFriendly("easy"), true);
  eq(isBeginnerFriendly("moderate"), true);
  eq(isBeginnerFriendly("hard"), false);
  eq(BEGINNER_TIERS.length, 2);
});

// ------------------------------------------------- tierForTargetName (grab-bag c2)
// The active-session difficulty chip: resolve the RUNNING target's tier from
// catalog entries the client already holds, and abstain whenever unsure.
test("tierForTargetName: matches by id or name, ignoring case/space; abstains otherwise", () => {
  const entries = [
    { id: "M 31", name: "Andromeda Galaxy", difficulty: "easy" as DifficultyTier },
    { id: "NGC 7000", name: "North America Nebula", difficulty: "hard" as DifficultyTier },
    { id: "IC 1396", name: "Elephant Trunk" },   // server sent no tier
  ];
  const cases: [string | null | undefined, DifficultyTier | null][] = [
    ["M 31", "easy"],                 // exact id
    ["m31", "easy"],                  // case + space insensitive
    ["andromeda galaxy", "easy"],     // by name
    ["North America Nebula", "hard"],
    ["IC 1396", null],                // matched but tier unknown -> abstain
    ["M 42", null],                   // no such entry
    ["", null],
    [null, null],
    [undefined, null],
  ];
  for (const [target, want] of cases) {
    eq(tierForTargetName(target, entries), want, `target=${String(target)}`);
  }
  eq(tierForTargetName("M 31", []), null, "no entries at all");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ndifficulty.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
