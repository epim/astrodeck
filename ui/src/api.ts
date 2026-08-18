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

async function req<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const { signal, done } = timeoutSignal(timeoutFor(path));
  let res: Response;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    throw new ApiError(
      timedOut ? "request timed out — server not responding" : "network error — server unreachable",
      0,
      timedOut,
    );
  } finally {
    done();
  }
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      /* no/invalid JSON body; parseApiError falls back to statusText */
    }
    const { message, code, id } = parseApiError(res.status, body, res.statusText);
    // `body`, not just the three fields parsed out of it — see ApiError.body.
    throw new ApiError(message, res.status, false, code, id, body);
  }
  return res.json() as Promise<T>;
}

export const api = {
  get: <T = unknown>(path: string) => req<T>("GET", path),
  post: <T = unknown>(path: string, body?: unknown) => req<T>("POST", path, body),
  put: <T = unknown>(path: string, body?: unknown) => req<T>("PUT", path, body),
  patch: <T = unknown>(path: string, body?: unknown) => req<T>("PATCH", path, body),
  del: <T = unknown>(path: string) => req<T>("DELETE", path),
};
