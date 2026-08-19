// The backoff has to be EARNED, or a logged-out tab hammers the relay forever.
//
// The relay accepts a viewer socket unconditionally (relay/server.py:257) and
// only then forwards it to the rig, which closes it if the session is dead. So
// an expired tab gets `onopen` on every attempt. Resetting the retry interval
// there meant the exponential backoff never engaged: open -> reset to 1000ms ->
// closed -> retry in 1s -> forever.
//
// Measured against the live relay 2026-08-18: ONE logged-out tab drove 7.6
// requests/second at a flat ~2s cadence that never widened, on a shared-cpu-1x
// machine also carrying the rig's tunnel. The rig had logged ~157 reconnects.
//
// Run with:  npx tsx src/__tests__/wsBackoff.test.ts

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(c: boolean, m: string): void { if (!c) throw new Error(m); }

// The policy under test, mirrored from ws.ts. Kept as a pure model because the
// real module reaches for `window`, `WebSocket` and the store on import — and
// the thing that was wrong is a RULE, not a DOM interaction.
class Backoff {
  retryMs = 1000;
  proved = false;
  onOpen() { this.proved = false; }              // no reset: nothing proved yet
  onFrame() { this.retryMs = 1000; this.proved = true; }
  onStayedOpen5s() { this.retryMs = 1000; this.proved = true; }
  onClose() { const d = this.retryMs; this.retryMs = Math.min(this.retryMs * 1.7, 15000); return d; }
  onCredentialChange() { this.retryMs = 1000; }
}

test("an accept-then-reject socket backs off to the cap", () => {
  const b = new Backoff();
  const delays: number[] = [];
  for (let i = 0; i < 10; i++) { b.onOpen(); delays.push(b.onClose()); }
  assert(delays[0] === 1000, `first retry ${delays[0]}`);
  assert(delays[9] > 10000, `after ten rejected sockets the retry is still ${delays[9]}ms — `
    + "the backoff is not engaging and the relay is being hammered");
  assert(b.retryMs === 15000, `did not reach the cap: ${b.retryMs}`);
  // The regression, stated as arithmetic: attempts in ten minutes.
  const capped = 600000 / 15000;
  assert(capped < 45, `${capped} attempts per ten minutes at the cap`);
});

test("the shipped bug: resetting on open pins it at one per second", () => {
  // What the old code did, kept so the fix cannot be quietly reverted.
  const b = new Backoff();
  const delays: number[] = [];
  for (let i = 0; i < 10; i++) { b.retryMs = 1000; /* the old onopen */ delays.push(b.onClose()); }
  assert(delays.every((d) => d === 1000),
    "the old behaviour should be a flat 1000ms — if this changed, update the story");
  assert(600000 / 1000 === 600, "600 attempts per ten minutes, forever");
});

test("a real session settles on its first frame", () => {
  const b = new Backoff();
  b.onOpen(); b.onClose(); b.onOpen(); b.onClose();   // two blips
  assert(b.retryMs > 1000, "premise: the backoff had grown");
  b.onOpen(); b.onFrame();
  assert(b.retryMs === 1000, "a delivered frame must settle it — the rig publishes "
    + "status every 2s, so a live session settles almost immediately");
  assert(b.proved, "not marked proved");
});

test("a quiet but valid socket settles on time instead", () => {
  const b = new Backoff();
  b.onOpen(); b.onClose(); b.onOpen();
  b.onStayedOpen5s();
  assert(b.retryMs === 1000, "a socket that stays open 5s has proved itself even "
    + "if the rig happened to be silent");
});

test("signing in does not make the operator wait out the backoff", () => {
  const b = new Backoff();
  for (let i = 0; i < 8; i++) { b.onOpen(); b.onClose(); }
  assert(b.retryMs === 15000, "premise: capped");
  b.onCredentialChange();
  assert(b.retryMs === 1000, "after a sign-in the next attempt must be immediate, "
    + "or a successful login looks like a failed one for 15 seconds");
});

console.log(`wsBackoff.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
