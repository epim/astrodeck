// The promote wrappers build the request the wire accepts, and read the answer
// by CODE (T-U7b-3, D-SES-4).
//
// Three of the assertions here guard a decision that is invisible at the call
// site and silently destructive when it is wrong:
//
//   1. `frame_id` IS SENT. It is an interlock, not a selector: the buffer holds
//      exactly one frame, so naming the one the card is showing changes nothing
//      about which frame is written and everything about what happens when the
//      buffer moved on underneath it. Drop the key and the failure is a saved
//      exposure the operator never looked at, filed under the settings and the
//      target of the one they did - with a success toast over it.
//
//   2. ONLY the keys the caller passed. A spread over a defaults object would
//      send `frame_id: null`, which the server reads as "save whatever is
//      held" - the exact opposite of the interlock, arrived at by tidiness.
//
//   3. THE REFUSAL IS THE CODE, NEVER THE SENTENCE. The `already_saved` fixture
//      below is deliberately worded UNLIKE the phrase the UI shows, so a
//      wrapper that recognised its own copy in the server's message would go
//      red here rather than on the rig. Three refusals mean three different
//      next actions (re-shoot / nothing / press again) and a client that reads
//      the prose loses all three the first time somebody edits a docstring.
//
// And the fourth: a 404 with NO code is FastAPI saying the route does not
// exist, not the server saying the frame is gone. The two want opposite
// screens, so `isPromoteRouteMissing` has to tell them apart.
//
// Run with:  npx tsx src/lib/__tests__/wireCapture.test.ts

/* eslint-disable @typescript-eslint/no-explicit-any */

// api/capture.ts pulls in lib/base.ts, which resolves the mount base off
// `window.location` at module scope - a bare import takes the file down before
// an assertion runs. Stub first, then import dynamically (the wireU7b idiom).
// The pathname is a RELAY one on purpose: a wrapper that had lost its base
// prefix would still build a correct-looking string under a root mount, so
// testing at the root would test nothing.
(globalThis as unknown as { window: unknown }).window = {
  location: { pathname: "/h/home-7/rig/capture", origin: "http://local" },
} as unknown as Window;

interface Call { method: string; url: string; body: any; }

let calls: Call[] = [];
let nextStatus = 200;
let nextBody: any = {};

(globalThis as any).fetch = async (url: any, init: any) => {
  calls.push({
    method: init?.method ?? "GET",
    url: String(url),
    body: init?.body ? JSON.parse(init.body) : null,
  });
  const status = nextStatus;
  const body = nextBody;
  nextStatus = 200;
  nextBody = {};
  return {
    ok: status < 400,
    status,
    statusText: `HTTP ${status}`,
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
};

/** Arm the next response as a failure carrying `body`. */
function failWith(status: number, body: any): void {
  nextStatus = status;
  nextBody = body;
}

const {
  getLastFrame, promoteLastFrame, PromoteRefused, isPromoteRouteMissing,
} = await import("../../api/capture");
const { ApiError } = await import("../../api");

let passed = 0, failed = 0;
const failures: string[] = [];
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  calls = [];
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}

/** The single call this step made, or a failure naming what it did instead. */
function only(): Call {
  assert(calls.length === 1,
    `expected exactly one request, got ${calls.length}: `
    + calls.map((c) => `${c.method} ${c.url}`).join(", "));
  return calls[0];
}

/** Run `fn` and hand back what it threw, or fail if it threw nothing. */
async function threw(fn: () => Promise<unknown>): Promise<unknown> {
  try { await fn(); } catch (e) { return e; }
  throw new Error("the call resolved where a refusal was armed");
}

// ------------------------------------------------------------ the buffer read

