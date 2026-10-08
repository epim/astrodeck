// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16ReopenThroughTheSlice.test.tsx - the reopen question end to end through
// the store slice and the shared RUN loop (#179, WP-120).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w16ReopenThroughTheSlice.test.tsx   (from ui/)
//
// WHY THIS IS A SEPARATE FILE. It needs `flowsSlice.ts` to know the `reopen`
// code (`FlowContinueCode`, `CONTINUE_CODES`, `FLAG_FOR`, `sessionLogLine`),
// and that file was another package's (WP-117) when WP-120 was built. Without
// those edits a 409 `reopen` is logged as "could not start" and offers no way
// forward. The edits landed at the wave 16 integration.
//
// NAMED MUTANTS (each from a byte backup in the worktree, restored and
// sha256-compared; the first failure is quoted):
//
//   "code not recognised": flowsSlice.ts CONTINUE_CODES without "reopen".
//     RED, 1/5 passed:
//       x a 409 reopen is a QUESTION: its code, the server's sentence
//         verbatim, the session id: expected a continue question, got null
//   "flag not mapped": FLAG_FOR `reopen: "acceptReopen"` made `reopen:
//     "adopt"`. RED, 3/5 passed:
//       x yes re-posts with accept_reopen and every earlier answer; the first
//         post carried none: the re-post did not say yes (expected true, got
//         undefined)
//   "log line says continued": sessionLogLine's `s.reopened === true ? ... :`
//     made the plain "continued night". RED, 4/5 passed:
//       x the log says the finished session was reopened: the line:
//         continued night 2: 2 steps kept, 0 new

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "FocusEvent", "localStorage", "sessionStorage",
  "getComputedStyle", "matchMedia", "WebSocket", "ResizeObserver",
  "requestAnimationFrame", "cancelAnimationFrame",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}

interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
type Reply = () => any;
let script: Reply[] = [];
const okReply = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});
const conflict = (detail: Record<string, unknown>): Reply => () => ({
  ok: false, status: 409, statusText: "Conflict", json: async () => ({ detail }),
});
const started = (session?: Record<string, unknown>): Reply => () => okReply({
  started: true, flow_id: "f1", frames: 7, unmapped: [],
  ...(session ? { session } : {}),
});
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: u, method, body });
  if (method === "POST" && u.includes("/api/flows/f1/run")) {
    const next = script.shift();
    if (!next) return { ok: false, status: 500, statusText: "Server Error", json: async () => ({ detail: "script ran out" }) };
    return next();
  }
  return okReply({});
};
const runPosts = () => asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/f1/run"));
function serve(...replies: Reply[]): void { asks.length = 0; script = replies; }

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): asserts cond { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const { createFlowsActions, FLOWS_INIT, nextRunFlags, sessionLogLine } = await import("../flowsSlice");
const { askContinue, runAnsweringQuestions } = await import("../flowRunControls");
type FlowsHost = import("../flowsSlice").FlowsHost;

const SENTENCE =
  "This flow's session is complete: 5 subs recorded. The flow now asks for 7, "
  + "so 2 more are owed. CONTINUE reopens that session and counts the 5 toward "
  + "the 7. START OVER begins a new session that counts from 0.";
const REOPEN_409 = {
  code: "reopen", detail: SENTENCE, session_id: "s-old",
  recorded: 5, accepted: 5, quota: 7, owed: 2,
};
const REOPENED = { id: "s-old", night: 2, continued: true, reopened: true, kept: 2, new: 0, dropped: 0 };
const FRESH = { id: "s-new", night: 1, continued: false, kept: 0, new: 2, dropped: 0 };

const RECORD = {
  id: "f1", name: "M31 mosaic", folder: "My flows", tagline: "",
  graph: { nodes: [], edges: [] }, created_ts: 0, updated_ts: 0,
  last_run: null, last_result: "" as const, readonly: false,
};
function slice() {
  let state: FlowsHost;
  const set = (fn: (s: FlowsHost) => Partial<FlowsHost>) => { state = { ...state, ...fn(state) } as FlowsHost; };
  const get = () => state;
  const actions = createFlowsActions(set, get);
  state = { ...actions, flows: { ...FLOWS_INIT, record: RECORD as never } } as FlowsHost;
  return { get flows() { return state.flows; }, a: actions };
}

/** A confirm host that answers each question with the next scripted word:
 *  "yes", "start-over" or "cancel". */
function host(answers: string[]) {
  const asked: string[] = [];
  let settle!: (ok: boolean) => void;
  const pushConfirm = (r: any) => {
    asked.push(`${r.title} | ${r.confirmLabel}`);
    const which = answers.shift();
    return new Promise<boolean>((resolve) => {
      settle = resolve;
      if (which === "yes") resolve(true);
      else if (which === "cancel") resolve(false);
      else {
        // START OVER: the body's button records the choice and resolves false.
        const find = (n: any): any => {
          if (!n || typeof n !== "object") return null;
          if (n.props?.["data-testid"] === "confirm-start-over") return n;
          const kids = n.props?.children;
          for (const k of Array.isArray(kids) ? kids : [kids]) { const h = find(k); if (h) return h; }
          return null;
        };
        find(r.body).props.onClick();
      }
    });
  };
  return { asked, pushConfirm, resolveConfirm: (ok: boolean) => settle?.(ok) };
}

await testAsync("a 409 reopen is a QUESTION: its code, the server's sentence verbatim, the session id", async () => {
  serve(conflict(REOPEN_409));
  const s = slice();
  const ans = await s.a.flowsRun();
  assert(ans !== null && ans.kind === "continue", `expected a continue question, got ${JSON.stringify(ans)}`);
  eq<string>(ans.question.code, "reopen", "code");
  eq(ans.question.detail, SENTENCE, "the server's sentence, verbatim");
  eq(ans.question.sessionId, "s-old", "session id");
  eq(s.flows.logs.filter((l) => /could not start/.test(l.msg)).length, 0, "a question is not a failure");
});

await testAsync("yes re-posts with accept_reopen and every earlier answer; the first post carried none", async () => {
  serve(conflict(REOPEN_409), started(REOPENED));
  const s = slice();
  const first = await s.a.flowsRun({ acceptUnmapped: true });
  assert(first !== null && first.kind === "continue", "premise: asked");
  await s.a.flowsRun(nextRunFlags(first.flags, "reopen"));
  const [one, two] = runPosts();
  eq("accept_reopen" in one.body, false, "the first post answered a question nobody asked");
  eq(two.body.accept_reopen, true, "the re-post did not say yes");
  eq(two.body.accept_unmapped, true, "the re-post lost an earlier answer");
});

await testAsync("the log says the finished session was reopened", async () => {
  serve(started(REOPENED));
  const s = slice();
  await s.a.flowsRun();
  const line = sessionLogLine(REOPENED);
  assert(line !== null && /reopened the finished session/.test(line), `the line: ${line}`);
  assert(s.flows.logs.some((l) => /reopened the finished session/.test(l.msg)), "the flow log did not say so");
  assert(!/reopened/.test(sessionLogLine({ ...REOPENED, reopened: undefined }) ?? ""), "an ordinary continue says reopened");
});

await testAsync("the shared RUN loop asks, and CONTINUE posts accept_reopen", async () => {
  serve(conflict(REOPEN_409), started(REOPENED));
  const s = slice();
  const h = host(["yes"]);
  const out = await runAnsweringQuestions(
    s.a.flowsRun, async () => false, (q) => askContinue(q, h.pushConfirm, h.resolveConfirm));
  eq(out.cancelled, false, "cancelled");
  eq<string | null>(out.answered, "reopen", "answered");
  eq(h.asked[0], "Add to the finished session? | CONTINUE", "the dialog");
  eq(runPosts().length, 2, "two posts");
  eq(runPosts()[1].body.accept_reopen, true, "the yes");
});

await testAsync("START OVER posts fresh and not accept_reopen; CANCEL posts nothing more", async () => {
  serve(conflict(REOPEN_409), started(FRESH));
  const s = slice();
  const over = host(["start-over"]);
  const out = await runAnsweringQuestions(
    s.a.flowsRun, async () => false, (q) => askContinue(q, over.pushConfirm, over.resolveConfirm));
  eq<string | null>(out.answered, "fresh", "answered");
  const second = runPosts()[1].body;
  eq(second.fresh, true, "START OVER did not post fresh");
  eq("accept_reopen" in second, false, "START OVER also reopened");

  serve(conflict(REOPEN_409));
  const no = host(["cancel"]);
  const cancelled = await runAnsweringQuestions(
    slice().a.flowsRun, async () => false, (q) => askContinue(q, no.pushConfirm, no.resolveConfirm));
  eq(cancelled.cancelled, true, "cancel");
  eq(runPosts().length, 1, "CANCEL posted again");
});

const total = passed + failed;
console.log(`w16ReopenThroughTheSlice.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process?.exit(1);

export default { passed, failed, total };
export { passed, failed, total };
