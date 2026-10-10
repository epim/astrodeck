// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// flowsLoadErrorOwnField.test.ts -- the library LOAD has its own error field,
// so it can neither hide an open's reason nor be reported as one (#877).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/flowsLoadErrorOwnField.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `flows.libraryError` was written by the library load, by `flowsOpen` and by
// the save paths, and read by `flowOpenFailure` as "why this open failed" and
// by both library screens as "could not read the library". Since #859 a load
// retries for up to about 82 s, which is long enough for an open to fail
// meanwhile (the load's failure then overwrote the open's reason) and for a
// load to fail while an open is still out (the load's text was then reported
// as the open's reason). These cases drive the REAL store, the REAL
// `flowsLoadLibrary` and `flowsOpen` and the REAL `flowOpenFailure`, with
// `api.get` replaced by a per-path script and the retry wait held or instant.
// The screens' lines are graded in p6FlowLibraryRetrying.test.tsx and
// p6FlowsScreenRetrying.test.tsx.
//
// Named mutants (each turns the cases beside it red):
//   O-a  flowsLoadLibrary's catch writes `libraryError` instead of
//        `libraryLoadError` (the shared field again): OA, OB, OC.
//   O-b  libraryFailedIn reads `libraryError` instead of `libraryLoadError`:
//        OD (a failed load is no longer re-asked).
//   O-c  libraryFailedIn reads either field: OD (a failed open starts a
//        library reload when the tab comes back).
//   O-d  the load's success write no longer clears `libraryError`: OE, OF (a
//        failed open's or save's line stays under a library that loaded).
//   O-e  the load's success write clears `libraryError` unconditionally: OG
//        (an open's reason written while the load retried is wiped).
//   O-f  the success write decides by comparing text with the value captured
//        at the start (#921): OH, OI (a fresh failure in the stale one's
//        words is wiped).
//   O-g  the write count is never bumped (`libraryErrorWrites++` deleted):
//        OG, OH, OI (every write reads as "nothing changed").
//   O-h  flowsSave's catch writes the field without the count: OI (the one
//        path that writes the same words with no clear before it).
//   O-i  flowsDismissLibraryError does nothing: OK.
// The success write's side of the split is graded here by OE, OF, OG, OH, OI
// and OJ (the open's and the save's failure through the real actions) and by
// LE1, LE2 and LE3 in p6LibraryRetriesATimeout.test.ts (LE1 a save through the
// real action, LE2 and LE3 the field seeded directly).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// store.ts touches the document at import time, and lib/base.ts reads
// `window.location.pathname` at module scope. No request leaves the process:
// `api.get` and `api.post` are replaced below.
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
const { FLOWS_INIT, libraryFailedIn } = await import("../flowsSlice");
const { FLOW_OPEN_MISMATCH, flowOpenFailure, openFlowById } =
  await import("../../../next/hubs/session/flows/openFlow");

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
const OPEN_REASON = "no flow named gone";
const FOLDERS = [{ name: "My flows", count: 0, readonly: false }];
/** A 200 for the flow asked for that carries ANOTHER flow's record: the one
 *  case `flowOpenFailure` answers with FLOW_OPEN_MISMATCH. */
const OTHER = {
  id: "other", name: "M33 Ha", folder: "My flows", tagline: "", readonly: false,
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [], edges: [] },
};

/** A flow that is open in the editor and has unsaved edits (OF). */
const EDITED = {
  id: "edited", name: "M31 L", folder: "My flows", tagline: "", readonly: false,
  created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [], edges: [] },
};

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
// The open's own follow-ups (the compile) are advisory and swallow a failure.
(api as any).post = () => Promise.reject(new ApiError("no", 404));
/** What a save's PUT does: refuses, or answers with the record it was sent. */
let putMode: "fail" | "ok" = "ok";
const PUT_FAILURE = "network error — server unreachable";
(api as any).put = (_path: string, body: any) => (putMode === "fail"
  ? Promise.reject(new ApiError(PUT_FAILURE, 0))
  : Promise.resolve({ ...EDITED, ...(body.flow ?? {}) }));

function reset(): void {
  script = {};
  asked = [];
  putMode = "ok";
  setRetrySleepForTests(async () => {});
  useStore.setState({ flows: { ...FLOWS_INIT, folders: FOLDERS } } as any);
}
const flows = () => useStore.getState().flows;
const count = (path: string) => asked.filter((p) => p === path).length;

await test("OA a load that fails writes libraryLoadError and leaves libraryError alone", async () => {
  reset();
  script["/api/flows"] = () => Promise.reject(timeout());
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  await useStore.getState().flowsLoadLibrary({ retry: false });
  eq(flows().libraryLoadError, TIMEOUT_TEXT, "the load's failure, in its own field");
  eq(flows().libraryError, null, "an open's/save's field must not carry the load's failure");
});

