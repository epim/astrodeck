// The wave-S7 fetch wrappers build the request the wire actually accepts.
//
// These wrappers are consumed by nine other tasks, and every one of them is a
// place where getting the SHAPE right matters more than getting the code to
// run. Three of the assertions here guard a decision that is invisible at the
// call site and silently destructive when it is wrong:
//
//   1. `putSwitchPortSettings` must send ONLY the keys the caller passed.
//      `protect_during_run` is TRI-state - null means "follow the port's name"
//      and is one of its three real values - so the server reads presence off
//      `model_fields_set` and an absent key means UNCHANGED. A spread over a
//      defaults object would therefore FORGE a decision: a caller editing only
//      `follow_dew` would also send `protect_during_run: null` and silently
//      un-pin a port somebody had deliberately protected, with nothing on
//      screen to show for it.
//
//   2. `sessionStackImageUrl(seq, size)` must stay BYTE-IDENTICAL to the string
//      this app has always built. An empty `?channel=` means the same thing to
//      the server and a different thing to every browser cache, so appending it
//      unconditionally would re-fetch every stack image on every client for a
//      parameter that says nothing.
//
//   3. Every URL that reaches an <img> or an <a> must be prefixed by `u()`.
//      Under the relay the whole app is mounted at `/h/<home_id>`, and a bare
//      absolute path 404s there - the failure is invisible on a LAN box and
//      total through the tunnel, which is the worst combination to ship.
//
// The rest pin the method, the path and the query encoding of each wrapper, so
// a rename on the server side goes red here rather than at 2am on the rig.
//
// Run with:  npx tsx src/lib/__tests__/wireU7b.test.ts

/* eslint-disable @typescript-eslint/no-explicit-any */

// Every module under api/ pulls in lib/base.ts, which resolves the mount base
// off `window.location` at module scope - a bare import takes the file down
// before an assertion runs. Stub first, then import dynamically (the
// coolingConfigPayload / apiErrorPayload idiom). The pathname is a RELAY one on
// purpose: a `u()` that had been replaced by a bare path would still produce a
// correct-looking string under a root mount, so testing at the root would test
// nothing.
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

const { getEphemerisStatus, refreshEphemeris, getSatellitePasses } =
  await import("../../api/ephemeris");
const {
  startVideo, getVideoState, stopVideo, listRecordings, deleteRecording,
  stackRecording, recordingSerUrl, recordingStackUrl, videoSpaceShortfall,
} = await import("../../api/video");
const { getPlanning, putPlanning } = await import("../../api/planning");
const { getSwitchPorts, setSwitchPort, putSwitchPortSettings } =
  await import("../../api/power");
const { sessionStackImageUrl } = await import("../../api/sessionStack");
const { setFocusConfig, setDewConfig } = await import("../../api/backends");
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
    `expected exactly one request, got ${calls.length}: ` +
    calls.map((c) => `${c.method} ${c.url}`).join(", "));
  return calls[0];
}

// ------------------------------------------------------------- ephemerides

