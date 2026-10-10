// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// apiBodyBudget.test.ts - api.ts's request budget covers the BODY as well as
// the headers (#870).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/lib/__tests__/apiBodyBudget.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `req()` turned a timeout into `ApiError(timedOut)` only around `fetch()`. A
// large read whose headers arrive at once and whose body is still streaming
// when the 15 s run out failed in two ways:
//   * native `AbortSignal.timeout` keeps running, so the body read rejected
//     with a raw DOMException, which no `instanceof ApiError` / `.timedOut`
//     caller recognises (screens showed the browser's "signal timed out");
//   * on the fallback (no `AbortSignal.timeout`: old Safari / WebView) the
//     timer was cleared the moment `fetch()` resolved, so a stalled body hung
//     the load for good.
//
// These cases drive the REAL `api.get` with `fetch` replaced by a stub whose
// headers resolve at once and whose `json()` the case controls, and with the
// budget made controllable: the native signal is replaced by one the case
// aborts, and the fallback's `setTimeout` by one the case fires. No case waits
// for a real 15 s. A body that never settles is what a fetch implementation
// that does not cancel its stream on abort looks like, so every "stalled"
// case also proves the read is held to the budget by the signal itself.
//
// Named mutants (each turns the cases beside it red):
//   B1  req() reads the success body outside the guard (`return res.json()`):
//       N1, N2, F1.
//   B2  req() reads the error body outside the guard (`await res.json()`):
//       N3, N4, F2.
//   B3  done() is called as soon as fetch() resolves, as before: F1, F2.
//   B4  done() is never called (the fallback timer is left running after
//       every request): F3.

/* eslint-disable @typescript-eslint/no-explicit-any */

// `../../api` reads `window` at module scope (lib/base.ts resolves the base
// path from the URL), so a bare import takes this file down before an
// assertion runs. One stub, then a dynamic import.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };

const { api, ApiError } = await import("../../api");

const g = globalThis as any;
const realSetTimeout = g.setTimeout as typeof setTimeout;
const realClearTimeout = g.clearTimeout as typeof clearTimeout;
const realFetch = g.fetch;
const realTimeoutDescriptor = Object.getOwnPropertyDescriptor(AbortSignal, "timeout");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const TIMEOUT_TEXT = "request timed out — server not responding";
const rawTimeout = () => new DOMException("The operation timed out.", "TimeoutError");
const never = (): Promise<unknown> => new Promise(() => { /* a body that never finishes */ });
const tick = () => new Promise<void>((r) => { realSetTimeout(r, 0); });

// ------------------------------------------------------------------- the clock
interface Budget {
  ms: number;
  /** The budget runs out now: a no-op once the timer was cleared. */
  spend(): void;
  /** The fallback's timer was cleared (always false for the native signal). */
  cleared: boolean;
}
interface Rig { budgets: Budget[]; restore(): void }

/** `AbortSignal.timeout` present: a signal the case aborts, as the browser
 *  does when the budget runs out. */
function nativeRig(): Rig {
  const budgets: Budget[] = [];
  g.AbortSignal.timeout = (ms: number): AbortSignal => {
    const ac = new AbortController();
    budgets.push({ ms, cleared: false, spend() { ac.abort(rawTimeout()); } });
    return ac.signal;
  };
  return { budgets, restore: restoreEnv };
}

/** `AbortSignal.timeout` absent (old Safari / WebView): api.ts falls back to
 *  its own timer. `setTimeout` is replaced by one the case fires; a timer
 *  that was cleared never fires, as a real one. */
function fallbackRig(): Rig {
  const budgets: Budget[] = [];
  delete g.AbortSignal.timeout;
  g.setTimeout = (fn: () => void, ms?: number): Budget => {
    const b: Budget = {
      ms: ms ?? 0, cleared: false,
      spend() { if (!b.cleared) fn(); },
    };
    budgets.push(b);
    return b;
  };
  g.clearTimeout = (h: Budget) => { if (budgets.includes(h)) h.cleared = true; };
  return { budgets, restore: restoreEnv };
}
const clearedOf = (b: Budget): boolean => b.cleared;