await test("OB an open's reason survives a load that is retrying and then fails", async () => {
  reset();
  const sleep = deferred<void>();
  setRetrySleepForTests(() => sleep.promise);
  script["/api/flows"] = () => Promise.reject(timeout());
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  script["/api/flows/gone"] = () => Promise.reject(new ApiError(OPEN_REASON, 404));
  const load = useStore.getState().flowsLoadLibrary();
  await tick();
  eq(flows().libraryRetry, { attempt: 2, of: 4 }, "precondition: the load waits to ask again");

  const landed = await openFlowById("gone");
  eq(landed, false, "precondition: the open failed");
  eq(flowOpenFailure(null), OPEN_REASON, "the open's reason while the load retries");

  sleep.resolve();
  await load;
  eq([flows().libraryRetry, flows().libraryLoading, count("/api/flows")], [null, false, 4],
    "precondition: the load tried four times and its retries are spent");
  eq(flows().libraryError, OPEN_REASON, "the load's failure overwrote the open's reason");
  eq(flowOpenFailure(null), OPEN_REASON, "the open is now reported with the load's failure");
  eq(flows().libraryLoadError, TIMEOUT_TEXT, "the load's own failure is said in its own field");
});

await test("OC a load that fails while an open is still out is not the open's reason", async () => {
  reset();
  const read = deferred<unknown>();
  script["/api/flows"] = () => Promise.reject(timeout());
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  script["/api/flows/wanted"] = () => read.promise;
  const open = openFlowById("wanted");
  await tick();
  await useStore.getState().flowsLoadLibrary({ retry: false });
  eq([flows().libraryLoading, flows().libraryLoaded, count("/api/flows")], [false, false, 1],
    "precondition: the load asked once and failed while the open was out");
  read.resolve(OTHER);
  eq(await open, false, "precondition: the server answered with another flow, so the open did not land");
  eq(flowOpenFailure(null), FLOW_OPEN_MISMATCH,
    "the load's failure was reported as the reason the open did not land");
  eq(flows().libraryLoadError, TIMEOUT_TEXT, "the load's own failure is said in its own field");
});

await test("OD only a failed LOAD makes the library re-ask; a failed open does not", async () => {
  const base = { ...FLOWS_INIT, libraryLoaded: false, libraryLoading: false };
  eq(libraryFailedIn({ ...base, libraryLoadError: TIMEOUT_TEXT }), true, "a failed load is re-asked");
  eq(libraryFailedIn({ ...base, libraryError: OPEN_REASON }), false,
    "a failed open started a library reload when the tab came back");
  eq(libraryFailedIn({ ...base, libraryLoadError: TIMEOUT_TEXT, libraryLoading: true }), false,
    "a re-ask stacked on a load in flight");
  eq(libraryFailedIn({ ...base, libraryLoadError: TIMEOUT_TEXT, libraryLoaded: true }), false,
    "a library that loaded is not re-asked");
});

await test("OE a failed open's reason does not outlive a library load that then succeeds", async () => {
  reset();
  script["/api/flows"] = () => Promise.resolve([]);
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  script["/api/flows/gone"] = () => Promise.reject(new ApiError(OPEN_REASON, 404));
  eq(await openFlowById("gone"), false, "precondition: the open failed");
  eq(flows().libraryError, OPEN_REASON, "precondition: the open's reason is on the store");
  await useStore.getState().flowsLoadLibrary();
  eq(flows().libraryLoaded, true, "precondition: the load answered");
  eq(flows().libraryError, null,
    "the stale 'could not open or save' line is still under a library that loaded fine");
});

await test("OF a failed save's reason is gone once a save worked and the library reloads on close", async () => {
  reset();
  script["/api/flows"] = () => Promise.resolve([]);
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  useStore.setState({
    flows: { ...FLOWS_INIT, folders: FOLDERS, record: EDITED, graph: EDITED.graph, dirty: true },
  } as any);
  putMode = "fail";
  await useStore.getState().flowsSave();
  eq([flows().dirty, flows().libraryError], [true, PUT_FAILURE],
    "precondition: the save failed and said why");
  putMode = "ok";
  await useStore.getState().flowsSave();
  eq(flows().dirty, false, "precondition: the next save worked");
  await useStore.getState().flowsCloseEditor();
  eq([flows().record, flows().libraryLoaded], [null, true],
    "precondition: the editor closed and the library loaded");
  eq(flows().libraryError, null, "the failed save's line outlived the save that worked");
});

await test("OG an open that fails while the load retries keeps its reason when the load then succeeds", async () => {
  reset();
  const sleep = deferred<void>();
  setRetrySleepForTests(() => sleep.promise);
  script["/api/flows"] = (n) => (n === 0 ? Promise.reject(timeout()) : Promise.resolve([]));
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  script["/api/flows/gone"] = () => Promise.reject(new ApiError(OPEN_REASON, 404));
  const load = useStore.getState().flowsLoadLibrary();
  await tick();
  eq(flows().libraryRetry, { attempt: 2, of: 4 }, "precondition: the load waits to ask again");
  eq(await openFlowById("gone"), false, "precondition: the open failed meanwhile");
  sleep.resolve();
  await load;
  eq(flows().libraryLoaded, true, "precondition: the load's second try answered");
  eq(flows().libraryError, OPEN_REASON, "the open's reason was wiped by a load that started before it");
  eq(flowOpenFailure(null), OPEN_REASON, "and the open is reported with it");
});

