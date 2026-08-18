// A 409 that asks a QUESTION has to arrive with the question's material.
//
// `POST /api/flows/{id}/run` answers 409 `code:"unmapped"` with the list of
// graph settings the compile drops, so the UI can show them and ask "run
// anyway?". For as long as that endpoint existed the list never arrived:
// ApiError kept message/status/code/id and threw the decoded body away, while
// `flowsRun` read `err.unmapped` and `err.body?.unmapped` — one field that did
// not exist on the class and one shape the server does not send (FastAPI nests
// the payload under `detail`). The guard could never be true.
//
// The effect on a rig: RUN did nothing. No dialog, no error, one log line
// written off the bottom of the viewport. And it was not an edge case — every
// flow with a SAFETY, SLEW, AUTOFOCUS, GUIDE, REPORT, CONDITION, REFOCUS or
// ABORT node carries a loss, which is every flow, including the shipped
// Examples.
//
// Run with:  npx tsx src/lib/__tests__/apiErrorPayload.test.ts

// `../../api` reads `window` at module scope (lib/base.ts resolves the base
// path from the URL), so a bare import takes this file down before an
// assertion runs. One stub, then a dynamic import — the same shape
// flowNodeDom.test.tsx uses for jsdom.
(globalThis as unknown as { window: unknown }).window = {
  location: { pathname: "/", origin: "http://local" },
} as unknown as Window;

import { apiErrorPayload } from "../apiError";
const { ApiError } = await import("../../api");
const { flowsApi } = await import("../flowsApi");

let passed = 0, failed = 0;
const failures: string[] = [];
const queue: Promise<void>[] = [];
function test(name: string, fn: () => void | Promise<void>): void {
  queue.push((async () => {
    try { await fn(); passed++; }
    catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  })());
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// The body FastAPI actually sends for HTTPException(409, detail={...}).
const RUN_409 = {
  detail: {
    detail: "parts of this flow do not survive the compile",
    code: "unmapped",
    unmapped: [
      { key: "nodes.guide", level: "warn", detail: "settle and dither are not carried" },
      { key: "nodes.cycle.reject", level: "warn", detail: "the HFR threshold is not carried" },
    ],
  },
};

// THROUGH `req()`, not around it. A first draft built the ApiError by hand and
// asserted its `.body` — which passes whatever `req` does with the decoded
// body, and stayed green when the pass-through was deleted. The regression
// lives in one line of `req`, so the test has to run that line.
async function throwsFrom(status: number, body: unknown): Promise<InstanceType<typeof ApiError>> {
  const real = globalThis.fetch;
  (globalThis as unknown as { fetch: unknown }).fetch = async () =>
    ({ ok: false, status, statusText: "Conflict",
       json: async () => body }) as unknown as Response;
  try {
    await flowsApi.run("some-flow-id", false);
    throw new Error("the call resolved; it was supposed to throw");
  } catch (e) {
    if (!(e instanceof ApiError)) throw e;
    return e;
  } finally {
    (globalThis as unknown as { fetch: unknown }).fetch = real;
  }
}

test("the payload survives req() and the throw", async () => {
  const err = await throwsFrom(409, RUN_409);
  assert(err.code === "unmapped", `code lost: ${err.code}`);
  const p = apiErrorPayload(err.body);
  assert(!!p && Array.isArray(p!.unmapped),
    "the decoded body was dropped on the way out of req() — the 409 arrives as "
    + "a bare message and the question it was asking is gone, which is exactly "
    + "how RUN came to do nothing at all");
  assert((p!.unmapped as unknown[]).length === 2, "the list is truncated");
});

test("a body-less error still throws cleanly", async () => {
  const err = await throwsFrom(500, undefined);
  assert(err.status === 500 && err.body === undefined, "a 500 with no JSON must "
    + "not become a crash on the way to reporting it");
});

test("the nested shape is where the list actually lives", () => {
  const p = apiErrorPayload(RUN_409);
  assert(!!p, "no payload");
  const list = p!.unmapped as unknown[];
  assert(Array.isArray(list) && list.length === 2, "the unmapped list did not come through");
  // The exact read that was dead: un-nested. Kept as a negative so nobody
  // "simplifies" the helper back into it.
  assert((RUN_409 as Record<string, unknown>).unmapped === undefined,
    "the server does NOT send it un-nested; a reader that assumes so gets undefined");
});

test("an un-nested body still works — some endpoints send one", () => {
  const p = apiErrorPayload({ code: "x", unmapped: [1] });
  assert(!!p && Array.isArray(p!.unmapped), "flat payloads must not regress");
});

test("a validation-error array is not mistaken for a payload", () => {
  // pydantic 422s send `detail: [...]`. Treating that array as the payload
  // object would hand callers a list where they expect named fields.
  const p = apiErrorPayload({ detail: [{ loc: ["body"], msg: "bad" }] });
  assert(!!p && Array.isArray(p!.detail), "the array should stay under `detail`");
  assert(p!.unmapped === undefined, "invented an unmapped out of a 422");
});

test("junk in, null out", () => {
  for (const junk of [null, undefined, "text", 42]) {
    assert(apiErrorPayload(junk) === null, `${String(junk)} produced a payload`);
  }
});

await Promise.all(queue);
console.log(`apiErrorPayload.test: ${passed} passed, ${failed} failed`);
for (const f of failures) console.log(f);
if (failed > 0) process.exit(1);
