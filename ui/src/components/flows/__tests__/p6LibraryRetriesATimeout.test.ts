// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// p6LibraryRetriesATimeout.test.ts -- the flow library asks a timed-out load
// again by itself (#859).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/p6LibraryRetriesATimeout.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// On 2026-10-07 the owner opened the flow library at the start of a night and
// saw only "server not responding": one request had run past the 15 s budget
// during a brief disk stall, and the library loaded once per mount. These
// cases drive the REAL store and the REAL `flowsLoadLibrary`, with `api.get`
// replaced by a per-call script (answer, throw, or hold on a deferred) and the
// retry wait replaced through the test hatch (instant, or held).
//
// Named mutants (each turns the case beside it red):
//   L1   flowsLoadLibrary calls Promise.all without retryTransient.
//   L2   delete the onRetry write of libraryRetry.
//   L3a  delete the `if (!mine()) return;` before the success write.
//   L3b  delete the `if (!mine()) return;` at the top of the catch.
//   L4   the catch does not clear libraryRetry.
//   L5   flowsCloseEditor reloads with the retries (no `{ retry: false }`).
//   LE1  the success write clears `libraryError` unconditionally: a failure
//        another action wrote while the load was retrying is wiped.
//   LE2  the success write never clears `libraryError`: a failure that was
//        there when the load started outlives the library it was about.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// store.ts touches the document at import time, and lib/base.ts reads
// `window.location.pathname` at module scope. No request leaves the process:
// `api.get` is replaced below.
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

interface Deferred<T> { promise: Promise<T>; resolve(v: T): void; reject(e: unknown): void; }
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}
const tick = async (n = 6) => { for (let i = 0; i < n; i++) await new Promise((r) => setTimeout(r, 0)); };

const TIMEOUT_TEXT = "request timed out — server not responding";
const timeout = () => new ApiError(TIMEOUT_TEXT, 0, true);
const card = (id: string) => ({
  id, name: id.toUpperCase(), folder: "My flows", tagline: "", readonly: false,
  stages: 1, wires: 0, last_run: null, last_result: "", updated_ts: 0,
});
const CARDS1 = [card("old")];
const CARDS2 = [card("new")];
const FOLDERS = [{ name: "My flows", count: 1, readonly: false }];

/** What `api.get(path)` does on its n-th call for that path (0-based). */
type Step = (n: number) => Promise<unknown>;
let script: Record<string, Step> = {};
let asked: string[] = [];
(api as any).get = (path: string) => {
  const n = asked.filter((p) => p === path).length;
  asked.push(path);
  const step = script[path];
  if (!step) return Promise.reject(new ApiError("no", 404));
  return step(n);
};
const count = (path: string) => asked.filter((p) => p === path).length;

function reset(): void {
  script = {};
  asked = [];
  setRetrySleepForTests(async () => {});
  const st = useStore.getState() as any;
  useStore.setState({
    flows: {
      ...st.flows, cards: [], folders: [], libraryLoaded: false, libraryError: null,
      libraryRetry: null, libraryLoading: false, record: null, dirty: false,
    },
  } as any);
}
const flows = () => useStore.getState().flows;

await test("L1 a load that times out once and then succeeds ends with the library shown and no error", async () => {
  reset();
  script["/api/flows"] = (n) => (n === 0 ? Promise.reject(timeout()) : Promise.resolve(CARDS2));
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  await useStore.getState().flowsLoadLibrary();
  const f = flows();
  eq(f.libraryLoaded, true, "libraryLoaded");
  eq(f.libraryError, null, "libraryError");
  eq(f.libraryRetry, null, "libraryRetry");
  eq(f.libraryLoading, false, "libraryLoading");
  eq(f.cards.map((c) => c.id), ["new"], "the cards of the second try");
});

await test("L2 during the wait the screen says retrying, not the error", async () => {
  reset();
  let snap: any = null;
  setRetrySleepForTests(async () => {
    const f = flows();
    snap = { retry: f.libraryRetry, error: f.libraryError, loading: f.libraryLoading };
  });
  script["/api/flows"] = (n) => (n === 0 ? Promise.reject(timeout()) : Promise.resolve(CARDS2));
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  await useStore.getState().flowsLoadLibrary();
  eq(snap, { retry: { attempt: 2, of: 4 }, error: null, loading: true },
    "the store at the wait");
});

