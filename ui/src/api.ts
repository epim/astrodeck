// api.ts — robust fetch with per-endpoint timeouts, typed errors, and an
// AbortController fallback for old Safari/WebView (reliability spec §12,
// settings spec §2.1).
import { BASE } from "./lib/base";

export class ApiError extends Error {
  status: number;
  timedOut: boolean;
  code?: string;
  constructor(message: string, status: number, timedOut = false, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.timedOut = timedOut;
    this.code = code;
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
    let detail = res.statusText;
    let code: string | undefined;
    try {
      const j = await res.json();
      if (j && typeof j === "object") {
        if (typeof j.code === "string") code = j.code;
        detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
      }
    } catch {
      /* keep statusText */
    }
    throw new ApiError(detail, res.status, false, code);
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
