// profileActivate.test.tsx — Settings → Profiles, MOUNTED, across an activate.
//
//   Run directly:  npx tsx src/components/settings/__tests__/profileActivate.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// TWO DEFECTS, both of them about a control reporting the REQUEST instead of
// the RIG (audit #16, #64, #65):
//
//   * `POST /api/profiles/{id}/activate` goes through `_spawn_connect` and
//     resolves in ~40ms with {"started": "profile"} — the task has been
//     CREATED, the teardown has not even finished. The panel re-listed right
//     then, got the previous rig's active pointer back (the server moves it
//     inside connect_profile_id, only on success), and settled: the OLD
//     profile kept the accent ring, the ACTIVE chip and "auto-connects on
//     boot", while Activate re-armed. A second tap answered "'profile' is
//     already running".
//   * The handler did a `getProfile` round trip BEFORE raising any busy state
//     or opening any dialog — and for a simulator profile on a cold rig there
//     IS no dialog, so the whole request was dead time in which the card was
//     fully live.
//
// Both are claims about state over time, so this mounts the panel and drives
// it with a stub server whose active pointer moves when the connect lands, not
// when the POST returns.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- stub server
// ROWS is the controller's own answer to GET /api/profiles. The whole point is
// that `activePointerMoves()` is what changes it — never the POST.
type Row = { id: string; name: string; mode: string; devices_count: number; active: boolean; site_name: string | null };
let ROWS: Row[] = [
  { id: "a", name: "Deep sky rig", mode: "alpaca", devices_count: 4, active: true, site_name: null },
  { id: "b", name: "Planetary rig", mode: "empty", devices_count: 0, active: false, site_name: null },
];
const activePointerMoves = (id: string) => {
  ROWS = ROWS.map((r) => ({ ...r, active: r.id === id }));
};

/** Held so the test can observe the gap between the tap and the first
 *  response — the window in which the card used to be fully live. */
let releaseGet: () => void = () => {};
let gate: Promise<void> | null = null;
const activatePosts: string[] = [];

g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  const ok = (body: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => body } as any);
  if (path.includes("/activate") && method === "POST") {
    activatePosts.push(path);
    // `_spawn_connect` answers the instant the task exists. The rig is still
    // tearing down; the active pointer has NOT moved.
    return ok({ started: "profile" });
  }
  if (path.match(/\/api\/profiles\/[^/]+$/) && method === "GET") {
    if (gate) await gate;
    const id = path.split("/").pop()!;
    const row = ROWS.find((r) => r.id === id)!;
    return ok({
      id: row.id, name: row.name, primary_backend: "none", devices: [],
      nina_host: null, nina_port: 0, phd2_host: null, phd2_port: 0,
      optics: null, site_name: null, providers: null,
    });
  }
  if (path.includes("/api/profiles")) return ok(ROWS);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const ProfileList = (await import("../ProfileList")).default;
const { waitForProfileActive } = await import("../ProfileList");

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
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

useStore.setState({
  // A COLD rig: `profileActivateConfirm` returns no dialog for a profile with
  // no real motion when nothing is connected, which is exactly the case where
  // there was nothing at all between the tap and the network.
  status: { connected: {}, looping: false, mode: "none" },
  principal: { role: "admin", email: null, caps: ["view.status", "config.backend"] },
} as never);

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve(); }); };

await act(async () => { root.render(createElement(ProfileList)); });
await flush();

/** The card for a profile, located from its Rename button's accessible name —
 *  the one stable per-row hook that is not the text under test. */
function card(name: string): any {
  const rename = container.querySelector(`[aria-label="Rename ${name}"]`);
  if (!rename) throw new Error(`no card rendered for "${name}"`);
  const el = rename.parentElement.parentElement;
  if (!(el.textContent || "").includes(name)) throw new Error(`card lookup for "${name}" walked off the row`);
  return el;
}
const activateBtn = (name: string): any =>
  [...card(name).querySelectorAll("button")]
    .find((b: any) => /activate|reconnect|connecting/i.test(b.textContent || ""));
const isBadgedActive = (name: string): boolean =>
  [...card(name).querySelectorAll("span")].some((s: any) => (s.textContent || "").trim() === "Active");