await test("L3a a superseded load's late ANSWER writes nothing", async () => {
  reset();
  const listA = deferred<unknown>();
  const foldersA = deferred<unknown>();
  script["/api/flows"] = (n) => (n === 0 ? listA.promise : Promise.resolve(CARDS2));
  script["/api/flows/folders"] = (n) => (n === 0 ? foldersA.promise : Promise.resolve(FOLDERS));
  const a = useStore.getState().flowsLoadLibrary();
  await tick();
  await useStore.getState().flowsLoadLibrary();
  eq(flows().cards.map((c) => c.id), ["new"], "precondition: B's answer is in");
  listA.resolve(CARDS1);
  foldersA.resolve(FOLDERS);
  await a;
  eq(flows().cards.map((c) => c.id), ["new"], "A's late answer must not replace B's");
  eq(flows().libraryLoading, false, "libraryLoading");
});

await test("L3b a superseded load's late FAILURE writes nothing", async () => {
  reset();
  const sleepA = deferred<void>();
  let sleeps = 0;
  setRetrySleepForTests(() => { sleeps++; return sleepA.promise; });
  script["/api/flows"] = (n) => (n === 0 ? Promise.reject(timeout()) : Promise.resolve(CARDS2));
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  const a = useStore.getState().flowsLoadLibrary();
  await tick();
  eq(sleeps, 1, "precondition: A is waiting to ask again");
  await useStore.getState().flowsLoadLibrary();
  eq(flows().cards.map((c) => c.id), ["new"], "precondition: B's answer is in");
  sleepA.resolve();
  await a;
  eq(flows().libraryError, null, "A's timeout must not land on B's library");
  eq(flows().cards.map((c) => c.id), ["new"], "cards");
  eq(count("/api/flows"), 2, "A asked once, B once; A asks no more after B");
});

await test("L4 a timeout that persists ends in the error after four tries", async () => {
  reset();
  script["/api/flows"] = () => Promise.reject(timeout());
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  await useStore.getState().flowsLoadLibrary();
  const f = flows();
  eq(count("/api/flows"), 4, "four tries");
  eq(f.libraryError, TIMEOUT_TEXT, "the timeout text, as before");
  eq(f.libraryRetry, null, "no retrying line once the tries are spent");
  eq(f.libraryLoading, false, "libraryLoading");
  eq(f.libraryLoaded, false, "an unreachable library never reads as an empty one");
});

await test("L5 a close's library reload asks once", async () => {
  reset();
  script["/api/flows"] = () => Promise.reject(new ApiError("network error — server unreachable", 0));
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  await useStore.getState().flowsCloseEditor();
  eq(count("/api/flows"), 1, "the close holds leaveFlowEditor's one-at-a-time promise until this settles");
  eq(flows().libraryError, "network error — server unreachable", "the failure is still said");
});

await test("LE1 a failure another action writes while the load retries survives the load's answer", async () => {
  reset();
  const sleep = deferred<void>();
  setRetrySleepForTests(() => sleep.promise);
  script["/api/flows"] = (n) => (n === 0 ? Promise.reject(timeout()) : Promise.resolve(CARDS2));
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  const load = useStore.getState().flowsLoadLibrary();
  await tick();
  eq(flows().libraryRetry, { attempt: 2, of: 4 }, "precondition: the load waits to ask again");
  // libraryError is shared with saves and opens (#859 N5): one fails now.
  const st = useStore.getState() as any;
  useStore.setState({ flows: { ...st.flows, libraryError: "could not save QUICK M31" } } as any);
  sleep.resolve();
  await load;
  eq(flows().cards.map((c) => c.id), ["new"], "precondition: the load's answer landed");
  eq(flows().libraryError, "could not save QUICK M31", "the save's failure was wiped by the load");
});

await test("LE2 a failure that was there when the load started is cleared by its answer", async () => {
  reset();
  const st = useStore.getState() as any;
  useStore.setState({ flows: { ...st.flows, libraryError: TIMEOUT_TEXT } } as any);
  script["/api/flows"] = () => Promise.resolve(CARDS2);
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  await useStore.getState().flowsLoadLibrary();
  eq(flows().libraryLoaded, true, "precondition: the load answered");
  eq(flows().libraryError, null, "the old failure outlived the library it was about");
});

setRetrySleepForTests(null);

console.log(`p6LibraryRetriesATimeout.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
