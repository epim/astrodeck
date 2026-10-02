// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w5ForceActivateDetail.test.ts - the force-activate confirm is worded from
// the server's own coded-409 detail, not a fixed sentence (#256).
//
//   Run directly:  npx tsx src/next/hubs/rig/profiles/__tests__/w5ForceActivateDetail.test.ts
//   Also run by `npm test` (run-tests.mjs).
//
// The server's `_teardown_busy_detail` (app.py) now has three causes for the
// coded 409 "running", and only the auto-resume one says that forcing turns
// the recovering session's auto-resume off (#238). A dialog worded from a
// fixed sentence can never say that; one worded from the server's own
// `detail` says it automatically, because the server's own text already does.

// ------------------------------------------------------------- browser stub
// profilesModel.ts imports `ApiError` from `../../../../api`, whose
// `lib/base.ts` reads `window.location.pathname` at module scope (the relay
// mount-prefix fix) - nothing else this file touches needs the DOM, so a full
// jsdom is not needed, just enough of `window` for that one read.
(globalThis as Record<string, unknown>).window = { location: { pathname: "/" } };

// A dynamic import, not a static one: static `import` specifiers are hoisted
// above this file's own top-level code, which would read `window.location`
// before the stub above ever ran.
const { forceActivateConfirm, sentenceFrom } = await import("../profilesModel");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// --------------------------------------------------------------- sentenceFrom

test("sentenceFrom capitalizes and terminates a server fragment", () => {
  assert(
    sentenceFrom("a sequence, capture loop or polar alignment is running")
      === "A sequence, capture loop or polar alignment is running.",
    "did not capitalize and terminate the plain busy fragment",
  );
});

test("sentenceFrom leaves an already-terminated sentence alone bar the case", () => {
  assert(sentenceFrom("already ends here.") === "Already ends here.",
    "added a second period to a sentence that already had one");
});

test("sentenceFrom on empty input stays empty", () => {
  assert(sentenceFrom("") === "", "turned an empty detail into punctuation");
});

// ----------------------------------------------------------- forceActivateConfirm

// The server's exact `_TEARDOWN_WHILE_RECOVERING` text (app.py) - copied here
// rather than re-derived, because the dialog must show the SERVER'S words,
// and a test that re-typed its own version could pass against a server that
// said something else entirely (which is #256's whole complaint).
const RECOVERING_DETAIL =
  "auto-resume is re-centring the mount after a restart "
  + "(GET /api/sequence/resume-arm reports the step it is on); force stops "
  + "the re-centring before its next step and turns that session's "
  + "auto-resume off, as Abort does, then goes ahead";

test("the force dialog says auto-resume will be turned off when that is why the rig is busy", () => {
  const spec = forceActivateConfirm(RECOVERING_DETAIL);
  assert(spec.body.includes("turns that session's auto-resume off"),
    `dialog body does not mention disarming auto-resume: "${spec.body}"`);
  assert(spec.body.startsWith("Auto-resume is re-centring the mount"),
    `dialog body does not lead with the server's own reason: "${spec.body}"`);
  assert(spec.body.endsWith("Force-activate this profile anyway?"),
    `dialog body dropped the confirm question: "${spec.body}"`);
});

test("the force dialog still asks plainly when the cause is the plain busy guard", () => {
  const spec = forceActivateConfirm("a sequence, capture loop or polar alignment is running");
  assert(
    spec.body === "A sequence, capture loop or polar alignment is running. Force-activate this profile anyway?",
    `unexpected body for the plain cause: "${spec.body}"`,
  );
});

console.log(`w5ForceActivateDetail: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
