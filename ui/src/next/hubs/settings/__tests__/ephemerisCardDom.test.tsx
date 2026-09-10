// ephemerisCardDom.test.tsx - the satellite/comet element card in Settings >
// SKY DATA, MOUNTED (wave U7b, T-U7b-1; decision D-SKY-1).
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/ephemerisCardDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// FIVE THINGS, and four of them are about a claim nothing keeps:
//
//  1. A PRECONDITION MARKER, and the two rows behind it. A card that rendered
//     its title and nothing else would satisfy "no request was fired" for the
//     wrong reason.
//  2. REFRESH SENDS `{"which": "all"}`. The route takes a narrow value too, and
//     a button labelled REFRESH ELEMENTS that fetched only satellites would
//     leave the comets stale while looking like it had done the job.
//  3. THE 409 IS READ BY CODE, NOT BY MESSAGE. `routes.py:71-73` answers a FLAT
//     `{"code": "already_fetching", "which": ...}` with no `detail`, so
//     `ApiError.message` is the JSON-stringified body - which is not copy. The
//     UI supplies the sentence, and a second press must not become a second
//     outbound fetch.
//  4. A 404 IS AN OLDER ENGINE, NOT A FAILURE. S7L mounts this router; a rig
//     without it must render the card's explanation and NO controls, rather
//     than a button that cannot work.
//  5. A VIEWER SEES THE BUTTON, DIMMED, CARRYING THE REASON - and pressing it
//     posts nothing. Hiding it would be the other design, and a native
//     `disabled` would strip the reason out of the tree.
//
// Convention: jsdom by hand, createRoot + act, native events, settle(), a
// hand-written g.fetch recording into `asked`/`posts`, store seeding via
// useStore.setState, printed tally + the `{ passed, failed, total }` export
// (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// `SkyPackSheet` reaches the calibration area's own stylesheet through
// `tuning/calibration/index.ts`. Node has no idea what a `.css` file is, so a
// synchronous load hook answers with an empty module; it has to run BEFORE any
// import that reaches one, which is why every import below is dynamic.
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/settings/general/skyPack", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "localStorage", "sessionStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "DOMException",
  "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const NOW = Date.UTC(2026, 8, 10, 16, 7, 0);
Date.now = () => NOW;

// ------------------------------------------------------------------- network
const asked: string[] = [];
const posts: { url: string; method: string; body: any }[] = [];
const ok = (data: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });
const fail = (status: number, body: unknown) => ({
  ok: false, status, statusText: "", json: async () => body,
});

const STALE_SATS =
  "These satellite elements are 9 days old; a pass time this old can be a "
  + "minute out. Refresh them from Sky settings when the rig is online.";

let STATUS: any = {
  satellites: {
    which: "satellites", present: true, source: "celestrak",
    fetched_unix: NOW / 1000 - 9 * 86_400, age_days: 9.1, stale: true,
    count: 203, note: STALE_SATS,
  },
  comets: {
    which: "comets", present: true, source: "mpc",
    fetched_unix: NOW / 1000 - 3600, age_days: 0.04, stale: false,
    count: 941, note: null,
  },
  fetching: [],
  last_outcome: {},
};

/** What `POST /api/ephemeris/refresh` answers next. The 409 body is the FLAT
 *  shape the server really sends: a code and nothing to read out loud. */
let REFRESH_REPLY: "202" | "409" | "404" = "202";
let STATUS_REPLY: "200" | "404" = "200";

g.fetch = async (url: any, init?: { method?: string; body?: string }) => {
  const u = String(url);
  const method = String(init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  if (method !== "GET" && init?.body != null) {
    let body: unknown = init.body;
    try { body = JSON.parse(init.body); } catch { /* leave as text */ }
    posts.push({ url: u, method, body });
  }
  if (u.includes("/api/ephemeris/refresh")) {
    if (REFRESH_REPLY === "409") return fail(409, { code: "already_fetching", which: "all" });
    if (REFRESH_REPLY === "404") return fail(404, { detail: "Not Found" });
    return ok({ started: true, which: "all" });
  }
  if (u.includes("/api/ephemeris/status")) {
    if (STATUS_REPLY === "404") return fail(404, { detail: "Not Found" });
    return ok(STATUS);
  }
  if (u.includes("/api/survey/pack")) {
    return ok({ present: false, slug: "", survey: "", order: null, bytes: null,
      tile_count: null, fetched_at: null, fetching: null });
  }
  return ok({});
};

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { accessPhrase } = await import("../../../../lib/caps");
const { SkyPackSheet } = await import("../sheets/SkyPackSheet");
const {
  ALREADY_FETCHING, NO_EPHEMERIS_ENGINE, REFRESH_STARTED, cacheLine,
} = await import("../sheets/EphemerisCard");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 30)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const SP: { params: Record<string, string>; depth: 0 | 1 } = { params: {}, depth: 0 };

