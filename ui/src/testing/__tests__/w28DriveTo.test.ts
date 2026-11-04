// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w28DriveTo.test.ts - the wizard walker in testing/driveTo.ts drives to a step
// BY NAME, fails by name when it cannot, and no wizard test keeps a walker of
// its own (#720; WP 2.8).
//
//   Run directly:  node --import ./test-css-stub.mjs --import tsx src/testing/__tests__/w28DriveTo.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// NAMED MUTANT "the walker counts presses" (driveTo.ts: the loop presses a
// fixed five times, `for (let i = 0; i < 5; i++)`, instead of until the named
// step shows). Observed ("w28DriveTo.test: 4/7 passed"):
//   x it presses until the named step shows, whatever the number of steps:
//     NEXT did not leave review: pick a filter
//   x already on the step presses nothing: NEXT never reached target: stopped
//     on resume (passed framing, filters, guiding, night, resume)
//   x a step the wizard does not have ... is bounded: an endless wizard was
//     not cut off at the bound (expected 12, got 5)

const { driveTo, domWizardDriver, MAX_PRESSES } = await import("../driveTo");
const { readFileSync, readdirSync, statSync } = await import("node:fs");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}
function throws(fn: () => void): string {
  try { fn(); } catch (e) { return (e as Error).message; }
  throw new Error("expected a throw, none came");
}

/** A wizard over a list of steps. `stuckOn` is a step NEXT cannot leave. */
function fakeWizard(steps: string[], stuckOn?: string) {
  let at = 0;
  let presses = 0;
  return {
    driver: {
      step: () => steps[at] ?? null,
      press: () => {
        presses++;
        if (steps[at] !== stuckOn && at < steps.length - 1) at++;
      },
      missing: () => "pick a filter",
    },
    presses: () => presses,
  };
}

const FOUR = ["target", "framing", "filters", "review"];
const SIX = ["target", "framing", "filters", "guiding", "night", "resume", "review"];

test("it presses until the named step shows, whatever the number of steps", () => {
  for (const steps of [FOUR, SIX]) {
    const w = fakeWizard(steps);
    const passedSteps = driveTo("review", w.driver);
    eq(w.driver.step(), "review", "the walk did not end on the named step");
    eq(passedSteps, steps.slice(1), "the steps it passed");
    eq(w.presses(), steps.length - 1, "one press per step crossed");
  }
});

test("an extra step before the target (the #720 shape) changes nothing for the test", () => {
  const w = fakeWizard([...FOUR.slice(0, 3), "night", "resume", "review"]);
  driveTo("review", w.driver);
  eq(w.driver.step(), "review", "a wizard with two more steps did not end on review");
});

test("already on the step presses nothing", () => {
  const w = fakeWizard(SIX);
  eq(driveTo("target", w.driver), [], "steps passed when already there");
  eq(w.presses(), 0, "a press was made when already on the step");
});

test("a press that does not leave the step fails by the step and the wizard's own reason", () => {
  const w = fakeWizard(SIX, "filters");
  const msg = throws(() => driveTo("review", w.driver));
  assert(msg.includes("NEXT did not leave filters"), `the failure did not name the step: ${msg}`);
  assert(msg.includes("pick a filter"), `the failure did not carry the wizard's reason: ${msg}`);
});

test("a step the wizard does not have fails by name and lists what was passed, and is bounded", () => {
  const w = fakeWizard(FOUR);
  const msg = throws(() => driveTo("night", w.driver));
  assert(msg.includes("NEXT did not leave review"), `unexpected failure: ${msg}`);
  const cycle = { step: () => "a", press: () => {}, missing: () => "" };
  let presses = 0;
  const msg2 = throws(() => driveTo("z", { ...cycle, step: () => String(presses % 2), press: () => { presses++; } }));
  assert(msg2.includes("NEXT never reached z"), `unexpected failure: ${msg2}`);
  eq(presses, MAX_PRESSES, "an endless wizard was not cut off at the bound");
});

// ------------------------------------------------------------ the DOM driver
interface FakeEl {
  testid: string;
  attrs: Record<string, string>;
  text: string;
  closest: (sel: string) => FakeEl | null;
  getAttribute: (k: string) => string | null;
  textContent: string;
}
function fakeEl(testid: string, attrs: Record<string, string> = {}, text = ""): FakeEl {
  const el: FakeEl = {
    testid, attrs, text, textContent: text,
    closest: () => el,
    getAttribute: (k) => attrs[k] ?? null,
  };
  return el;
}
function fakeDoc(els: FakeEl[]): any {
  return { querySelector: (sel: string) => els.find((e) => sel === `[data-testid="${e.testid}"]`) ?? null };
}

test("the DOM driver reads the step marker, presses NEXT's button and reports a lock", () => {
  const stepEl = fakeEl("wizard-step", { "data-step": "target" });
  const nextEl = fakeEl("wizard-next");
  const clicked: FakeEl[] = [];
  const d = domWizardDriver(fakeDoc([stepEl, nextEl]), (el) => {
    clicked.push(el as any);
    stepEl.attrs["data-step"] = "framing";
  });
  eq(d.step(), "target", "the step read");
  d.press();
  eq(clicked.length, 1, "NEXT was not clicked");
  eq(d.step(), "framing", "the step after the click");

  nextEl.attrs["aria-disabled"] = "true";
  const why = fakeEl("wizard-missing", {}, "Choose at least one filter.");
  const locked = domWizardDriver(fakeDoc([stepEl, nextEl, why]), () => { throw new Error("clicked a locked NEXT"); });
  const msg = throws(() => locked.press());
  assert(msg.includes("NEXT is locked") && msg.includes("Choose at least one filter."), `lock message: ${msg}`);
  const none = domWizardDriver(fakeDoc([stepEl]), () => {});
  assert(throws(() => none.press()).includes("no NEXT button"), "a missing NEXT was not named");
});

// --------------------------------------------- no wizard test walks on its own
const toPath = (u: URL): string => decodeURIComponent(u.pathname).replace(/^\/([A-Za-z]:)/, "$1");
const SRC = toPath(new URL("../../", import.meta.url)).replace(/[\\/]+$/, "");
const SELF = "w28DriveTo.test.ts";
function wizardTests(dir: string, out: string[]): void {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules") continue;
    const abs = `${dir}/${name}`;
    if (statSync(abs).isDirectory()) wizardTests(abs, out);
    else if (/\.test\.tsx?$/.test(name) && name !== SELF) out.push(abs);
  }
}

test("no test file under ui/src keeps a walker of its own: they all go through driveTo", () => {
  const files: string[] = [];
  wizardTests(SRC, files);
  assert(files.length > 400, `only ${files.length} test files walked - the scan is not reading ui/src`);
  const own = files.filter((f) => {
    const src = readFileSync(f, "utf8") as string;
    return /\b(?:function|const)\s+nextUntil\b/.test(src)
      || (/["']wizard-next["']/.test(src) && !/driveTo/.test(src));
  });
  eq(own.map((f) => f.slice(SRC.length + 1)), [],
    "a wizard test presses NEXT without driveTo, or defines its own walker:");
});

const total = passed + failed;
console.log(`w28DriveTo.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
