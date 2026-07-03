// Regression test for the WS-reconnect rehydration bug: onopen only refreshed
// config/principal/authMethods/update/logs, never the sequence/status snapshot.
// A terminal `sequence` transition (complete/aborted/error) that fired while the
// socket was down was lost forever (the bus keeps no history for that topic), so
// the store's `sequence` slice stayed stuck on the last state it saw (e.g.
// RUNNING) even after the link came back and 2s status polling resumed.
//
// Run directly:  npx tsx src/__tests__/ws.test.ts
// Same inline-assert harness as store.test.ts (no vitest/jest wired in yet).

// ------------------------------------------------------------ browser stubs
class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null {
    return this.m.has(k) ? (this.m.get(k) as string) : null;
  }
  setItem(k: string, v: string): void {
    this.m.set(k, String(v));
  }
  removeItem(k: string): void {
    this.m.delete(k);
  }
  clear(): void {
    this.m.clear();
  }
}
const g = globalThis as unknown as {
  localStorage?: Storage;
  document?: unknown;
  window?: unknown;
  location?: unknown;
  WebSocket?: unknown;
};
if (typeof g.localStorage === "undefined") {
  g.localStorage = new MemStorage() as unknown as Storage;
}
if (typeof g.document === "undefined") {
  const classList = {
    toggle(_c: string, _on?: boolean): void {},
    add(_c: string): void {},
    remove(_c: string): void {},
    contains(_c: string): boolean {
      return false;
    },
  };
  const style = {
    setProperty(_k: string, _v: string): void {},
    getPropertyValue(_k: string): string {
      return "";
    },
  };
  g.document = { documentElement: { classList, style } };
}
const FAKE_LOCATION = { pathname: "/", host: "localhost", protocol: "http:" };
if (typeof g.window === "undefined") {
  // ws.ts's stale-liveness ticker uses window.setInterval/clearInterval directly
  // (not the bare globals), so the stub needs them even under Node.
  g.window = { location: FAKE_LOCATION, setInterval, clearInterval, setTimeout, clearTimeout };
}
if (typeof g.location === "undefined") {
  g.location = FAKE_LOCATION;
}

// A minimal fake WebSocket: captures the instance so the test can drive its
// event handlers directly (connectWs assigns onopen/onmessage/onclose/onerror).
class FakeWebSocket {
  static last: FakeWebSocket | null = null;
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  constructor(url: string) {
    this.url = url;
    FakeWebSocket.last = this;
  }
  close(): void {
    /* no-op — tests never need to observe a real close */
  }
}
g.WebSocket = FakeWebSocket;

// Import AFTER the stubs are installed so module-load-time DOM/window touches succeed.
const { useStore } = await import("../store");
const { api } = await import("../api");
const { connectWs } = await import("../ws");
import type { MonitorSnapshot } from "../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try {
    await fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

await test("reconnect: onopen rehydrates sequence from /api/monitor/snapshot", async () => {
  // A dead run: store still thinks the sequence is RUNNING at 45%, exactly as it
  // would after a terminal transition was missed while the socket was down.
  useStore.setState({
    sequence: { state: "running", plan_name: "Tonight", progress: { frames_done: 45, frames_total: 100, percent: 45, elapsed_s: 900, rejected: 0 } } as never,
  });

  const snapshot: MonitorSnapshot = {
    sequence: { state: "complete", plan_name: "Tonight", progress: { frames_done: 100, frames_total: 100, percent: 100, elapsed_s: 1800, rejected: 0 } } as never,
    status: { connected: {}, looping: false, mode: "sim" } as never,
    preview_id: null,
    guide_recent: [],
  };

  const origGet = api.get;
  (api as unknown as { get: typeof api.get }).get = (async (path: string) => {
    if (path === "/api/monitor/snapshot") return snapshot as never;
    if (path === "/api/logs") return [] as never;
    throw new Error("network error — server unreachable"); // config/principal/authMethods/update: fail-quiet
  }) as typeof api.get;

  try {
    connectWs();
    const sock = FakeWebSocket.last!;
    await sock.onopen?.();
    // onopen's fire-and-forget calls (loadConfig/loadPrincipal/...) and the
    // snapshot rehydration are all async; let their microtasks/promises settle.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
  } finally {
    (api as unknown as { get: typeof api.get }).get = origGet;
  }

  const seq = useStore.getState().sequence;
  eq(seq.state, "complete", "sequence state rehydrated to the true terminal state");
  eq(seq.progress?.percent, 100, "sequence progress rehydrated");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nws.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };

// connectWs() arms a `window.setInterval` staleTicker on the fake WebSocket that
// otherwise keeps the Node event loop alive forever under a bare `tsx` run. Reach
// process via globalThis (no @types/node in this Vite/browser project) so `tsc -b`
// (browser lib, no Node types) still compiles this file cleanly.
(globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(
  failed ? 1 : 0,
);
