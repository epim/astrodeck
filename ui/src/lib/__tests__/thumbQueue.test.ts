// thumbQueue.test.ts — the pacing that stops the gallery asking for a minute's
// work in one frame of animation.
//
//   Run directly:  npx tsx src/lib/__tests__/thumbQueue.test.ts
//   Also run by:   npm test
//
// Same dependency-free inline-assert harness as gallery.test.ts — no framework,
// no jsdom. Everything here is arithmetic and bookkeeping.
//
// WHAT THIS FILE STOPS. Measured against the relay 2026-08-10 at 1920x1080 @2x:
// the grid put 41 tiles inside the IntersectionObserver's margin, set 41 `src`
// attributes at once, and the relay's per-IP token bucket (RELAY_HTTP_BURST 40
// at RELAY_HTTP_RATE 20/s) answered 19 of them 429. ZERO tiles decoded. The
// screen was a wall of empty boxes, one captioned PREVIEW FAILED.
//
// Two mechanisms, and the second is the one that is easy to get wrong: a cap on
// how many load at once, and a cooldown SHARED by every tile — because the
// bucket is per-IP, so one tile's 429 is a fact about the whole page.

import {
  MAX_IN_FLIGHT, DEFAULT_COOLDOWN_MS, MAX_COOLDOWN_MS,
  acquire, noteRateLimited, noteSucceeded, cooldownRemainingMs,
  _reset, _state, _clock,
} from "../thumbQueue";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  _reset();
  const realNow = _clock.now;
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { _clock.now = realNow; _reset(); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** Freeze the clock at `t`, returning a setter so a test can advance it. */
function frozen(t = 0): (ms: number) => void {
  let now = t;
  _clock.now = () => now;
  return (ms: number) => { now += ms; };
}

// ------------------------------------------------------- the concurrency cap

await test("only MAX_IN_FLIGHT start; the rest queue", async () => {
  const gets = Array.from({ length: 41 }, () => acquire());
  await Promise.resolve();
  eq(_state().inFlight, MAX_IN_FLIGHT, "in flight");
  eq(_state().waiting, 41 - MAX_IN_FLIGHT, "queued");
  gets.forEach((g) => g.cancel());
});

await test("a released slot is handed to the next waiter", async () => {
  const first = acquire();
  const rest = Array.from({ length: MAX_IN_FLIGHT }, () => acquire());
  const release = await first.slot;
  eq(_state().inFlight, MAX_IN_FLIGHT, "still full");
  release();
  await Promise.resolve();
  eq(_state().waiting, 0, "the waiter was let in");
  rest.forEach((r) => r.cancel());
});

await test("every queued tile eventually runs — nothing is stranded", async () => {
  let started = 0;
  await Promise.all(Array.from({ length: 41 }, () =>
    acquire().slot.then((release) => { started += 1; release(); })));
  eq(started, 41, "tiles that ran");
});

await test("releasing twice does not inflate the cap", async () => {
  const release = await acquire().slot;
  release();
  release();
  eq(_state().inFlight, 0, "in flight after a double release");
});

await test("cancelling a queued waiter frees the queue, not a slot", async () => {
  const held = Array.from({ length: MAX_IN_FLIGHT }, () => acquire());
  await Promise.resolve();
  const queued = acquire();
  eq(_state().waiting, 1, "queued before cancel");
  queued.cancel();
  eq(_state().waiting, 0, "queued after cancel");
  eq(_state().inFlight, MAX_IN_FLIGHT, "slots untouched");
  held.forEach((h) => h.cancel());
});

await test("a cancelled waiter never receives a slot when one frees", async () => {
  const held = Array.from({ length: MAX_IN_FLIGHT }, () => acquire());
  await Promise.resolve();
  let ran = false;
  const queued = acquire();
  void queued.slot.then(() => { ran = true; });
  queued.cancel();
  (await held[0].slot)();
  await Promise.resolve();
  await Promise.resolve();
  assert(!ran, "a cancelled tile was started anyway");
  held.slice(1).forEach((h) => h.cancel());
});

// ------------------------------------------------------- the shared cooldown

await test("one 429 parks EVERY pending request, not just the tile that saw it", async () => {
  frozen(1_000_000);
  const held = Array.from({ length: MAX_IN_FLIGHT }, () => acquire());
  await Promise.resolve();
  let ran = false;
  void acquire().slot.then(() => { ran = true; });

  noteRateLimited(null);
  eq(cooldownRemainingMs(), DEFAULT_COOLDOWN_MS, "cooldown");

  // Free a slot DURING the cooldown. The waiter must still not start — this is
  // the whole point: the bucket is shared, so the page waits together.
  (await held[0].slot)();
  await Promise.resolve();
  await Promise.resolve();
  assert(!ran, "a tile started while the relay was still saying 429");
  held.slice(1).forEach((h) => h.cancel());
});

await test("Retry-After beats our own guess — the server knows its bucket", () => {
  frozen(5_000);
  noteRateLimited(7);
  eq(cooldownRemainingMs(), 7000, "honoured Retry-After");
});

await test("backs off exponentially over repeated 429s", () => {
  const advance = frozen(0);
  noteRateLimited(null);
  const first = cooldownRemainingMs();
  advance(first);
  noteRateLimited(null);
  assert(cooldownRemainingMs() > first,
         `second backoff ${cooldownRemainingMs()} did not exceed first ${first}`);
});

await test("the backoff is capped, so a stall still recovers", () => {
  const advance = frozen(0);
  for (let i = 0; i < 20; i++) { noteRateLimited(null); advance(cooldownRemainingMs()); }
  noteRateLimited(null);
  assert(cooldownRemainingMs() <= MAX_COOLDOWN_MS,
         `backoff ${cooldownRemainingMs()} exceeded the ${MAX_COOLDOWN_MS} cap`);
});

await test("a success resets the escalation", () => {
  const advance = frozen(0);
  noteRateLimited(null);
  noteRateLimited(null);
  advance(cooldownRemainingMs());
  noteSucceeded();
  noteRateLimited(null);
  eq(cooldownRemainingMs(), DEFAULT_COOLDOWN_MS, "cooldown after a success");
});

await test("a shorter Retry-After never shortens a cooldown already in force", () => {
  frozen(0);
  noteRateLimited(10);
  noteRateLimited(1);
  eq(cooldownRemainingMs(), 10_000, "cooldown");
});

await test("requests resume once the cooldown expires", async () => {
  const advance = frozen(0);
  const held = Array.from({ length: MAX_IN_FLIGHT }, () => acquire());
  await Promise.resolve();
  let ran = false;
  void acquire().slot.then(() => { ran = true; });
  noteRateLimited(null);
  (await held[0].slot)();
  await Promise.resolve();
  assert(!ran, "started during the cooldown");
  advance(DEFAULT_COOLDOWN_MS);
  // A release after the cooldown has expired re-pumps the queue.
  (await held[1].slot)();
  await Promise.resolve();
  await Promise.resolve();
  assert(ran, "never resumed after the cooldown expired");
  held.slice(2).forEach((h) => h.cancel());
});

// ------------------------------------- the cap has to fit the relay's bucket

await test("MAX_IN_FLIGHT stays well under RELAY_HTTP_BURST (40)", () => {
  // Not a style preference. The relay refills at RELAY_HTTP_RATE 20/s, so a cap
  // below that drains slower than the bucket refills — which is what makes a
  // sustained scroll never trip the limiter at all rather than trip it slower.
  assert(MAX_IN_FLIGHT < 20,
         `a cap of ${MAX_IN_FLIGHT} can still outrun the relay's refill rate`);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
if (failures.length) console.log(failures.join("\n"));
console.log(`thumbQueue.test.ts: ${passed}/${total} passed`);
export default { passed, failed, total };
