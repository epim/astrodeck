// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// retryLoad.test.ts -- the one retry policy the one-shot screens share (#859).
//
//   Run directly:  npx tsx src/lib/__tests__/retryLoad.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// On 2026-10-07 one request ran past the UI's 15 s budget during a brief disk
// stall and the flow library stranded on "server not responding" until the
// owner reloaded. `retryTransient` asks a transient failure again by itself
// (2 s, 5 s, 15 s) and never retries the home's own answer. These cases drive
// the REAL function with REAL ApiErrors; the waits go through the test hatch,
// which records them instead of sleeping.
//
// Named mutants (each turns the case beside it red):
//   R1  isTransientLoadError returns false.
//   R2  isTransientLoadError drops the status conditions (any ApiError is
//       transient); and TRANSIENT_HTTP_STATUSES.has(s) -> s >= 500.
//   R3  drop `|| status === 0`.
//   R4  loop bound `i >= delays.length` -> `i > delays.length`.
//   R5  delete the post-sleep `if (opts?.stop?.()) throw e`.
//   R6  drop the TimeoutError clause.
//   R7  the line formats `of` as `attempt`.
//   R8  drop TRANSIENT_HTTP_STATUSES.has(status); and delete 503 from the set.

// `../../api` reads `window` at module scope (lib/base.ts), so one stub, then
// a dynamic import, the shape apiErrorPayload.test.ts uses.
(globalThis as unknown as { window: unknown }).window = {
  location: { pathname: "/", origin: "http://local" },
} as unknown as Window;

const { ApiError } = await import("../../api");
const {
  retryTransient, retryingLine, setRetrySleepForTests,
} = await import("../retryLoad");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq(a: unknown, b: unknown, msg: string): void {
  const sa = JSON.stringify(a);
  const sb = JSON.stringify(b);
  if (sa !== sb) throw new Error(`${msg}\n  expected ${sb}\n  got      ${sa}`);
}

let waits: number[] = [];
setRetrySleepForTests(async (ms) => { waits.push(ms); });

/** A load that throws each of `errs` in turn, then answers `value`. */
function scripted<T>(errs: unknown[], value: T): { load: () => Promise<T>; calls: () => number } {
  let n = 0;
  return {
    load: async () => {
      const i = n++;
      if (i < errs.length) throw errs[i];
      return value;
    },
    calls: () => n,
  };
}

const timeout = () => new ApiError("request timed out — server not responding", 0, true);

await test("R1 a load that times out once and then answers returns the answer", async () => {
  waits = [];
  const s = scripted([timeout()], "the library");
  const seen: unknown[] = [];
  const got = await retryTransient(s.load, { onRetry: (r) => seen.push(r) });
  eq(got, "the library", "the answer after the retry");
  eq(s.calls(), 2, "two tries");
  eq(seen, [{ attempt: 2, of: 4 }], "onRetry names the try about to happen");
  eq(waits, [2000], "the first wait is 2 s");
});

await test("R2 a refusal is thrown at once", async () => {
  for (const status of [500, 404]) {
    waits = [];
    const refusal = new ApiError("x", status);
    const s = scripted([refusal], "never");
    let thrown: unknown = null;
    try { await retryTransient(s.load); } catch (e) { thrown = e; }
    eq(thrown === refusal, true, `status ${status} must be rethrown as it came`);
    eq(s.calls(), 1, `status ${status} must not be asked again`);
    eq(waits, [], `status ${status} must not wait`);
  }
});

await test("R3 a network error (status 0, not timed out) is retried", async () => {
  waits = [];
  const s = scripted([new ApiError("network error — server unreachable", 0, false)], 1);
  await retryTransient(s.load);
  eq(s.calls(), 2, "two tries");
});

await test("R4 the waits are spent, then the last error is thrown", async () => {
  waits = [];
  const errs = [timeout(), timeout(), timeout(), timeout(), timeout()];
  const s = scripted(errs, "never");
  let thrown: unknown = null;
  try { await retryTransient(s.load); } catch (e) { thrown = e; }
  eq(s.calls(), 4, "four tries in all");
  eq(waits, [2000, 5000, 15000], "the three waits");
  eq(thrown === errs[3], true, "the fourth error is the one rethrown");
});

await test("R5 a stop that turns true during the wait ends it with no further request", async () => {
  waits = [];
  let stopped = false;
  setRetrySleepForTests(async (ms) => { waits.push(ms); stopped = true; });
  try {
    const s = scripted([timeout(), timeout()], "never");
    let thrown: unknown = null;
    try { await retryTransient(s.load, { stop: () => stopped }); } catch (e) { thrown = e; }
    eq(s.calls(), 1, "no request after the stop");
    eq(thrown !== null, true, "the error is rethrown");
  } finally {
    setRetrySleepForTests(async (ms) => { waits.push(ms); });
  }
});

await test("R6 a TimeoutError from a body read counts as transient", async () => {
  waits = [];
  const s = scripted([new DOMException("t", "TimeoutError")], 1);
  await retryTransient(s.load);
  eq(s.calls(), 2, "two tries");
});

await test("R7 the retrying line", () => {
  eq(retryingLine({ attempt: 3, of: 4 }),
    "No answer from the rig yet. Asking again by itself (try 3 of 4).",
    "the one line every retrying screen shows");
});

for (const [status, text] of [
  [502, "home not connected"], [503, "too many concurrent requests"], [504, "home did not respond"],
] as const) {
  await test(`R8 the relay's own ${status} is retried`, async () => {
    waits = [];
    const s = scripted([new ApiError(text, status)], "answer");
    eq(await retryTransient(s.load), "answer", "the answer after the retry");
    eq(s.calls(), 2, `a ${status} is asked again`);
  });
}

setRetrySleepForTests(null);

console.log(`retryLoad.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
