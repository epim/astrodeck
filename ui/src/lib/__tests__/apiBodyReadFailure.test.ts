// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// apiBodyReadFailure.test.ts - a success body that fails to read for any reason
// but the budget is an ApiError too (#918).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/lib/__tests__/apiBodyReadFailure.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// #870 made a body read that runs out of budget an `ApiError(timedOut)`. Every
// other way a 200's body ends was still the browser's own exception:
//   * the connection drops mid-body: `res.json()` rejects with a raw
//     `TypeError`, no `instanceof ApiError` caller recognises it, screens print
//     the browser's text, and `isTransientLoadError` does not retry it;
//   * the answer is not JSON (the relay serves the SPA's HTML for a path it
//     does not route, with a 200): a raw `SyntaxError`.
//
// These cases drive the REAL `api.get` with `fetch` replaced by one that
// answers a REAL `Response`, so the exception classes are the platform's, not
// a stub's guess at them: a `ReadableStream` that errors after its first chunk
// is a connection that dropped mid-body, and an HTML text body is the relay's
// SPA page.
//
// Named mutants (each turns the cases beside it red):
//   D1  req() returns the success body without converting a failed read
//       (`return await withinBudget(res.json(), signal)`): T1, T2, T3, T4.
//   D2  unreadableBody() answers every failure with the network error: T2, T4.
//   D3  unreadableBody() answers every failure with the unreadable-answer
//       error: T1, T3, T4.
//   D4  the unreadable-answer error carries status 0 (retried as unreachable):
//       T2.

/* eslint-disable @typescript-eslint/no-explicit-any */

// `../../api` reads `window` at module scope (lib/base.ts resolves the base
// path from the URL), so a bare import takes this file down before an
// assertion runs. One stub, then a dynamic import.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };

const { api, ApiError } = await import("../../api");
const { isTransientLoadError } = await import("../retryLoad");

const g = globalThis as any;
const realFetch = g.fetch;

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
  finally { g.fetch = realFetch; }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const NETWORK_TEXT = "network error — server unreachable";

/** What the call threw, as the call site sees it. */
async function thrown(p: Promise<unknown>): Promise<unknown> {
  try { await p; } catch (e) { return e; }
  throw new Error("the call resolved");
}
function describe(e: unknown): string {
  return e instanceof Error ? `${e.constructor.name} "${e.message}"` : String(e);
}

/** A 200 whose body gives one chunk and then the stream errors: the
 *  connection dropped before the body was whole. */
function droppedMidBody(reason: unknown = new TypeError("terminated")): Response {
  const body = new ReadableStream<Uint8Array>({
    start(c) { c.enqueue(new TextEncoder().encode('{"cards":[')); c.error(reason); },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "application/json" } });
}

/** The relay's SPA page, served with a 200 for a path it does not route. */
function spaPage(): Response {
  return new Response("<!doctype html><html><body>AstroDeck</body></html>",
    { status: 200, headers: { "Content-Type": "text/html" } });
}

const answer = (res: () => Response): void => { g.fetch = async () => res(); };

await test("T1 a connection dropped mid-body is the network ApiError, which a load retries", async () => {
  answer(() => droppedMidBody());
  const e = await thrown(api.get("/api/flows"));
  assert(e instanceof ApiError, `threw ${describe(e)}, not an ApiError`);
  const err = e as InstanceType<typeof ApiError>;
  assert(err.message === NETWORK_TEXT, `message is "${err.message}"`);
  assert(err.status === 0 && err.timedOut === false, `status ${err.status}, timedOut ${err.timedOut}`);
  assert(isTransientLoadError(err), "a dropped connection is not asked again by the retrying loads");
});

await test("T2 a 200 that is not JSON is an unreadable-answer ApiError, which a load does not retry", async () => {
  answer(spaPage);
  const e = await thrown(api.get("/api/flows"));
  assert(e instanceof ApiError, `threw ${describe(e)}, not an ApiError`);
  const err = e as InstanceType<typeof ApiError>;
  assert(/^unreadable answer/.test(err.message), `message is "${err.message}"`);
  assert(err.message !== NETWORK_TEXT, "an HTML page was reported as an unreachable server");
  assert(err.status === 200 && err.timedOut === false, `status ${err.status}, timedOut ${err.timedOut}`);
  assert(!/doctype|Unexpected token/i.test(err.message),
    `the browser's own text reached the screen: "${err.message}"`);
  assert(!isTransientLoadError(err), "a page the server will serve again is asked again as if the link had dropped");
});

await test("T3 any other way the body read ends is the network ApiError, not a raw exception", async () => {
  for (const reason of [
    new DOMException("The operation was aborted.", "AbortError"),
    new DOMException("A network error occurred.", "NetworkError"),
    new Error("socket hang up"),
  ]) {
    answer(() => droppedMidBody(reason));
    const e = await thrown(api.get("/api/flows"));
    assert(e instanceof ApiError, `a body stream that errored with ${describe(reason)} threw ${describe(e)}, not an ApiError`);
    assert((e as InstanceType<typeof ApiError>).message === NETWORK_TEXT,
      `${describe(reason)} read as "${(e as Error).message}"`);
    assert((e as InstanceType<typeof ApiError>).status === 0, `${describe(reason)} came back with a status`);
  }
});

await test("T4 the same holds for a write: a POST whose answer drops or is HTML", async () => {
  answer(() => droppedMidBody());
  const dropped = await thrown(api.post("/api/flows/f/run", {}));
  assert(dropped instanceof ApiError && dropped.message === NETWORK_TEXT, `POST dropped threw ${describe(dropped)}`);
  answer(spaPage);
  const html = await thrown(api.post("/api/flows/f/run", {}));
  assert(html instanceof ApiError && /^unreadable answer/.test(html.message), `POST HTML threw ${describe(html)}`);
});

// --------------------------------------------------- what must not change
await test("R1 a body that arrives whole is returned as parsed", async () => {
  answer(() => new Response('{"cards":[{"id":"a"}]}', { status: 200, headers: { "Content-Type": "application/json" } }));
  const v = await api.get<{ cards: { id: string }[] }>("/api/flows");
  assert(JSON.stringify(v) === '{"cards":[{"id":"a"}]}', `the body came back as ${JSON.stringify(v)}`);
});

await test("R2 an error response keeps its status and message whatever its body does", async () => {
  g.fetch = async () => new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" });
  const html = await thrown(api.get("/api/flows"));
  assert(html instanceof ApiError && html.status === 502 && html.message === "Bad Gateway",
    `a 502 with an HTML body threw ${describe(html)} (status ${(html as any).status})`);
  g.fetch = async () => new Response('{"detail":"no flow named x"}', { status: 404 });
  const json = await thrown(api.get("/api/flows/x"));
  assert(json instanceof ApiError && json.status === 404 && json.message === "no flow named x",
    `a 404 threw ${describe(json)}`);
});

await test("R3 a fetch that fails is still the network ApiError", async () => {
  g.fetch = async () => { throw new TypeError("Failed to fetch"); };
  const e = await thrown(api.get("/api/flows"));
  assert(e instanceof ApiError && e.message === NETWORK_TEXT && e.status === 0, `threw ${describe(e)}`);
});

g.fetch = realFetch;
console.log(`apiBodyReadFailure.test: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  process.exitCode = 1;
}
