// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// api.ts — robust fetch with per-endpoint timeouts, typed errors, and an
// AbortController fallback for old Safari/WebView (reliability spec §12,
// settings spec §2.1).
import { BASE } from "./lib/base";
import { parseApiError } from "./lib/apiError";

// Re-exported so `ApiError.code`'s parsing logic is reachable (and testable)
// via api.ts, while living in lib/apiError.ts to stay dependency-free (no
// window access at module load) — see that file for the FastAPI nested-detail
// rationale.
export { parseApiError } from "./lib/apiError";

export class ApiError extends Error {
  status: number;
  timedOut: boolean;
  code?: string;
  /** Target-resource id some 409 payloads carry (e.g. name_collision's
   *  existing-location id) — the authoritative reference for a follow-up
   *  overwrite, parsed by lib/apiError.ts. */
  id?: string;
  /** The DECODED response body, verbatim.
   *
   *  Some 409s are a QUESTION, not a failure, and they carry the material the
   *  question is about: `POST /api/flows/{id}/run` answers 409 `code:
   *  "unmapped"` with the list of graph settings the compile drops, so the UI
   *  can show them and ask "run anyway?".
   *
   *  Message, code and id used to be all that survived — the body was decoded,
   *  read for those three fields, and dropped on the floor. `flowsRun` read
   *  `err.unmapped` and `err.body?.unmapped`, neither of which existed on this
   *  class, so its guard could never be true: EVERY flow carrying any loss fell
   *  through to "could not start", and since almost every flow carries one
   *  (SAFETY, SLEW, AUTOFOCUS, GUIDE, REPORT, CONDITION, REFOCUS and ABORT all
   *  have inert params), RUN could not start anything at all. Verified on the
   *  rig 2026-08-18: press RUN, get a 409, no dialog, no way forward.
   *
   *  `unknown` on purpose — a caller narrows what it expects. Typing it would
   *  make this class know every endpoint's error shape. */
  body?: unknown;
  constructor(
    message: string,
    status: number,
    timedOut = false,
    code?: string,
    id?: string,
    body?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.timedOut = timedOut;
    this.code = code;
    this.id = id;
    this.body = body;
  }
}

// Per-endpoint budgets: the synchronous connect path does real device I/O
// (build_nina_rig handshake; NINA read timeout is 120s) — a blanket 15s aborts a
// legitimately-slow bridge and desyncs UI vs backend.
// Keep in step with server/astrodeck/api/slow_requests.py (UI_REQUEST_BUDGET_S
// and _UI_LONG_BUDGETS), which logs a request past these budgets as a warning.
function timeoutFor(path: string): number {
  if (/\/api\/connect\/(nina|alpaca|phd2)/.test(path)) return 130000;
  if (/\/api\/discover/.test(path)) return 30000;
  return 15000; // _spawn'd ops return {started} immediately, so 15s is plenty
}

// AbortSignal.timeout is recent; fall back for old iPad Safari / Android WebView
// or timeouts mis-label as a generic "network error".
function timeoutSignal(ms: number): { signal: AbortSignal; done(): void } {
  if (typeof AbortSignal !== "undefined" && "timeout" in AbortSignal) {
    return { signal: (AbortSignal as unknown as { timeout(ms: number): AbortSignal }).timeout(ms), done() {} };
  }
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(new DOMException("Timeout", "TimeoutError")), ms);
  return { signal: ac.signal, done() { clearTimeout(t); } };
}

const isTimeout = (e: unknown): boolean => e instanceof DOMException && e.name === "TimeoutError";

/** The one ApiError a spent budget becomes, whether it ran out waiting for the
 *  headers or for the body (#870). */
const timedOutError = (): ApiError => new ApiError("request timed out — server not responding", 0, true);

/** Holds a body read to the request's budget (#870).
 *
 *  The budget covers the whole request, not only `fetch()`: headers can arrive
 *  at once while a large body (the flow list, a report, the session listing)
 *  is still streaming when the 15 s run out. Without this a native
 *  `AbortSignal.timeout` rejects the read with a raw DOMException, which no
 *  `instanceof ApiError` / `.timedOut` caller recognises, and the fallback
 *  timer, cleared once `fetch()` resolved, never fires at all. A read is
 *  also raced against the signal itself, because a fetch implementation that
 *  does not cancel its body stream on abort would otherwise leave it pending
 *  for good. */
function withinBudget<T>(read: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const spent = () => reject(timedOutError());
    if (signal.aborted) spent();
    else signal.addEventListener("abort", spent, { once: true });
    read
      .then(resolve, (e) => reject(isTimeout(e) ? timedOutError() : e))
      .finally(() => signal.removeEventListener("abort", spent));
  });
}

async function req<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const { signal, done } = timeoutSignal(timeoutFor(path));
  // `done()` runs after the body has been read, not when the headers arrive:
  // on the fallback path it is what clears the timer, and the timer is the
  // budget the body read is held to (#870).
  try {
    let res: Response;
    try {
      res = await fetch(BASE + path, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (e) {
      throw isTimeout(e) ? timedOutError() : new ApiError("network error — server unreachable", 0);
    }
    if (!res.ok) {
      let body: unknown;
      try {
        body = await withinBudget(res.json(), signal);
      } catch (e) {
        // A body that never finished arriving is a timeout, not an absent body.
        if (e instanceof ApiError) throw e;
        /* no/invalid JSON body; parseApiError falls back to statusText */
      }
      const { message, code, id } = parseApiError(res.status, body, res.statusText);
      // `body`, not just the three fields parsed out of it — see ApiError.body.
      throw new ApiError(message, res.status, false, code, id, body);
    }
    return await withinBudget(res.json() as Promise<T>, signal);
  } finally {
    done();
  }
}

export const api = {
  get: <T = unknown>(path: string) => req<T>("GET", path),
  post: <T = unknown>(path: string, body?: unknown) => req<T>("POST", path, body),
  put: <T = unknown>(path: string, body?: unknown) => req<T>("PUT", path, body),
  patch: <T = unknown>(path: string, body?: unknown) => req<T>("PATCH", path, body),
  del: <T = unknown>(path: string) => req<T>("DELETE", path),
};