function mount(): { host: any; root: any } {
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(createElement(SkyPackSheet, SP)); });
  return { host, root };
}
async function unmount(m: { host: any; root: any }): Promise<void> {
  await act(async () => { m.root.unmount(); });
  m.host.remove();
}

const byId = (host: any, id: string): any => host.querySelector(`[data-testid="${id}"]`);
const text = (host: any): string => String(host.textContent ?? "").replace(/\s+/g, " ");
const click = (el: any): void => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const refreshPosts = (from: number) =>
  posts.slice(from).filter((p) => p.url.includes("/api/ephemeris/refresh"));

const ADMIN_CAPS = [
  "view.status", "view.preview", "view.media", "view.site_precise",
  "view.site_derived", "view.weather", "control.capture", "control.mount",
  "control.guide", "control.power", "config.safety", "config.solar_override",
  "config.backend", "config.site_optics", "config.alerts", "admin.users",
  "system.update",
];

function seed(caps: string[], role: string): void {
  act(() => {
    useStore.setState({
      principal: { role, email: `${role}@rig`, caps },
      equipConnected: true,
      wsPhase: "up",
      toasts: [],
      status: { connected: {} },
      config: { survey: { online_fetch: false } },
    } as never);
  });
}

// ============================================================ 1. the admin
seed(ADMIN_CAPS, "admin");
let m = mount();
await settle();

test("the card rendered, with a line per element file", () => {
  assert(byId(m.host, "settings-ephemeris") != null,
    "no settings-ephemeris marker: the fixture is wrong, not the component");
  assert(byId(m.host, "ephemeris-row-satellites") != null, "no satellites row");
  assert(byId(m.host, "ephemeris-row-comets") != null, "no comets row");
  const t = text(m.host);
  assert(/203 objects/.test(t), `the satellite count is missing: ${t}`);
  assert(/941 objects/.test(t), `the comet count is missing: ${t}`);
  assert(/celestrak/.test(t) && /mpc/.test(t), `the sources are missing: ${t}`);
});

test("the sheet is SKY DATA - the title names both downloads", () => {
  const title = m.host.querySelector(".nx-sheet-title");
  eq(String(title?.textContent), "SKY DATA", "sheet title:");
  assert(/orbital elements/.test(text(m.host)),
    "the sub-line does not say the sheet holds the element files");
});

test("a stale note is the server's own sentence, verbatim", () => {
  const note = byId(m.host, "ephemeris-note-satellites");
  assert(note != null, "no note on a stale element file");
  eq(String(note.textContent), STALE_SATS, "the stale note must be verbatim:");
  // The fresh one has nothing to say, and says nothing.
  assert(byId(m.host, "ephemeris-note-comets") == null,
    "a fresh element file grew a note out of nowhere");
});

test("a cache line says how many, from where and when - not just a tick", () => {
  assert(/objects/.test(cacheLine(STATUS.satellites)), "no count in the line");
  eq(cacheLine({ ...STATUS.comets, present: false }), "no elements on the rig",
    "an absent cache:");
  eq(cacheLine(null), "not loaded", "nothing fetched yet:");
});

await testAsync("REFRESH ELEMENTS posts {\"which\": \"all\"} once", async () => {
  const from = posts.length;
  const btn = byId(m.host, "ephemeris-refresh");
  assert(btn != null, "no REFRESH ELEMENTS button");
  assert(btn.getAttribute("aria-disabled") !== "true", "an admin's refresh is locked");
  click(btn);
  await settle();
  const fired = refreshPosts(from);
  eq(fired.length, 1, "refresh requests:");
  eq(fired[0].method, "POST", "refresh method:");
  eq(JSON.stringify(fired[0].body), JSON.stringify({ which: "all" }), "refresh body:");
  assert(text(m.host).includes(REFRESH_STARTED),
    `the 202 said nothing about what happens next: ${text(m.host)}`);
});