function restoreEnv(): void {
  g.setTimeout = realSetTimeout;
  g.clearTimeout = realClearTimeout;
  g.fetch = realFetch;
  if (realTimeoutDescriptor) Object.defineProperty(AbortSignal, "timeout", realTimeoutDescriptor);
}

// ----------------------------------------------------------------- the server
function answer(ok: boolean, status: number, json: () => Promise<unknown>, statusText = ""): void {
  g.fetch = async () => ({ ok, status, statusText, json }) as unknown as Response;
}

type Outcome =
  | { kind: "value"; value: unknown }
  | { kind: "threw"; error: unknown }
  | { kind: "hung" };

/** What the call did, or "hung" when it neither returned nor threw within
 *  100 ms of real time: the failure a body with no budget produces. */
async function outcome(p: Promise<unknown>): Promise<Outcome> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const hung = new Promise<Outcome>((res) => { timer = realSetTimeout(() => res({ kind: "hung" }), 100); });
  const settled = p.then(
    (value): Outcome => ({ kind: "value", value }),
    (error): Outcome => ({ kind: "threw", error }),
  );
  const o = await Promise.race([settled, hung]);
  realClearTimeout(timer);
  return o;
}

function expectTimedOut(o: Outcome, label: string): void {
  assert(o.kind !== "hung", `${label}: the call never settled - the body read has no budget`);
  assert(o.kind === "threw", `${label}: the call resolved`);
  const e = (o as { error: unknown }).error;
  assert(e instanceof ApiError,
    `${label}: threw ${e instanceof DOMException ? `a raw DOMException "${e.name}"` : String(e)}, not an ApiError`);
  const err = e as InstanceType<typeof ApiError>;
  assert(err.timedOut === true, `${label}: timedOut is ${err.timedOut}`);
  assert(err.status === 0, `${label}: status is ${err.status}`);
  assert(err.message === TIMEOUT_TEXT, `${label}: message is "${err.message}"`);
}

// ----------------------------------------------------- native AbortSignal.timeout
await test("N1 native: headers at once, the body read rejects with TimeoutError -> ApiError(timedOut)", async () => {
  const rig = nativeRig();
  try {
    answer(true, 200, async () => { throw rawTimeout(); });
    expectTimedOut(await outcome(api.get("/api/flows")), "success body");
  } finally { rig.restore(); }
});

await test("N2 native: headers at once, the body never finishes, the budget runs out -> ApiError(timedOut)", async () => {
  const rig = nativeRig();
  try {
    answer(true, 200, never);
    const p = api.get("/api/flows");
    await tick();
    assert(rig.budgets.length === 1 && rig.budgets[0].ms === 15000,
      `one 15 s budget per request (got ${rig.budgets.map((b) => b.ms).join(",")})`);
    rig.budgets[0].spend();
    expectTimedOut(await outcome(p), "success body");
  } finally { rig.restore(); }
});

await test("N3 native: an error response whose body rejects with TimeoutError -> ApiError(timedOut)", async () => {
  const rig = nativeRig();
  try {
    answer(false, 503, async () => { throw rawTimeout(); }, "Service Unavailable");
    expectTimedOut(await outcome(api.get("/api/flows")), "error body");
  } finally { rig.restore(); }
});

await test("N4 native: an error response whose body never finishes -> ApiError(timedOut)", async () => {
  const rig = nativeRig();
  try {
    answer(false, 503, never, "Service Unavailable");
    const p = api.get("/api/flows");
    await tick();
    rig.budgets[0].spend();
    expectTimedOut(await outcome(p), "error body");
  } finally { rig.restore(); }
});

// ----------------------------------------- fallback (no AbortSignal.timeout)
await test("F1 fallback: headers at once, the body never finishes -> the timer still fires and ApiError(timedOut)", async () => {
  const rig = fallbackRig();
  try {
    answer(true, 200, never);
    const p = api.get("/api/flows");
    await tick();
    assert(rig.budgets.length === 1 && rig.budgets[0].ms === 15000,
      `one 15 s timer per request (got ${rig.budgets.map((b) => b.ms).join(",")})`);
    assert(!clearedOf(rig.budgets[0]),
      "the budget timer was cleared when the headers arrived, with the body still to read");
    rig.budgets[0].spend();
    expectTimedOut(await outcome(p), "success body");
  } finally { rig.restore(); }
});