const click = (node: any) =>
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });

test("the panel mounted with the server's two rows, A active", () => {
  // Anti-blank-page guard: every assertion below is about which row wears the
  // ACTIVE chip, and "no rows" would satisfy the negative ones for free.
  assert(activateBtn("Deep sky rig") != null, "no Activate/Reconnect control on the A row");
  assert(activateBtn("Planetary rig") != null, "no Activate/Reconnect control on the B row");
  assert(isBadgedActive("Deep sky rig"), "A is the server's active profile but carries no ACTIVE chip");
  assert(!isBadgedActive("Planetary rig"), "B is not active but is badged as if it were");
});

// ------------------------------------------------- #65: busy before the GET
await testAsync("the row goes busy before the first round trip, not after it", async () => {
  gate = new Promise<void>((r) => { releaseGet = r; });
  const b = activateBtn("Planetary rig");
  await click(b);
  await flush();
  // The GET is still in flight and no dialog exists for this profile.
  assert(activatePosts.length === 0,
    "the activate POST already went out — the fixture is not holding the pre-flight GET, " +
    "so this test is not measuring the gap it claims to");
  assert(activateBtn("Planetary rig").disabled === true,
    "Activate is still live while its pre-flight request is in the air — on touch that " +
    "gap is one double-tap wide, and the second tap is what the server answers with " +
    "\"'profile' is already running\"");
});

// -------------------------------------- #16/#64: the rig answers, not the POST
await testAsync("while the rig connects, the panel neither claims done nor moves the chip", async () => {
  releaseGet();
  gate = null;
  await flush();
  assert(activatePosts.length === 1, `expected exactly one activate POST, saw ${activatePosts.length}`);
  // The server's pointer moves only when the connect succeeds. Model that: the
  // POST has returned, the rig is still coming up.
  const b = activateBtn("Planetary rig");
  assert(/connecting/i.test(b.textContent || ""),
    `the button reads "${(b.textContent || "").trim()}" — the POST returning is not the rig ` +
    "connecting, and a re-armed Activate here is what earns the second tap");
  assert(b.disabled === true, "the button is pressable again while its own connect is still running");
  assert(isBadgedActive("Deep sky rig") && !isBadgedActive("Planetary rig"),
    "the panel moved the ACTIVE chip on the strength of the POST — the controller has " +
    "not switched profiles yet, and on a failed connect it never will");
});

await testAsync("when the connect lands, the chip and the boot line follow it", async () => {
  activePointerMoves("b");
  // One poll interval (1500ms) plus slack.
  await act(async () => { await sleep(1900); });
  await flush();
  assert(isBadgedActive("Planetary rig"),
    "the controller reports B active and the panel still does not — the re-list after " +
    "the POST was too early and nothing ever asked again");
  assert(!isBadgedActive("Deep sky rig"),
    "A is no longer the active profile but still wears the chip, and with it the claim " +
    "that it is the rig that comes back on boot");
  assert((card("Planetary rig").className || "").includes("border-accent"),
    "the accent ring did not move to the profile that is actually active");
  const b = activateBtn("Planetary rig");
  assert(!/connecting/i.test(b.textContent || "") && b.disabled === false,
    "the row is still held busy after the connect landed");
});

act(() => { root.unmount(); });

// ------------------------------------------------------- the waiter itself
await testAsync("waitForProfileActive returns null rather than hanging on a connect that never lands", async () => {
  // The failure mode a bare `setBusy(true)` has when its `finally` is missed:
  // a control disabled forever. The budget is what stops that here.
  const t0 = Date.now();
  const seen: number[] = [];
  ROWS = ROWS.map((r) => ({ ...r, active: false }));
  const out = await waitForProfileActive("b", (rows) => seen.push(rows.length), 260, 100);
  assert(out === null, "a pointer that never moves must expire, not resolve as success");
  assert(seen.length >= 2, `expected repeated re-lists while waiting, saw ${seen.length}`);
  assert(Date.now() - t0 < 2000, "the budget did not bound the wait");
});

console.log(`profileActivate: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
