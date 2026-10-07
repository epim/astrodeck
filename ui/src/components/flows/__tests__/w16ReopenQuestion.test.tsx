// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16ReopenQuestion.test.tsx - the UI half of reopening a complete flow
// session (#179, spec I-30; backlog WP-120): the run call's flag, and the
// dialog that asks the question.
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/flows/__tests__/w16ReopenQuestion.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The server answers `POST /api/flows/{id}/run` with a 409 `reopen` when the
// flow's newest session is COMPLETE and the flow was edited to owe more
// (`_continue_flow_session`), and lifts it with `accept_reopen`. The operator
// is ASKED: Run never reopens silently, and START OVER is the other answer.
//
// WHAT THIS FILE HOLDS. The two pieces that live in `flowsApi.ts` and
// `flowRunControls.tsx`: the flag reaches the wire (and only when set, so
// every other request keeps the body it always had), and the question is
// worded, answered and offered START OVER like its three siblings. The
// question's route THROUGH the store (`flowsSlice.ts`: `CONTINUE_CODES`,
// `FLAG_FOR`, `sessionLogLine`) is that file's, and its test goes with it.
//
// MUTANTS, each run from a byte backup of the one file it changes and
// restored byte-identical (sha256 + grep verified). Observed:
//
//   U1 "the flag never reaches the wire" (flowsApi.ts: the spread of
//      `accept_reopen` replaced by `{}`):
//        x the run body carries accept_reopen true when the flag is set, with
//          every earlier answer kept: the flag did not reach the wire
//          (expected true, got undefined)
//   U2 "the flag is sent on every request" (flowsApi.ts: the spread replaced
//      by `accept_reopen: f.acceptReopen === true`, the form that sends six
//      flags plus one, which sendToWizardSheet.test.tsx pins at six):
//        x a request that does not answer the question keeps the body it always
//          had: the body of {} grew a key, so every other caller's request
//          changed (expected accept_dropped,accept_recount,accept_unmapped,
//          adopt,force,fresh, got accept_dropped,accept_recount,
//          accept_reopen,accept_unmapped,adopt,force,fresh)
//   U3 "the dialog repeats START OVER's note under the reopen sentence"
//      (flowRunControls.tsx: `SAYS_START_OVER` made `new Set(["adopt"])`):
//        x the reopen sentence says what START OVER does, so the dialog does not
//          say it twice: START OVER's note is repeated under a sentence that
//          holds it: <span style="display:flex;...
//   U4 "the reopen question's yes is ADOPT" (flowRunControls.tsx:
//      `CONTINUE_VERB.reopen` made "ADOPT"):
//        x the reopen question is titled, worded and answered as a CONTINUE:
//          the yes is CONTINUE, not ADOPT (expected CONTINUE, got ADOPT)

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// `lib/base.ts` reads `window.location` at module scope.
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

// ------------------------------------------------------------ the fake server
interface Ask { url: string; method: string; body: any }
const asks: Ask[] = [];
g.fetch = async (url: any, init: any) => {
  let body: any = null;
  if (init?.body) { try { body = JSON.parse(init.body); } catch { body = init.body; } }
  asks.push({ url: String(url), method: (init?.method ?? "GET").toUpperCase(), body });
  return {
    ok: true, status: 200, statusText: "OK",
    json: async () => ({ started: true, flow_id: "f1", frames: 7, unmapped: [] }),
  };
};
const lastRunBody = (): any => {
  const posts = asks.filter((a) => a.method === "POST" && a.url.includes("/api/flows/f1/run"));
  return posts[posts.length - 1]?.body;
};

// ------------------------------------------------------------------- harness
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

// ------------------------------------------ imports, AFTER the globals are set
const { flowsApi } = await import("../../../lib/flowsApi");
const {
  CONTINUE_TITLE, CONTINUE_VERB, START_OVER_LABEL, START_OVER_NOTE, askContinue,
} = await import("../flowRunControls");
const { renderToStaticMarkup } = await import("react-dom/server");
type FlowContinueQuestion = import("../flowsSlice").FlowContinueQuestion;
type ConfirmRequest = import("../../../store").ConfirmRequest;

/** The server's own words for these numbers (`reopen_detail`, a session of 5
 *  subs against a flow that now asks for 7). The dialog prints them as they
 *  come, so the fixture is the sentence and not a paraphrase. */
const REOPEN_SENTENCE =
  "This flow's session is complete: 5 subs recorded. The flow now asks for 7, "
  + "so 2 more are owed. CONTINUE reopens that session and counts the 5 toward "
  + "the 7. START OVER begins a new session that counts from 0.";
const RECOUNT_SENTENCE =
  "this session counted every sub taken (412); counting accepted subs makes it 371";

/** A question as the run route draws it. Built through `unknown` because the
 *  slice's `FlowContinueCode` is the three the store knows, and `askContinue`
 *  is what reads the code at run time; the dialog is what this file holds. */
