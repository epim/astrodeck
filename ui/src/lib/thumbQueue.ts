// lib/thumbQueue.ts — how many thumbnails may be in flight at once, and what to
// do when the far end says "not so fast".
//
// WHY THIS EXISTS. A desktop gallery at 1920px puts ~41 tiles inside the
// IntersectionObserver's margin at once, and every one of them used to set its
// `src` the moment it appeared. Measured against the relay on 2026-08-10: 41
// requests, 19 answered 429, ZERO tiles rendered. The grid was a wall of empty
// boxes with one tile reading "PREVIEW FAILED".
//
// The relay dampens per-IP with a token bucket — RELAY_HTTP_BURST 40 at
// RELAY_HTTP_RATE 20/s (relay/relay/config.py). A 41-request burst drains it on
// arrival, so the failure is not a slow server, a big file, or a bad frame: it
// is the client asking for a minute's worth of work in one frame of animation.
// Raising the bucket would move the cliff, not remove it — a 4K monitor shows
// more tiles again. The client has to pace itself.
//
// TWO MECHANISMS, and the second is the one that is easy to miss:
//
//   1. A CONCURRENCY CAP. At most `MAX_IN_FLIGHT` thumbnails load at once;
//      the rest queue. Everything still loads, just not all at the same instant.
//
//   2. A SHARED COOLDOWN. The bucket is per-IP, so one tile's 429 is not that
//      tile's bad luck — it is a fact about the whole page. A per-tile retry
//      would have each of the other forty discover the same thing separately,
//      which is the stampede again wearing a hat. One 429 therefore parks
//      EVERY pending request for a moment.
//
// FIFO, deliberately: tiles enter the queue in the order the observer reveals
// them, which is the order they appear down the page, which is the order a
// human reads them.

/** How many thumbnail requests may be in flight at once.
 *
 *  Six, matching the classic per-host connection limit a browser would impose
 *  on its own over HTTP/1.1 — over the relay everything shares one tunnel, so
 *  nothing else is going to impose it for us. Low enough that the token bucket
 *  refills (20/s) faster than we drain it, which is what makes a sustained
 *  scroll never trip the limiter at all. */
export const MAX_IN_FLIGHT = 6;

/** How long everything waits after a 429 with no `Retry-After` to go on. */
export const DEFAULT_COOLDOWN_MS = 1500;

/** Cap on the backoff, so a long stall still recovers in a bounded time. */
export const MAX_COOLDOWN_MS = 15_000;

type Waiter = { run: () => void; cancelled: boolean };

let inFlight = 0;
const waiting: Waiter[] = [];
let cooldownUntil = 0;
let consecutive429 = 0;
let timer: ReturnType<typeof setTimeout> | null = null;

/** Test seam: the clock and the timer, so a test never sleeps in real time. */
export const _clock = { now: () => Date.now() };

function pump(): void {
  if (timer) { clearTimeout(timer); timer = null; }
  const now = _clock.now();
  if (now < cooldownUntil) {
    timer = setTimeout(pump, cooldownUntil - now);
    return;
  }
  while (inFlight < MAX_IN_FLIGHT && waiting.length) {
    const w = waiting.shift()!;
    if (w.cancelled) continue;
    inFlight += 1;
    w.run();
  }
}

/**
 * Wait for a slot. Resolves with a `release` that MUST be called once the
 * request has finished — success, failure, or abandonment. The returned
 * `cancel` drops a still-queued waiter (a tile unmounted by a fast scroll).
 */
export function acquire(): { slot: Promise<() => void>; cancel: () => void } {
  const w: Waiter = { run: () => {}, cancelled: false };
  const slot = new Promise<() => void>((resolve) => {
    let released = false;
    const release = () => {
      // Idempotent: <img> can fire load and error in odd orders across
      // browsers, and double-releasing would inflate the cap silently.
      if (released) return;
      released = true;
      inFlight -= 1;
      pump();
    };
    w.run = () => resolve(release);
  });
  waiting.push(w);
  pump();
  return {
    slot,
    cancel: () => {
      w.cancelled = true;
      const i = waiting.indexOf(w);
      if (i >= 0) waiting.splice(i, 1);
    },
  };
}

/**
 * Report that the far end pushed back. Parks every pending request, with an
 * exponential backoff over repeated 429s so a genuinely saturated link is not
 * hammered on a fixed cadence.
 *
 * `retryAfterS` is the `Retry-After` header when the server sent one — it is
 * the only party that knows how long its bucket needs, so it always wins.
 */
export function noteRateLimited(retryAfterS?: number | null): void {
  consecutive429 += 1;
  const backoff = Number.isFinite(retryAfterS) && (retryAfterS as number) > 0
    ? (retryAfterS as number) * 1000
    : Math.min(DEFAULT_COOLDOWN_MS * 2 ** (consecutive429 - 1), MAX_COOLDOWN_MS);
  cooldownUntil = Math.max(cooldownUntil, _clock.now() + backoff);
  pump();
}

/** Report a clean response, which is what ends the backoff escalation. */
export function noteSucceeded(): void {
  consecutive429 = 0;
}

/** How long a caller should wait before retrying, 0 when the coast is clear. */
export function cooldownRemainingMs(): number {
  return Math.max(0, cooldownUntil - _clock.now());
}

/** Test seam only. */
export function _reset(): void {
  inFlight = 0;
  waiting.length = 0;
  cooldownUntil = 0;
  consecutive429 = 0;
  if (timer) { clearTimeout(timer); timer = null; }
}

/** Test seam only — what the queue thinks it is doing. */
export function _state(): { inFlight: number; waiting: number; cooldownMs: number } {
  return {
    inFlight,
    waiting: waiting.filter((w) => !w.cancelled).length,
    cooldownMs: cooldownRemainingMs(),
  };
}
