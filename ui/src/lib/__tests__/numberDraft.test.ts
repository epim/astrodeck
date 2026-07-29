// Unit tests for the plan editor's numeric-field reading (UX review
// 2026-07-28, rank 1: "plan numeric fields cannot be cleared; every edit
// concatenates").
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite build`),
// so this uses the same tiny inline-assert harness as exposure.test.ts.
// Run it with:  npx tsx src/lib/__tests__/numberDraft.test.ts

import { readNumberDraft } from "../../components/sequence/NumberField";

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
function assert(cond: boolean, msg: string): void {
  if (!cond) throw new Error(msg);
}

// ------------------------------------------------------------ the trap itself
test("Number('') is 0 — the reason a finite-check alone corrupts the field", () => {
  assert(Number("") === 0, "if this ever changes the guard below can relax");
  assert(Number.isFinite(Number("")), "an empty box parses as a perfectly finite zero");
});

test("blank HOLDS the model where blank is not a value", () => {
  assert(readNumberDraft("", false).kind === "hold", "empty must not write");
  assert(readNumberDraft("   ", false).kind === "hold", "whitespace must not write");
});

test("blank CLEARS where blank is a value (cooling setpoint = off)", () => {
  assert(readNumberDraft("", true).kind === "clear", "empty must mean off");
  assert(readNumberDraft("  ", true).kind === "clear", "whitespace must mean off");
});

// ------------------------------------------------------------ ordinary values
test("a plain number commits", () => {
  const r = readNumberDraft("120", false);
  assert(r.kind === "commit" && r.value === 120, `expected commit 120, got ${JSON.stringify(r)}`);
});
test("a negative number commits", () => {
  const r = readNumberDraft("-10", false);
  assert(r.kind === "commit" && r.value === -10, `expected commit -10, got ${JSON.stringify(r)}`);
});
test("a decimal commits", () => {
  const r = readNumberDraft("2.5", true);
  assert(r.kind === "commit" && r.value === 2.5, `expected commit 2.5, got ${JSON.stringify(r)}`);
});

// -------------------------------------------------- in-progress text is HELD
// These are the keystrokes a human passes THROUGH on the way to a number. Each
// one used to re-insert the previous value under the caret.
for (const partial of ["-", ".", "-.", "1e", "+"]) {
  test(`'${partial}' is an in-progress edit, not a value`, () => {
    assert(readNumberDraft(partial, false).kind === "hold", `'${partial}' must hold`);
    assert(readNumberDraft(partial, true).kind === "hold",
      `'${partial}' must hold even where blank is a value — it is not blank`);
  });
}
test("junk holds", () => {
  assert(readNumberDraft("abc", false).kind === "hold", "letters must hold");
});
test("Infinity and NaN hold — a plan number must stay finite", () => {
  assert(readNumberDraft("Infinity", false).kind === "hold", "Infinity must hold");
  assert(readNumberDraft("-Infinity", false).kind === "hold", "-Infinity must hold");
  assert(readNumberDraft("NaN", false).kind === "hold", "NaN must hold");
});
test("scientific notation still commits (the exposure bound check rejects it later)", () => {
  const r = readNumberDraft("1e5", false);
  assert(r.kind === "commit" && r.value === 100000, `expected commit 1e5, got ${JSON.stringify(r)}`);
});

// ------------------------------------------------- the reported transcript
// Replay of the pro's keystrokes on a 120s exposure, through both the old
// helper and the new one, as the CONTROLLED-INPUT loop actually runs it: the
// text the box shows next is whatever the model says (old) or whatever was
// typed (new).
function replayOld(start: number, keys: string[]): { text: string; model: number } {
  // the shipped helper: `Number.isFinite(n) && v !== "" ? n : fallback`
  const num = (v: string, fallback: number) => {
    const n = Number(v);
    return Number.isFinite(n) && v !== "" ? n : fallback;
  };
  let model = start;
  let text = String(start);
  for (const k of keys) {
    const typed = k === "\b" ? text.slice(0, -1) : text + k;
    model = num(typed, model);
    text = String(model);   // controlled input: the model re-renders the box
  }
  return { text, model };
}

function replayNew(start: number, keys: string[]): { text: string; model: number } {
  let model = start;
  let text = String(start);
  for (const k of keys) {
    const typed = k === "\b" ? text.slice(0, -1) : text + k;
    const r = readNumberDraft(typed, false);
    if (r.kind === "commit") model = r.value;
    text = typed;           // the draft is what the box shows while editing
  }
  return { text, model };
}

const CLEAR_AND_RETYPE = ["\b", "\b", "\b", "\b", "3", "0", "0"];

test("OLD helper: clearing 120 and typing 300 yields 1300 (the reported defect)", () => {
  const { model } = replayOld(120, CLEAR_AND_RETYPE);
  assert(model === 1300, `the defect should reproduce as 1300, got ${model}`);
});
test("NEW reading: clearing 120 and typing 300 yields 300", () => {
  const { text, model } = replayNew(120, CLEAR_AND_RETYPE);
  assert(model === 300, `expected 300, got ${model}`);
  assert(text === "300", `the box must read 300, got '${text}'`);
});
test("NEW reading: the field can actually reach empty", () => {
  const { text } = replayNew(120, ["\b", "\b", "\b"]);
  assert(text === "", `expected an empty box, got '${text}'`);
});
test("OLD helper: the field could never reach empty", () => {
  const { text } = replayOld(120, ["\b", "\b", "\b"]);
  assert(text === "1", `the old field bottomed out at '1', got '${text}'`);
});
// The cooling setpoint is the same loop with two differences that made it
// worse: blank is a legal value (null = off), so the box STARTS empty, and the
// old fallback was `plan.cool_to ?? -10` — a default the user never typed.
// Measured in the browser on the shipped build: typing "-10" into the empty box
// produced -1010, because the lone leading "-" is not finite and re-inserted
// the -10 default under the caret before the digits arrived.
function replayOldCool(keys: string[]): { text: string; model: number | null } {
  const num = (v: string, fallback: number) => {
    const n = Number(v);
    return Number.isFinite(n) && v !== "" ? n : fallback;
  };
  let model: number | null = null;
  let text = "";
  for (const k of keys) {
    const typed = k === "\b" ? text.slice(0, -1) : text + k;
    model = typed === "" ? null : num(typed, model ?? -10);
    text = model == null ? "" : String(model);
  }
  return { text, model };
}

function replayNewCool(keys: string[]): { text: string; model: number | null } {
  let model: number | null = null;
  let text = "";
  for (const k of keys) {
    const typed = k === "\b" ? text.slice(0, -1) : text + k;
    const r = readNumberDraft(typed, true);
    if (r.kind === "commit") model = r.value;
    else if (r.kind === "clear") model = null;
    text = typed;
  }
  return { text, model };
}

test("OLD helper: typing -10 into the empty setpoint yields -1010 (measured)", () => {
  const { model } = replayOldCool(["-", "1", "0"]);
  assert(model === -1010, `the defect should reproduce as -1010, got ${model}`);
});
test("NEW reading: typing -10 into the empty setpoint yields -10", () => {
  const { text, model } = replayNewCool(["-", "1", "0"]);
  assert(model === -10, `expected -10, got ${model}`);
  assert(text === "-10", `the box must read -10, got '${text}'`);
});
test("NEW reading: emptying the setpoint means OFF, not 'keep the old number'", () => {
  const { text, model } = replayNewCool(["-", "1", "0", "\b", "\b", "\b"]);
  assert(model === null, `expected null (off), got ${model}`);
  assert(text === "", `the box must be empty, got '${text}'`);
});

// ---------------------------------------------------------------- report
console.log(`\n${passed} passed, ${failed} failed`);
for (const f of failures) console.error(f);
if (failed > 0) {
  // (no @types/node in this UI package — same escape hatch as exposure.test.ts)
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export const result = { passed, failed };
