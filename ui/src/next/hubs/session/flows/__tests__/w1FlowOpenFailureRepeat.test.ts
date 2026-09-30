// w1FlowOpenFailureRepeat.test.ts - the second of two identical failed opens
// must still carry the server's own reason, not the "server answered with a
// different flow" mismatch sentence (#555).
//
//   Run directly:  node --import tsx <this file>
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `flowOpenFailure(previousError)` compared the CURRENT
// `libraryError` against a snapshot taken before the open, BY TEXT:
// `now && now !== previousError ? now : FLOW_OPEN_MISMATCH`. `flowsOpen`
// never cleared `libraryError` on a failed read, so the value left over from
// one failed open was still sitting there when the next one began. Two opens
// of the SAME missing flow, back to back, both failing with the identical
// server text ("no flow named <id>"), left `now === previousError` on the
// SECOND attempt - reported as FLOW_OPEN_MISMATCH, a claim (the server
// answered with a DIFFERENT flow) the code path never checked and the
// operator would go hunting for a mix-up that never happened.
//
// THE FIX, in two parts that this file names separately (one mutant each):
//   1. `flowsSlice.ts` `flowsOpen` clears `libraryError` itself right before
//      every open that gets far enough to try a real read (see its comment,
//      "CLEARED HERE"), so a stale reason from an EARLIER, unrelated open
//      cannot leak into a later one that succeeds on the wrong id (the
//      FLOW_OPEN_MISMATCH case).
//   2. `openFlow.ts` `flowOpenFailure` no longer compares text at all: once
//      (1) holds, any `libraryError` left once `openFlowById` resolves false
//      was written by THIS attempt, whatever its text, so a bare
//      `now ?? FLOW_OPEN_MISMATCH` is correct and the repeated-failure case
//      cannot be mistaken for "nothing changed".

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// store.ts and lib/base.ts read `window.location` at module scope, so a
// window has to exist before either is imported. No React is mounted here -
// every case below calls store actions directly.
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`, { url: "http://local/" });
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

// ------------------------------------------------------------- the fake rig
/** A flow id the server never has - every GET for it answers the SAME 404,
 *  twice, which is the repeat this file exists to prove. */
const MISSING = "flow-missing";
const REASON = "no flow named flow-missing";

/** A flow id whose read answers 200 but with ANOTHER flow's record - the one
 *  case FLOW_OPEN_MISMATCH is for, and the control this file must not break
 *  while fixing the repeat. */
const MISMATCH_ID = "flow-x";
const OTHER = {
  id: "flow-other", name: "M33 Ha", folder: "My flows", tagline: "",
  readonly: false, created_ts: 1, updated_ts: 2, last_run: null, last_result: "",
  graph: { nodes: [], edges: [] },
};

g.fetch = async (url: any, init?: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  const reply = (status: number, data: unknown) => ({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    headers: { get: () => "application/json" },
    json: async () => data,
    text: async () => JSON.stringify(data),
  });
  if (u === `/api/flows/${MISSING}` && method === "GET") return reply(404, { detail: REASON });
  if (u === `/api/flows/${MISMATCH_ID}` && method === "GET") return reply(200, OTHER);
  if (u === "/api/flows/compile") return reply(200, { plan: {}, structural: [], issues: [], unmapped: [] });
  if (/\/progress$/.test(u)) return reply(200, { session: null, blocks: [] });
  return reply(200, { ok: true });
};

// ------------------------------------------------ imports, AFTER globals are set
const { useStore } = await import("../../../../../store");
const { FLOWS_INIT } = await import("../../../../../components/flows/flowsSlice");
const {
  FLOW_OPEN_MISMATCH, flowOpenFailure, libraryErrorNow, openFlowById,
} = await import("../openFlow");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

/** Nothing open, no stale error - the fixture every case starts from. */
function reset(): void {
  useStore.setState({
    flows: { ...FLOWS_INIT, libraryLoaded: true, libraryError: null, record: null },
  } as never);
}

// ------------------------------------------------------------------ the cases

await test(
  "the second of two identical failed opens still carries the server's reason",
  async () => {
    reset();
    const before1 = libraryErrorNow();
    const landed1 = await openFlowById(MISSING);
    eq(landed1, false, "premise: the first open must fail");
    eq(flowOpenFailure(before1), REASON,
      "the first failure must carry the server's own words");

    const before2 = libraryErrorNow();
    const landed2 = await openFlowById(MISSING);
    eq(landed2, false, "premise: the second open must fail identically");
    eq(flowOpenFailure(before2), REASON,
      "the second, identical failure was reported as the mismatch sentence "
      + "instead of the server's reason");
  },
);

await test(
  "control: a stale error from an earlier open does not leak into an unrelated 200 mismatch",
  async () => {
    reset();
    // Leave a stale error behind, from an open of a DIFFERENT, unrelated id.
    await openFlowById(MISSING);
    eq(useStore.getState().flows.libraryError, REASON,
      "premise: the missing flow's open left an error behind");

    // A second open that LANDS, but on the wrong id (a 200 carrying another
    // flow's record) - the case flowOpenFailure's mismatch sentence exists
    // for. Nothing here runs the catch, so this only reads right once the
    // stale reason above has been cleared before the read.
    const before = libraryErrorNow();
    const landed = await openFlowById(MISMATCH_ID);
    eq(landed, false, "premise: the id in the body does not match the id asked for");
    eq(flowOpenFailure(before), FLOW_OPEN_MISMATCH,
      "the earlier, unrelated failure's reason leaked into a 200 mismatch");
  },
);

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w1FlowOpenFailureRepeat.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