await testAsync("an idle card does not poll - nothing is in flight", async () => {
  // R11: `POST /api/ephemeris/refresh` reaches the internet and the status
  // route is only worth reading while a fetch is running. `fetching` is empty
  // here, so the 5 s interval must never start.
  const before = asked.filter((a) => a.includes("/api/ephemeris/status")).length;
  await act(async () => { await new Promise((r) => setTimeout(r, 5400)); });
  const after = asked.filter((a) => a.includes("/api/ephemeris/status")).length;
  eq(after, before, "an idle card asked for status again:");
});

await unmount(m);

// ================================================= 2. the second press (409)
REFRESH_REPLY = "409";
m = mount();
await settle();

await testAsync("a 409 already_fetching explains itself and does not fire again", async () => {
  const from = posts.length;
  click(byId(m.host, "ephemeris-refresh"));
  await settle();
  eq(refreshPosts(from).length, 1, "requests after a refused press:");
  const t = text(m.host);
  assert(t.includes(ALREADY_FETCHING),
    `the refusal did not reach the reader: ${t}`);
  // The body the server sends carries NO sentence, so the stringified JSON must
  // never be what is shown. This is the assertion that goes red if the branch
  // reads the message instead of the code.
  assert(!/already_fetching"/.test(t) && !/\{"code"/.test(t),
    `a raw error body reached the screen: ${t}`);
  // And it is not an ERROR: a fetch already running is the answer, not a fault.
  assert(byId(m.host, "ephemeris-error") == null,
    "a 409 already_fetching was rendered as a failure");

  // A second press is still one request per press and still no retry loop.
  const from2 = posts.length;
  click(byId(m.host, "ephemeris-refresh"));
  await settle();
  eq(refreshPosts(from2).length, 1, "a refused press retried itself:");
});

await unmount(m);

// ====================================== 3. an engine without the routes (404)
REFRESH_REPLY = "202";
STATUS_REPLY = "404";
m = mount();
await settle();

test("a rig without the ephemeris routes says so and offers no controls", () => {
  assert(byId(m.host, "settings-ephemeris") != null, "the card vanished entirely");
  const note = byId(m.host, "ephemeris-absent");
  assert(note != null, "no explanation on an engine that does not carry ephemerides");
  eq(String(note.textContent), NO_EPHEMERIS_ENGINE, "the absent sentence:");
  assert(byId(m.host, "ephemeris-refresh") == null,
    "a button that cannot work is still on screen");
  // The rest of the sheet is untouched: the offline pack editor still renders.
  assert(byId(m.host, "pack-card") != null,
    "a missing ephemeris router took the survey pack editor down with it");
});

await unmount(m);

// ============================================================ 4. the viewer
STATUS_REPLY = "200";
seed(["view.status", "view.preview"], "viewer");
m = mount();
await settle();

await testAsync("a viewer sees the same button, dimmed, naming what it needs", async () => {
  const btn = byId(m.host, "ephemeris-refresh");
  assert(btn != null, "the refresh button was hidden from a viewer instead of locked");
  eq(btn.getAttribute("aria-disabled"), "true", "the locked button's aria state:");
  assert(!btn.hasAttribute("disabled"),
    "a native disabled attribute strips the reason out of the tree");
  const phrase = accessPhrase("config.site_optics");
  const reason = `${btn.getAttribute("title") ?? ""} ${text(m.host)}`;
  assert(reason.includes(phrase),
    `the lock does not name what it needs (${phrase}): ${reason}`);

  const from = posts.length;
  click(btn);
  await settle();
  eq(refreshPosts(from).length, 0, "a viewer's press reached the network:");
});

await unmount(m);

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`ephemerisCardDom: ${passed}/${total} passed`);
for (const f of failures) console.error(f);
if (failed > 0) process.exitCode = 1;

export default { passed, failed, total };
export { passed, failed, total };