await test("F2 fallback: an error response whose body never finishes -> ApiError(timedOut)", async () => {
  const rig = fallbackRig();
  try {
    answer(false, 503, never, "Service Unavailable");
    const p = api.get("/api/flows");
    await tick();
    rig.budgets[0].spend();
    expectTimedOut(await outcome(p), "error body");
  } finally { rig.restore(); }
});

await test("F3 fallback: the timer is cleared once the body is read, success or error", async () => {
  const rig = fallbackRig();
  try {
    answer(true, 200, async () => ({ ok: 1 }));
    const o1 = await outcome(api.get("/api/flows"));
    assert(o1.kind === "value" && JSON.stringify((o1 as any).value) === '{"ok":1}', "the body came back");
    assert(clearedOf(rig.budgets[0]), "a request that finished left its budget timer running");

    answer(false, 404, async () => ({ detail: "no flow named x" }), "Not Found");
    const o2 = await outcome(api.get("/api/flows/x"));
    assert(o2.kind === "threw", "the 404 threw");
    assert(clearedOf(rig.budgets[1]), "a failed request left its budget timer running");
  } finally { rig.restore(); }
});

// --------------------------------------------------- what must not change
await test("R1 the fetch itself timing out is still ApiError(timedOut); any other fetch failure is still a network error", async () => {
  const rig = nativeRig();
  try {
    g.fetch = async () => { throw rawTimeout(); };
    expectTimedOut(await outcome(api.get("/api/flows")), "fetch");
    g.fetch = async () => { throw new TypeError("Failed to fetch"); };
    const o = await outcome(api.get("/api/flows"));
    assert(o.kind === "threw" && (o as any).error instanceof ApiError, "an ApiError");
    const e = (o as any).error as InstanceType<typeof ApiError>;
    assert(e.timedOut === false && e.status === 0 && e.message === "network error — server unreachable",
      `a network failure reads "${e.message}" (timedOut ${e.timedOut}, status ${e.status})`);
  } finally { rig.restore(); }
});

await test("R2 a body that arrives in time is returned, on both paths", async () => {
  for (const rig of [nativeRig(), fallbackRig()]) {
    try {
      answer(true, 200, async () => [{ id: "a" }]);
      const o = await outcome(api.get("/api/flows"));
      assert(o.kind === "value" && JSON.stringify((o as any).value) === '[{"id":"a"}]',
        `the body came back as ${JSON.stringify(o)}`);
    } finally { rig.restore(); }
  }
});

await test("R3 an error body that arrives in time keeps its status, code and decoded body", async () => {
  const rig = nativeRig();
  try {
    const body = { detail: { detail: "parts of this flow do not survive", code: "unmapped", unmapped: [1, 2] } };
    answer(false, 409, async () => body, "Conflict");
    const o = await outcome(api.post("/api/flows/f/run", {}));
    assert(o.kind === "threw" && (o as any).error instanceof ApiError, "an ApiError");
    const e = (o as any).error as InstanceType<typeof ApiError>;
    assert(e.status === 409 && e.code === "unmapped" && e.timedOut === false,
      `status ${e.status}, code ${e.code}, timedOut ${e.timedOut}`);
    assert(e.body === body, "the decoded body was dropped");
  } finally { rig.restore(); }
});

await test("R4 an error body that is not JSON still falls back to the status text, not a timeout", async () => {
  const rig = nativeRig();
  try {
    answer(false, 500, async () => { throw new SyntaxError("Unexpected token < in JSON"); }, "Internal Server Error");
    const o = await outcome(api.get("/api/flows"));
    assert(o.kind === "threw" && (o as any).error instanceof ApiError, "an ApiError");
    const e = (o as any).error as InstanceType<typeof ApiError>;
    assert(e.status === 500 && e.timedOut === false && e.message === "Internal Server Error",
      `status ${e.status}, timedOut ${e.timedOut}, message "${e.message}"`);
  } finally { rig.restore(); }
});

restoreEnv();
console.log(`apiBodyBudget.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
