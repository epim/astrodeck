// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6TonightRetriesOnlyTheRead.test.ts -- Tonight asks a timed-out READ again by
// itself, and never repeats the save or the compile in front of it (#859).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/p6TonightRetriesOnlyTheRead.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `flowsFetchTonight` flushes first: a PUT of an edited flow (#688), then a
// compile POST when the compile in hand is not for the graph on screen. The
// issue's rule is "never retry a POST by itself", so only the GET is wrapped.
// The REAL store and the REAL action; `api.get`, `api.put` and `api.post` are
// replaced by per-call scripts and the retry wait runs at once.
//
// Named mutants (each turns the case beside it red):
//   T1  no retryTransient around flowsApi.tonight.
//   T2  the whole try body (save + compile + read) wrapped in retryTransient.
//   T3  `tonightErrorTransient: true` unconditionally in the catch.
//   UX6 the read's `stop` is `() => false`: a read superseded by a newer one
//       keeps asking after its wait.
//   UX9 flowsFetchTonight's first write no longer resets `tonightRetry` and
//       `tonightErrorTransient`: a newer read starts under the older read's
//       "try 2 of 4".

/* eslint-disable @typescript-eslint/no-explicit-any */
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`,
                      { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {},
                          removeEventListener() {}, addListener() {},
                          removeListener() {} });
win.WebSocket = class { close() {} addEventListener() {} send() {} };
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "localStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "WebSocket", "location",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

const { useStore } = await import("../../../store");
const { api, ApiError } = await import("../../../api");
const { setRetrySleepForTests } = await import("../../../lib/retryLoad");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq(got: unknown, want: unknown, msg: string): void {
  const a = JSON.stringify(got);
  const b = JSON.stringify(want);
  if (a !== b) throw new Error(`${msg}\n  expected ${b}\n  got      ${a}`);
}

const timeout = () => new ApiError("request timed out — server not responding", 0, true);
interface Deferred<T> { promise: Promise<T>; resolve(v: T): void; }
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}
const tick = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };
const TONIGHT = { ok: true, night: null, targets: [], story: [] };
const GRAPH = { nodes: [], edges: [] };

type Step = (n: number) => Promise<unknown>;
let script: Record<string, Step> = {};
let asked: string[] = [];
function fake(method: string) {
  return (path: string) => {
    const key = `${method} ${path}`;
    const n = asked.filter((p) => p === key).length;
    asked.push(key);
    const step = script[key];
    if (!step) return Promise.reject(new ApiError("no", 404));
    return step(n);
  };
}
(api as any).get = fake("GET");
(api as any).put = fake("PUT");
(api as any).post = fake("POST");
const count = (key: string) => asked.filter((p) => p === key).length;
const methodCount = (m: string) => asked.filter((p) => p.startsWith(`${m} `)).length;

function reset(dirty: boolean): void {
  script = {};
  asked = [];
  setRetrySleepForTests(async () => {});
  const st = useStore.getState() as any;
  useStore.setState({
    flows: {
      ...st.flows,
      record: {
        id: "f1", name: "Flow f1", folder: "My flows", tagline: "", graph: GRAPH,
        created_ts: 1, updated_ts: 1, last_run: null, last_result: "", readonly: false,
      },
      graph: GRAPH, dirty, compiled: null, compiling: false,
      tonight: null, tonightLoading: false, tonightError: null,
      tonightRetry: null, tonightErrorTransient: false,
    },
  } as any);
}
const flows = () => useStore.getState().flows;
const GET_TONIGHT = "GET /api/flows/f1/tonight";

await test("T1 a timed-out tonight read is asked again and lands", async () => {
  reset(false);
  script[GET_TONIGHT] = (n) => (n === 0 ? Promise.reject(timeout()) : Promise.resolve(TONIGHT));
  await useStore.getState().flowsFetchTonight({ flush: false });
  const f = flows();
  eq(f.tonight, TONIGHT, "the answer of the second try");
  eq(f.tonightError, null, "tonightError");
  eq(f.tonightRetry, null, "tonightRetry");
  eq(f.tonightLoading, false, "tonightLoading");
});

await test("T2 the save before the read is never repeated", async () => {
  reset(true);
  script["PUT /api/flows/f1"] = () => Promise.reject(timeout());
  script["POST /api/flows/compile"] = () => Promise.resolve({ ok: true, steps: [] });
  script[GET_TONIGHT] = (n) => (n === 0 ? Promise.reject(timeout()) : Promise.resolve(TONIGHT));
  await useStore.getState().flowsFetchTonight();
  eq(count("PUT /api/flows/f1"), 1, "the flush's PUT runs once");
  eq(count(GET_TONIGHT), 2, "the read is asked twice");
  eq(methodCount("POST") <= 1, true, `the compile POST runs at most once (${methodCount("POST")})`);
  eq(flows().tonight, TONIGHT, "and the read lands");
});

await test("T3 a transient failure is marked, a refusal is not", async () => {
  reset(false);
  script[GET_TONIGHT] = () => Promise.reject(timeout());
  await useStore.getState().flowsFetchTonight({ flush: false });
  eq(count(GET_TONIGHT), 4, "precondition: four tries");
  eq(flows().tonightErrorTransient, true, "a timeout after four tries is transient");

  reset(false);
  script[GET_TONIGHT] = () => Promise.reject(new ApiError("ephemeris failed", 500));
  await useStore.getState().flowsFetchTonight({ flush: false });
  eq(count(GET_TONIGHT), 1, "precondition: a refusal is asked once");
  eq(flows().tonightError, "ephemeris failed", "precondition: the refusal is shown");
  eq(flows().tonightErrorTransient, false, "a refusal is the home's answer, not transient");
});

await test("UX6 a read superseded by a newer one asks no more after its wait", async () => {
  reset(false);
  const sleepA = deferred<void>();
  setRetrySleepForTests(() => sleepA.promise);
  script[GET_TONIGHT] = (n) => (n === 0 ? Promise.reject(timeout()) : Promise.resolve(TONIGHT));
  const a = useStore.getState().flowsFetchTonight({ flush: false });
  await tick();
  eq(count(GET_TONIGHT), 1, "precondition: A asked once and waits");
  await useStore.getState().flowsFetchTonight({ flush: false });
  eq(count(GET_TONIGHT), 2, "precondition: B asked once and answered");
  sleepA.resolve();
  await a;
  eq(count(GET_TONIGHT), 2, "A asked again after B superseded it");
  eq(flows().tonight, TONIGHT, "B's answer stands");
});

await test("UX9 a newer read starts with no retrying line of its own", async () => {
  reset(false);
  const sleepA = deferred<void>();
  setRetrySleepForTests(() => sleepA.promise);
  const getB = deferred<unknown>();
  script[GET_TONIGHT] = (n) => (n === 0 ? Promise.reject(timeout()) : getB.promise);
  const a = useStore.getState().flowsFetchTonight({ flush: false });
  await tick();
  eq(flows().tonightRetry, { attempt: 2, of: 4 }, "precondition: A waits to ask again");
  const b = useStore.getState().flowsFetchTonight({ flush: false });
  eq(flows().tonightRetry, null, "B's first try shows A's try count");
  getB.resolve(TONIGHT);
  await b;
  sleepA.resolve();
  await a;
  eq(flows().tonight, TONIGHT, "B's answer stands");
});

setRetrySleepForTests(null);
// Nothing open: no autosave timer outlives the file.
useStore.setState({ flows: { ...(useStore.getState() as any).flows, record: null, dirty: false } } as any);

console.log(`p6TonightRetriesOnlyTheRead.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