const question = (code: string, detail: string): FlowContinueQuestion =>
  ({ code, detail, sessionId: "s-old" }) as unknown as FlowContinueQuestion;

/** Open `askContinue` against a fake confirm host and hand back what it asked
 *  and the two ways to answer it. */
function ask(q: FlowContinueQuestion) {
  let req: Omit<ConfirmRequest, "resolve"> | null = null;
  let settle!: (ok: boolean) => void;
  const pushConfirm = (r: Omit<ConfirmRequest, "resolve">) => {
    req = r;
    return new Promise<boolean>((resolve) => { settle = resolve; });
  };
  const answer = askContinue(q, pushConfirm, (ok) => settle(ok));
  return {
    get req() { assert(req, "askContinue pushed no confirm"); return req; },
    answer,
    /** The CONTINUE (or ADOPT) button. */
    yes: () => settle(true),
    /** Everything else: CANCEL, Escape, a tap outside. */
    no: () => settle(false),
  };
}

/** Walk a React element tree for the node whose `data-testid` is `id`. */
function byTestId(node: any, id: string): any {
  if (!node || typeof node !== "object") return null;
  if (node.props?.["data-testid"] === id) return node;
  const kids = node.props?.children;
  for (const k of Array.isArray(kids) ? kids : [kids]) {
    const hit = byTestId(k, id);
    if (hit) return hit;
  }
  return null;
}

// ============================================================ the run call
const SIX = ["accept_dropped", "accept_recount", "accept_unmapped", "adopt", "force", "fresh"];

await testAsync("the run body carries accept_reopen true when the flag is set, with every earlier answer kept", async () => {
  await flowsApi.run("f1", { acceptUnmapped: true, acceptRecount: true, acceptReopen: true });
  const body = lastRunBody();
  eq(body.accept_reopen, true, "the flag did not reach the wire");
  eq(body.accept_unmapped, true, "an earlier answer was lost");
  eq(body.accept_recount, true, "an earlier answer was lost");
});

await testAsync("a request that does not answer the question keeps the body it always had", async () => {
  for (const flags of [{}, { acceptReopen: false }, { fresh: true }] as const) {
    await flowsApi.run("f1", flags);
    eq(Object.keys(lastRunBody()).sort().join(","), SIX.join(","),
      `the body of ${JSON.stringify(flags)} grew a key, so every other caller's request changed`);
  }
  await flowsApi.run("f1", true, true);
  eq(Object.keys(lastRunBody()).sort().join(","), SIX.join(","), "the bare-boolean form changed");
});

// ================================================================ the dialog
await testAsync("the reopen question is titled, worded and answered as a CONTINUE", async () => {
  eq(CONTINUE_TITLE.reopen, "Add to the finished session?", "the title");
  eq(CONTINUE_VERB.reopen, "CONTINUE", "the yes is CONTINUE, not ADOPT");
  const t = ask(question("reopen", REOPEN_SENTENCE));
  eq(t.req.title, CONTINUE_TITLE.reopen, "the dialog's title");
  eq(t.req.confirmLabel, "CONTINUE", "the dialog's yes");
  const html = renderToStaticMarkup(t.req.body as never);
  assert(html.includes(">" + REOPEN_SENTENCE.replace(/'/g, "&#x27;") + "<"),
    `the server's sentence was not printed verbatim: ${html}`);
  t.yes();
  eq<unknown>(await t.answer, "reopen", "yes answers with the question's own code");
});

await testAsync("the reopen sentence says what START OVER does, so the dialog does not say it twice", async () => {
  const t = ask(question("reopen", REOPEN_SENTENCE));
  const html = renderToStaticMarkup(t.req.body as never);
  assert(!html.includes(START_OVER_NOTE), `START OVER's note is repeated under a sentence that holds it: ${html}`);
  assert(byTestId(t.req.body, "confirm-start-over"), "the reopen question offers no START OVER");
  t.no();
  await t.answer;
});

await testAsync("CONTROL: a recount question, whose sentence is silent on START OVER, does carry the note", async () => {
  const t = ask(question("recount", RECOUNT_SENTENCE));
  const html = renderToStaticMarkup(t.req.body as never);
  assert(html.includes(START_OVER_NOTE), "the control lost its note, so the assertion above proves nothing");
  t.no();
  await t.answer;
});

await testAsync("START OVER answers the reopen question with fresh, and CANCEL answers nothing", async () => {
  const over = ask(question("reopen", REOPEN_SENTENCE));
  const button = byTestId(over.req.body, "confirm-start-over");
  assert(button, "no START OVER button");
  eq(button.props.children, START_OVER_LABEL, "the button's word");
  button.props.onClick();
  eq(await over.answer, "fresh", "START OVER did not post fresh");

  const cancel = ask(question("reopen", REOPEN_SENTENCE));
  cancel.no();
  eq(await cancel.answer, null, "CANCEL started something");
});

const total = passed + failed;
console.log(`w16ReopenQuestion.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export default { passed, failed, total };
export { passed, failed, total };
