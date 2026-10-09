// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// retryLoad.ts -- one retry policy for the screens that load once (#859).
//
// On 2026-10-07 one request ran past the UI's 15 s budget during a brief disk
// stall on the rig (#858). The flow library said "server not responding" and
// stayed that way: it loaded once per mount, and only a human pressing RETRY or
// reloading could bring it back, although the server answered in under 0.25 s
// a few minutes later. Six screens had the same shape.
//
// The policy, in one place so it is not six copies:
//   * A transient failure (a timeout, a network error, or a proxy's
//     502/503/504) is asked again by itself after 2 s, 5 s and 15 s, while the
//     screen shows `retryingLine` and never the error. After the last try the
//     error and RETRY show exactly as before.
//   * Any other HTTP status is the home's own answer and is never retried.
//   * A failed screen re-asks once when the websocket comes back up or the tab
//     becomes visible again (`useRetryOnReturn`).
//   * ONLY idempotent reads (GET). Never wrap a POST, PUT, PATCH or DELETE.
//
// `ApiError` is imported as a TYPE only: api.ts imports lib/base.ts, which
// reads `window.location` at module load, so a runtime import would make this
// helper unloadable in a plain node test.
import { useEffect, useRef } from "react";
import type { ApiError } from "../api";

/** Waits between tries after a transient failure (#859). On timeouts (15 s
 *  each) the tries run 0-15, 17-32, 37-52 and 67-82 s; on immediate failures
 *  at 0, 2, 7 and 22 s, which covers a relay re-dial (its backoff is capped
 *  at 15 s). */
export const LOAD_RETRY_DELAYS_MS: readonly number[] = [2000, 5000, 15000];

/** HTTP statuses a PROXY answers with when the home is unreachable or busy:
 *  the relay's own 502/503/504 (relay/relay/server.py: "home not connected",
 *  "too many concurrent requests", "home did not respond"). None of the routes
 *  the retrying screens read answers these itself. */
export const TRANSIENT_HTTP_STATUSES: ReadonlySet<number> = new Set([502, 503, 504]);

/** The try about to happen, 2-based, and how many there will be in all. */
export interface LoadRetry { attempt: number; of: number; }

/** The request got no answer from the home: a timeout, a network error, or a
 *  proxy's 502/503/504. Any other HTTP status is the home's answer and is never
 *  retried. */
export function isTransientLoadError(e: unknown): boolean {
  if (typeof e !== "object" || e === null) return false;
  const err = e as Partial<ApiError> & { name?: unknown };
  if (err.name === "ApiError") {
    const status = typeof err.status === "number" ? err.status : NaN;
    return err.timedOut === true || status === 0 || TRANSIENT_HTTP_STATUSES.has(status);
  }
  // AbortSignal.timeout firing while the body is read (api.ts converts only
  // the fetch call itself into an ApiError).
  return err.name === "TimeoutError";
}

export interface RetryTransientOptions {
  /** Default LOAD_RETRY_DELAYS_MS. */
  delaysMs?: readonly number[];
  /** Called BEFORE each wait. */
  onRetry?: (r: LoadRetry) => void;
  /** True when the caller no longer wants the answer. */
  stop?: () => boolean;
}

const realSleep = (ms: number): Promise<void> =>
  new Promise<void>((resolve) => { setTimeout(resolve, ms); });
let sleepImpl: ((ms: number) => Promise<void>) | null = null;
function sleep(ms: number): Promise<void> {
  return (sleepImpl ?? realSleep)(ms);
}

/** Test hatch: replace the wait (null restores setTimeout). */
export function setRetrySleepForTests(fn: ((ms: number) => Promise<void>) | null): void {
  sleepImpl = fn;
}

/** Runs `load`; after a transient failure waits and runs it again, at most
 *  delays.length more times. Rethrows a non-transient error at once and the
 *  last error once the waits are spent or `stop()` is true. ONLY FOR IDEMPOTENT
 *  READS (GET): never wrap a POST, PUT, PATCH or DELETE in it. */
export async function retryTransient<T>(
  load: () => Promise<T>,
  opts?: RetryTransientOptions,
): Promise<T> {
  const delays = opts?.delaysMs ?? LOAD_RETRY_DELAYS_MS;
  const of = delays.length + 1;
  for (let i = 0; ; i++) {
    try {
      return await load();
    } catch (e) {
      if (i >= delays.length || !isTransientLoadError(e) || opts?.stop?.()) throw e;
      opts?.onRetry?.({ attempt: i + 2, of });
      await sleep(delays[i]);
      if (opts?.stop?.()) throw e;
    }
  }
}

/** The one line every retrying screen shows. */
export function retryingLine(r: LoadRetry): string {
  return `No answer from the rig yet. Asking again by itself (try ${r.attempt} of ${r.of}).`;
}

/** Re-asks a FAILED load once when the websocket comes back up or the tab
 *  becomes visible. `isFailed` is CALLED AT EVENT TIME, never cached at render:
 *  it must read live state (the store via getState(), or refs the load sets
 *  synchronously), so it returns false the moment a re-ask starts, and two
 *  handlers firing in one event cannot both start one. `was` is seeded from
 *  the first `connected`, so mounting with the socket already up is not a
 *  reconnect. */
export function useRetryOnReturn(
  isFailed: () => boolean,
  retry: () => void,
  connected: boolean,
): void {
  const failedRef = useRef(isFailed);
  failedRef.current = isFailed;
  const retryRef = useRef(retry);
  retryRef.current = retry;
  const was = useRef(connected);
  useEffect(() => {
    const before = was.current;
    was.current = connected;
    if (!before && connected && failedRef.current()) retryRef.current();
  }, [connected]);
  useEffect(() => {
    if (typeof document === "undefined") return;
    const onVis = () => {
      if (document.visibilityState === "visible" && failedRef.current()) retryRef.current();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);
}