await test("ephemeris status is a plain GET under the relay mount", async () => {
  await getEphemerisStatus();
  const c = only();
  assert(c.method === "GET", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/ephemeris/status", `wrong url: ${c.url}`);
});

await test("refresh POSTs the which the caller asked for", async () => {
  await refreshEphemeris("comets");
  const c = only();
  assert(c.method === "POST", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/ephemeris/refresh", `wrong url: ${c.url}`);
  assert(c.body?.which === "comets", `wrong body: ${JSON.stringify(c.body)}`);
});

await test("refresh defaults to all, so a plain button needs no argument", async () => {
  await refreshEphemeris();
  assert(only().body?.which === "all", "the default which was not 'all'");
});

await test("already_fetching arrives as a CODE, not a message to match", async () => {
  // The server answers a FLAT {"code": "already_fetching", "which": ...} with
  // no `detail` key, so there is no sentence to lift and the UI supplies one.
  // A caller must be able to branch without reading the message at all - if it
  // could not, every re-wording of a server string would be a UI bug.
  failWith(409, { code: "already_fetching", which: "satellites" });
  let err: any = null;
  try { await refreshEphemeris("satellites"); } catch (e) { err = e; }
  assert(err instanceof ApiError, `not an ApiError: ${String(err)}`);
  assert(err.status === 409, `wrong status: ${err.status}`);
  assert(err.code === "already_fetching",
    `the code did not survive parsing: ${String(err.code)}`);
});

await test("passes sends no query at all when nothing was asked for", async () => {
  // Every default is the server's own, so an omitted field and a field sent at
  // its default are the same request - and the shorter one is cacheable.
  await getSatellitePasses();
  assert(only().url === "/h/home-7/api/satellites/passes",
    `an empty query built a query string: ${calls[0].url}`);
});

await test("passes repeats ids rather than joining them", async () => {
  // FastAPI binds `ids: list[int] | None = Query(None)` from REPEATS.
  // "25544,48274" would 422 as one bad int.
  await getSatellitePasses({ hours: 6, minAltDeg: 20, ids: [25544, 48274] });
  const c = only();
  assert(c.url.includes("hours=6"), `hours missing: ${c.url}`);
  assert(c.url.includes("min_alt_deg=20"),
    `min_alt_deg missing or mis-spelled: ${c.url}`);
  assert(c.url.includes("ids=25544") && c.url.includes("ids=48274"),
    `ids were not repeated: ${c.url}`);
  assert(!c.url.includes("25544%2C") && !c.url.includes("25544,"),
    `ids were joined into one parameter: ${c.url}`);
});

// -------------------------------------------------------------- SER video

await test("startVideo POSTs the body it was handed, and nothing else", async () => {
  await startVideo({ fps: 60, duration_s: 12, roi: { x: 0, y: 0, w: 640, h: 480, bin: 1 } });
  const c = only();
  assert(c.method === "POST", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/capture/video", `wrong url: ${c.url}`);
  assert(c.body.fps === 60 && c.body.duration_s === 12,
    `the request did not survive: ${JSON.stringify(c.body)}`);
  assert(c.body.roi?.w === 640, `the roi did not survive: ${JSON.stringify(c.body)}`);
  assert(!("exposure_ms" in c.body),
    `a field the caller never set was forged: ${JSON.stringify(c.body)}`);
});

await test("the video status and stop routes are the two the wire names", async () => {
  await getVideoState();
  assert(only().url === "/h/home-7/api/capture/video", `wrong url: ${calls[0].url}`);
  calls = [];
  await stopVideo();
  const c = only();
  assert(c.method === "POST" && c.url === "/h/home-7/api/capture/video/stop",
    `wrong stop request: ${c.method} ${c.url}`);
});

await test("listRecordings unwraps the envelope, and survives an empty one", async () => {
  nextBody = { recordings: [{ id: "2026-09-10T2312-ser01" }] };
  const rows = await listRecordings();
  assert(rows.length === 1 && rows[0].id === "2026-09-10T2312-ser01",
    `the rows did not come through: ${JSON.stringify(rows)}`);
  nextBody = {};
  assert((await listRecordings()).length === 0,
    "a body with no recordings key threw instead of answering []");
});

await test("delete and stack address the recording by an ENCODED id", async () => {
  await deleteRecording("2026-09-10T2312-ser01");
  let c = only();
  assert(c.method === "DELETE", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/captures/video/2026-09-10T2312-ser01",
    `wrong url: ${c.url}`);
  calls = [];
  await stackRecording("2026-09-10T2312-ser01", 40);
  c = only();
  assert(c.method === "POST", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/captures/video/2026-09-10T2312-ser01/stack",
    `wrong url: ${c.url}`);
  assert(c.body?.keep_pct === 40, `wrong body: ${JSON.stringify(c.body)}`);
});

await test("stackRecording defaults keep_pct to the server's 25", async () => {
  await stackRecording("rec");
  assert(only().body?.keep_pct === 25, "the shipped keep_pct default moved");
});

await test("the two media URLs carry the relay mount prefix", async () => {
  // The one that matters most: these go into an <a href> and an <img src>,
  // where a 404 through the tunnel is silent. A bare path looks perfect on a
  // LAN box and fails for every remote client.
  const ser = recordingSerUrl("2026-09-10T2312-ser01");
  assert(ser.startsWith("/h/home-7/"),
    `the .ser url is not under the relay mount: ${ser}`);
  assert(ser === "/h/home-7/api/captures/video/2026-09-10T2312-ser01.ser",
    `wrong .ser url: ${ser}`);
  const png = recordingStackUrl("2026-09-10T2312-ser01");
  assert(png.startsWith("/h/home-7/"),
    `the stack url is not under the relay mount: ${png}`);
  assert(png.endsWith("/stack.png"), `wrong stack url: ${png}`);
});

await test("a 507 hands back both byte figures, and nothing else does", async () => {
  // The one refusal on this surface with no `code`: the two numbers ARE the
  // message, and a sheet must never have to reach into err.body for them.
  failWith(507, {
    detail: "insufficient disk space", free_bytes: 2_100_000_000,
    required_bytes: 5_800_000_000,
  });
  let err: any = null;
  try { await startVideo({}); } catch (e) { err = e; }
  const short = videoSpaceShortfall(err);
  assert(short !== null, "the 507 byte figures were not readable");
  assert(short!.free_bytes === 2_100_000_000 &&
         short!.required_bytes === 5_800_000_000,
    `wrong figures: ${JSON.stringify(short)}`);
  // A coded 409 is not a disk shortfall, however it is shaped.
  failWith(409, { detail: { detail: "a recording is already running", code: "lane_busy" } });
  let busy: any = null;
  try { await startVideo({}); } catch (e) { busy = e; }
  assert(busy.code === "lane_busy", `the 409 code was lost: ${String(busy.code)}`);
  assert(videoSpaceShortfall(busy) === null,
    "a lane_busy 409 was read as a disk shortfall");
  assert(videoSpaceShortfall(new Error("network")) === null,
    "a plain Error was read as a disk shortfall");
});

// --------------------------------------------------------------- planning

await test("planning reads with GET and writes with PUT", async () => {
  await getPlanning();
  assert(only().method === "GET", `wrong method: ${calls[0].method}`);
  assert(calls[0].url === "/h/home-7/api/planning", `wrong url: ${calls[0].url}`);
  calls = [];
  await putPlanning({ pool: ["M 31", "NGC 7331"] });
  const c = only();
  assert(c.method === "PUT", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/planning", `wrong url: ${c.url}`);
});

await test("a quick-only patch does not send a pool", async () => {
  // `pool` is REPLACED WHOLE when present, so forging an empty one would wipe
  // the operator's shortlist on a write that only meant to move the hours.
  await putPlanning({ quick: { hours: 3 } });
  const c = only();
  assert(Object.keys(c.body).length === 1,
    `the patch grew a key: ${JSON.stringify(c.body)}`);
  assert(c.body.quick?.hours === 3, `wrong body: ${JSON.stringify(c.body)}`);
  assert(!("pool" in c.body),
    `a pool the caller never sent would have REPLACED the stored one: ` +
    JSON.stringify(c.body));
});

await test("an explicitly empty pool is sent, because emptying it is a request",
  async () => {
    await putPlanning({ pool: [] });
    const c = only();
    assert("pool" in c.body && Array.isArray(c.body.pool) && c.body.pool.length === 0,
      `clearing the pool did not reach the server: ${JSON.stringify(c.body)}`);
    assert(!("quick" in c.body), `quick was forged: ${JSON.stringify(c.body)}`);
  });

// ------------------------------------------------------------------ power

await test("the two port routes are the ones the wire names", async () => {
  await getSwitchPorts();
  assert(only().url === "/h/home-7/api/switch/ports", `wrong url: ${calls[0].url}`);
  calls = [];
  await setSwitchPort(3, 1);
  const c = only();
  assert(c.method === "POST" && c.url === "/h/home-7/api/switch/set",
    `wrong set request: ${c.method} ${c.url}`);
  assert(c.body?.port_id === 3 && c.body?.value === 1,
    `wrong set body: ${JSON.stringify(c.body)}`);
});

await test("a follow_dew edit sends EXACTLY ONE key", async () => {
  // THE ONE THAT MATTERS. Absent means unchanged; a spread over a defaults
  // object would send protect_during_run: null as well and silently un-pin a
  // port somebody deliberately protected.
  await putSwitchPortSettings(3, { follow_dew: true });
  const c = only();
  assert(c.method === "PUT", `wrong method: ${c.method}`);
  assert(c.url === "/h/home-7/api/switch/ports/3", `wrong url: ${c.url}`);
  assert(Object.keys(c.body).length === 1,
    `the settings write carried a decision the caller never made: ` +
    JSON.stringify(c.body));
  assert(c.body.follow_dew === true, `wrong body: ${JSON.stringify(c.body)}`);
});

await test("a null protect_during_run is SENT, not omitted", async () => {
  // The other half of the same rule: null is one of the three real values
  // ("follow the port's name, and keep following it through a rename"), so
  // dropping it as if it were absence would make "clear this override"
  // unreachable through the only route that writes it.
  await putSwitchPortSettings(3, { protect_during_run: null });
  const c = only();
  assert("protect_during_run" in c.body,
    `the null was dropped as if it meant nothing: ${JSON.stringify(c.body)}`);
  assert(c.body.protect_during_run === null,
    `the null did not survive: ${JSON.stringify(c.body)}`);
  assert(Object.keys(c.body).length === 1,
    `follow_dew was forged: ${JSON.stringify(c.body)}`);
});

await test("false is a decision too, and reaches the server", async () => {
  await putSwitchPortSettings(7, { protect_during_run: false, follow_dew: false });
  const c = only();
  assert(c.body.protect_during_run === false && c.body.follow_dew === false,
    `a truthiness test dropped the two falsy decisions: ${JSON.stringify(c.body)}`);
  assert(Object.keys(c.body).length === 2, `wrong body: ${JSON.stringify(c.body)}`);
});

// ------------------------------------------------------- stack preview URL

await test("the composite URL is byte-identical to the shipped one", async () => {
  const url = sessionStackImageUrl(7, 1200);
  assert(url === "/h/home-7/api/sequence/stack/preview.jpg?size=1200&seq=7",
    `the composite URL moved, invalidating every client's cache: ${url}`);
});

await test("a channel is appended, and encoded", async () => {
  assert(sessionStackImageUrl(7, 1200, "Ha")
    === "/h/home-7/api/sequence/stack/preview.jpg?size=1200&seq=7&channel=Ha",
    `wrong channel URL: ${sessionStackImageUrl(7, 1200, "Ha")}`);
  const odd = sessionStackImageUrl(7, 1200, "H-alpha narrow");
  assert(odd.endsWith("&channel=H-alpha%20narrow"),
    `a channel name with a space was not encoded: ${odd}`);
  // An empty string is not a channel: it would mean the composite to the
  // server and a different cache key to the browser.
  assert(sessionStackImageUrl(7, 1200, "") === sessionStackImageUrl(7, 1200),
    "an empty channel changed the URL");
});

// ------------------------------------------------------- config sub-blocks

await test("focus and dew post their own block to /api/config", async () => {
  await setFocusConfig({
    approach_overshoot_steps: 200,
    temp_comp: {
      enabled: true, steps_per_c: -12.5, reference_temp_c: 11.0,
      reference_position: 11218, max_step_per_move: 200, deadband_steps: 5,
    },
  });
  let c = only();
  assert(c.method === "POST" && c.url === "/h/home-7/api/config",
    `wrong focus request: ${c.method} ${c.url}`);
  assert(Object.keys(c.body).length === 1 && "focus" in c.body,
    `the focus write carried another block: ${JSON.stringify(c.body)}`);
  assert(c.body.focus.temp_comp.steps_per_c === -12.5,
    `the signed coefficient did not survive: ${JSON.stringify(c.body.focus)}`);
  calls = [];
  await setDewConfig({
    enabled: true, margin_full_c: 1.0, margin_off_c: 5.0, min_power: 0,
    max_power: 100, camera_window: true, manual_override_s: 7200,
    interval_s: 120,
  });
  c = only();
  assert(Object.keys(c.body).length === 1 && "dew" in c.body,
    `the dew write carried another block: ${JSON.stringify(c.body)}`);
  assert(c.body.dew.margin_off_c === 5.0, `wrong dew body: ${JSON.stringify(c.body)}`);
});

console.log(`wireU7b: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
if (failed > 0) process.exit(1);
