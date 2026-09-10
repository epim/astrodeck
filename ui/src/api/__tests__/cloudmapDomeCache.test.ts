// The dome grid is fetched ONCE per cycle, not once per widget (T-R7-21a #21).
//
//   Run directly:  npx tsx src/api/__tests__/cloudmapDomeCache.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// WHAT THIS IS ABOUT. `#/sky` has two independent 60 s pollers over
// `/api/cloudmap/dome`: the finder's cloud layer (`next/hubs/sky/finder/
// model.ts`) and the dome card's own panel (`components/cloudmap/
// SkyDomePanel`). They are not phase-locked, so the route was asked twice per
// minute for the same grid - and each ask makes the SERVER walk 540 rays
// through the cloud volume. That is the cost, and it lands on the machine that
// is also running the sequence.
//
// The cache is a module-level TTL plus one shared in-flight promise, so:
//   1. two callers inside the window make ONE request and both get the grid;
//   2. two callers a millisecond apart - before any answer exists - also make
//      one request, which the TTL alone would not achieve;
//   3. a caller past the TTL fetches again, so the pollers keep their cadence;
//   4. a FAILURE is not cached, or a feed that blinked would stay dead for a
//      minute after it came back;
//   5. two different resolutions are two different questions and never share.
//
// Convention: no jsdom needed; printed tally plus the `{ passed, failed, total }`
// export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// `../cloudmap` reaches `api.ts` -> `lib/base.ts`, which reads
// `window.location` at module scope. Stub first, import dynamically (the
// `coolingConfigPayload.test.ts` idiom).
(globalThis as unknown as { window: unknown }).window = {
  location: { pathname: "/", origin: "http://local" },
} as unknown as Window;

interface Ask { url: string }
const asks: Ask[] = [];
let failNext = false;
/** While true, every fetch parks until `releaseHeld()` - so a second caller can
 *  be made to arrive while the first request is genuinely still in the air. */
let holdMode = false;
const held: (() => void)[] = [];
const releaseHeld = (): void => {
  holdMode = false;
  while (held.length > 0) (held.pop() as () => void)();
};

(globalThis as any).fetch = async (url: any, _init: any) => {
  asks.push({ url: String(url) });
  if (holdMode) await new Promise<void>((r) => { held.push(r); });
  if (failNext) {
    return {
      ok: false, status: 503, statusText: "Service Unavailable",
      headers: { get: () => "application/json" },
      json: async () => ({ detail: "cloudmap feed down" }),
      text: async () => '{"detail":"cloudmap feed down"}',
    };
  }
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => ({
      enabled: true, observed_at: "2026-09-10T04:00:00Z", stale: false,
      rows: [[0.1, 0.2]], alt_start: 0, alt_step: 6, az_step: 10,
    }),
    text: async () => "{}",
  };
};

const { DOME_TTL_MS, getCloudmapDome, resetCloudmapDomeCache } =
  await import("../cloudmap");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
const domeAsks = (): Ask[] => asks.filter((a) => a.url.includes("/api/cloudmap/dome"));

// --------------------------------------------------- 0. the vacuity guard
await test("the stub really answers this route - the vacuity guard", async () => {
  resetCloudmapDomeCache();
  asks.length = 0;
  const grid = await getCloudmapDome(6, 10);
  eq(domeAsks().length, 1, "the first call did not reach fetch at all:");
  assert(grid != null && grid.enabled === true,
    "the wrapper did not return the stubbed grid, so every assertion below is about nothing");
  assert(domeAsks()[0].url.includes("alt_step=6&az_step=10"),
    `the resolution is missing from the URL: "${domeAsks()[0].url}"`);
});

// ------------------------------------- 1. THE ONE THAT MATTERS: one request
await test("the SECOND widget inside the window makes no second request", async () => {
  resetCloudmapDomeCache();
  asks.length = 0;
  const first = await getCloudmapDome(6, 10);
  const second = await getCloudmapDome(6, 10);
  eq(domeAsks().length, 1,
    "#/sky asked the server to walk 540 rays twice for one grid - the finder's cloud "
    + "layer and the dome card each fetched their own copy:");
  eq(second, first, "the second caller got a different object than the cache holds");
});

await test("two callers before any answer exists share the in-flight request", async () => {
  // The TTL alone cannot do this: nothing is cached until the first answer
  // lands, and both pollers can fire in the same tick after a route change.
  resetCloudmapDomeCache();
  asks.length = 0;
  holdMode = true;
  const a = getCloudmapDome(6, 10);
  const b = getCloudmapDome(6, 10);
  const inFlight = domeAsks().length;
  // Released before the assertion, and unconditionally: under a sabotage that
  // opens two requests, leaving either parked would hang the file instead of
  // failing it, and a hang reads as a broken harness rather than a defect.
  releaseHeld();
  const [ga, gb] = await Promise.all([a, b]);
  eq(inFlight, 1, "two simultaneous callers each opened their own request:");
  eq(ga, gb, "the two callers resolved to different objects");
  eq(domeAsks().length, 1, "a second request appeared after the first resolved:");
});

// ------------------------------------------- 2. the pollers keep their cadence
await test("a caller past the TTL fetches again", async () => {
  resetCloudmapDomeCache();
  asks.length = 0;
  const realNow = Date.now;
  let t = 1_000_000;
  Date.now = () => t;
  try {
    await getCloudmapDome(6, 10);
    t += DOME_TTL_MS - 1;
    await getCloudmapDome(6, 10);
    eq(domeAsks().length, 1, "a call one millisecond INSIDE the window refetched:");
    t += 2;
    await getCloudmapDome(6, 10);
    eq(domeAsks().length, 2,
      "the cache outlived its TTL: a 60 s poller would be drawing a stale grid forever:");
  } finally {
    Date.now = realNow;
  }
});

await test("the TTL is under the pollers' own 60 s cadence", () => {
  // Both callers refresh every 60_000 ms. A TTL at or above that would hand a
  // poller its own previous answer and the layer would stop updating.
  assert(DOME_TTL_MS < 60_000,
    `DOME_TTL_MS is ${DOME_TTL_MS} ms, at or above the 60 s poll: the dome would freeze`);
  assert(DOME_TTL_MS > 5_000,
    `DOME_TTL_MS is ${DOME_TTL_MS} ms - too short to coalesce two unsynchronised pollers`);
});

// --------------------------------------------------- 3. a failure is not cached
await test("a failed fetch is not remembered", async () => {
  resetCloudmapDomeCache();
  asks.length = 0;
  failNext = true;
  let threw = false;
  try { await getCloudmapDome(6, 10); } catch { threw = true; }
  failNext = false;
  assert(threw, "the 503 resolved instead of rejecting - the fixture is wrong");
  const grid = await getCloudmapDome(6, 10);
  eq(domeAsks().length, 2,
    "the retry never left the tab: a feed that blinked would stay dead for a whole TTL:");
  assert(grid.enabled === true, "the retry did not return the recovered grid");
});

// ------------------------------------- 4. two resolutions are two questions
await test("a different resolution is never served from another one's cache", async () => {
  resetCloudmapDomeCache();
  asks.length = 0;
  await getCloudmapDome(6, 10);
  await getCloudmapDome(3, 5);
  eq(domeAsks().length, 2,
    "a finer grid was answered with the coarse one already in hand:");
  assert(domeAsks()[1].url.includes("alt_step=3&az_step=5"),
    `the second request asked for the wrong resolution: "${domeAsks()[1].url}"`);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
console.log(`cloudmapDomeCache.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