// ------------------------------------------- the success clear counts writes (#921)
//
// The load clears, on success, the reason `libraryError` held when it started.
// "Held" was decided by comparing TEXT with the value captured at the start, so
// a failure written again with the same words while the load was out read as
// "nothing changed" and was wiped. Each case below seeds a reason through a
// real failure, holds a load, fails the same way again, and lets the load land.

await test("OH an open that fails with the stale reason's words while a load is out keeps its line", async () => {
  reset();
  const held = deferred<unknown>();
  script["/api/flows"] = () => held.promise;
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  script["/api/flows/gone"] = () => Promise.reject(new ApiError(OPEN_REASON, 404));
  eq(await openFlowById("gone"), false, "precondition: the first open failed");
  eq(flows().libraryError, OPEN_REASON, "precondition: its reason is the stale text X");
  const load = useStore.getState().flowsLoadLibrary({ retry: false });
  await tick();
  eq(flows().libraryLoading, true, "precondition: the load is out");
  eq(await openFlowById("gone"), false, "precondition: the same open failed again while it is out");
  eq(flows().libraryError, OPEN_REASON, "precondition: with the same words X");
  held.resolve([]);
  await load;
  eq(flows().libraryLoaded, true, "precondition: the load answered");
  eq(flows().libraryError, OPEN_REASON, "the fresh failure, in the stale one's words, was wiped by the load");
  eq(flowOpenFailure(null), OPEN_REASON, "and the open is no longer reported with its reason");
});

await test("OI a save that fails with the stale reason's words while a load is out keeps its line", async () => {
  reset();
  const held = deferred<unknown>();
  script["/api/flows"] = () => held.promise;
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  useStore.setState({
    flows: { ...FLOWS_INIT, folders: FOLDERS, record: EDITED, graph: EDITED.graph, dirty: true },
  } as any);
  putMode = "fail";
  await useStore.getState().flowsSave();
  eq(flows().libraryError, PUT_FAILURE, "precondition: the first save failed with the text X");
  const load = useStore.getState().flowsLoadLibrary({ retry: false });
  await tick();
  eq(flows().libraryLoading, true, "precondition: the load is out");
  await useStore.getState().flowsSave();
  eq([flows().dirty, flows().libraryError], [true, PUT_FAILURE],
    "precondition: the same save failed again while it is out, with the same words X");
  held.resolve([]);
  await load;
  eq(flows().libraryLoaded, true, "precondition: the load answered");
  eq(flows().libraryError, PUT_FAILURE, "the save's fresh failure, in the stale one's words, was wiped by the load");
});

await test("OJ a failure that was there when the load started, and nothing wrote since, is still cleared", async () => {
  reset();
  const held = deferred<unknown>();
  script["/api/flows"] = () => held.promise;
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  script["/api/flows/gone"] = () => Promise.reject(new ApiError(OPEN_REASON, 404));
  eq(await openFlowById("gone"), false, "precondition: the open failed");
  const load = useStore.getState().flowsLoadLibrary({ retry: false });
  await tick();
  held.resolve([]);
  await load;
  eq(flows().libraryError, null, "the stale line outlived a load that answered with nothing written since");
});

// ---------------------------------------------- the dismissal (#919)

await test("OK DISMISS clears the open's reason, not the load's, and a repeat of the failure shows again", async () => {
  reset();
  script["/api/flows"] = () => Promise.reject(timeout());
  script["/api/flows/folders"] = () => Promise.resolve(FOLDERS);
  script["/api/flows/gone"] = () => Promise.reject(new ApiError(OPEN_REASON, 404));
  await useStore.getState().flowsLoadLibrary({ retry: false });
  eq(await openFlowById("gone"), false, "precondition: the open failed");
  eq([flows().libraryError, flows().libraryLoadError], [OPEN_REASON, TIMEOUT_TEXT],
    "precondition: an open's reason and a load's failure, each in its own field");
  useStore.getState().flowsDismissLibraryError();
  eq([flows().libraryError, flows().libraryLoadError], [null, TIMEOUT_TEXT],
    "DISMISS must clear the open's reason and leave the load's failure (and its RETRY) up");
  eq(await openFlowById("gone"), false, "precondition: the same open failed again");
  eq(flows().libraryError, OPEN_REASON, "a failure the operator had dismissed did not show when it happened again");
});

setRetrySleepForTests(null);

console.log(`flowsLoadErrorOwnField.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