await test("the held frame is a plain GET under the relay mount", async () => {
  nextBody = {
    available: true, id: 4, ts: 1757500000, exposure_s: 30, gain: 120,
    binning: 1, frame_type: "Light", target: "NGC 7331", filter: "L",
    saved: false,
  };
  const f = await getLastFrame();
  const c = only();
  assert(c.method === "GET", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/capture/last", `wrong url: ${c.url}`);
  assert(f.available === true && f.id === 4,
    `the answer did not survive the wrapper: ${JSON.stringify(f)}`);
  assert(f.saved === false, "GET /api/capture/last described a SAVED frame");
});

// ----------------------------------------------------------------- the write

await test("a bare promote sends an empty body - no key is forged", async () => {
  nextBody = { saved: true, path: "2026-09-10/NGC 7331_L_30s.fits", filter: "L",
    id: 4, target: "NGC 7331", ts: 1757500000 };
  await promoteLastFrame();
  const c = only();
  assert(c.method === "POST", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/capture/last/save", `wrong url: ${c.url}`);
  assert(Object.keys(c.body).length === 0,
    `a caller that passed nothing sent something: ${JSON.stringify(c.body)}`);
});

await test("the frame_id interlock is sent, and alone", async () => {
  nextBody = { saved: true, path: "a.fits", filter: "L", id: 4, target: "",
    ts: 1 };
  await promoteLastFrame({ frame_id: 4 });
  const c = only();
  assert(c.body.frame_id === 4,
    `the interlock never reached the wire: ${JSON.stringify(c.body)}`);
  assert(Object.keys(c.body).length === 1,
    `a defaults spread forged a second key: ${JSON.stringify(c.body)}`);
});

await test("a target override rides beside the interlock, both verbatim", async () => {
  nextBody = { saved: true, path: "a.fits", filter: "L", id: 4, target: "M 31",
    ts: 1 };
  const r = await promoteLastFrame({ target: "M 31", frame_id: 4 });
  const c = only();
  assert(c.body.target === "M 31" && c.body.frame_id === 4,
    `wrong body: ${JSON.stringify(c.body)}`);
  assert(Object.keys(c.body).length === 2,
    `the override carried a third key: ${JSON.stringify(c.body)}`);
  assert(r.path === "a.fits" && r.target === "M 31",
    `the saved row did not survive: ${JSON.stringify(r)}`);
});

await test("an explicitly empty target is still a key the caller wrote", async () => {
  nextBody = { saved: true, path: "a.fits", filter: "L", id: 4, target: "",
    ts: 1 };
  await promoteLastFrame({ target: "" });
  const c = only();
  assert("target" in c.body && c.body.target === "",
    `an empty override was dropped rather than sent: ${JSON.stringify(c.body)}`);
  assert(!("frame_id" in c.body),
    `frame_id appeared without a caller: ${JSON.stringify(c.body)}`);
});

// -------------------------------------------------------------- the refusals

await test("404 nothing_to_promote arrives as a typed refusal, by code", async () => {
  failWith(404, { detail: { detail: "no unsaved frame to save",
    code: "nothing_to_promote" } });
  const e = await threw(() => promoteLastFrame({ frame_id: 4 }));
  assert(e instanceof PromoteRefused,
    `a refusal reached the caller as ${(e as Error).name}`);
  const r = e as InstanceType<typeof PromoteRefused>;
  assert(r.code === "nothing_to_promote", `wrong code: ${r.code}`);
  assert(r.status === 404, `wrong status: ${r.status}`);
  assert(r.message === "no unsaved frame to save",
    `the server's sentence was re-worded: ${r.message}`);
});

await test("409 already_saved is read from the code, not the prose", async () => {
  // Worded UNLIKE anything the UI says on purpose: a wrapper that matched its
  // own copy in the message would find nothing here and fall through.
  failWith(409, { detail: { detail: "that exposure is already on disk",
    code: "already_saved" } });
  const e = await threw(() => promoteLastFrame({ frame_id: 4 }));
  assert(e instanceof PromoteRefused,
    `a refusal reached the caller as ${(e as Error).name}`);
  const r = e as InstanceType<typeof PromoteRefused>;
  assert(r.code === "already_saved", `wrong code: ${r.code}`);
  assert(r.status === 409, `wrong status: ${r.status}`);
});

await test("409 frame_id_mismatch keeps the sentence carrying both ids", async () => {
  failWith(409, { detail: {
    detail: "that frame is no longer the one being held (asked for 4, holding 7)",
    code: "frame_id_mismatch" } });
  const e = await threw(() => promoteLastFrame({ frame_id: 4 }));
  const r = e as InstanceType<typeof PromoteRefused>;
  assert(r instanceof PromoteRefused, "the mismatch was not typed");
  assert(r.code === "frame_id_mismatch", `wrong code: ${r.code}`);
  assert(/asked for 4, holding 7/.test(r.message),
    `the two ids are the only place that number appears, and they were lost: ${r.message}`);
});

// -------------------------------------------------- an engine without the route

await test("a bare 404 is a MISSING ROUTE, not a missing frame", async () => {
  failWith(404, { detail: "Not Found" });
  const e = await threw(() => promoteLastFrame({ frame_id: 4 }));
  assert(!(e instanceof PromoteRefused),
    "an older engine's 404 was read as 'that frame is gone' - the two want opposite screens");
  assert(e instanceof ApiError && e.status === 404,
    `wrong error for a missing route: ${(e as Error).name}`);
  assert(isPromoteRouteMissing(e), "the missing-route test does not recognise it");
  // Built by hand rather than round-tripped through `promoteLastFrame`: that
  // one already converts a coded 404 into a `PromoteRefused`, which is not an
  // `ApiError` at all, so asking `isPromoteRouteMissing` about it would be true
  // for the wrong reason and could never go red. This is the shape the function
  // has to say NO to.
  const coded = new ApiError("no unsaved frame to save", 404, false,
    "nothing_to_promote");
  assert(!isPromoteRouteMissing(coded),
    "a coded 404 was mistaken for an engine with no promote route - the frame is "
    + "gone and the route is fine, which is the opposite screen");
});

await test("anything that is not a refusal reaches the caller unchanged", async () => {
  failWith(500, { detail: "disk went away" });
  const e = await threw(() => promoteLastFrame({ frame_id: 4 }));
  assert(!(e instanceof PromoteRefused),
    "a server fault was dressed up as an operator-readable refusal");
  assert(e instanceof ApiError && e.status === 500,
    `wrong error: ${(e as Error).name}`);
});

const total = passed + failed;
console.log(`wireCapture: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export { passed, failed, total };
export default { passed, failed, total };
if (failed > 0) process.exit(1);
