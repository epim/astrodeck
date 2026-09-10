// rigProfilesDom.test.tsx - the PROFILES sheet, MOUNTED (plan hub-rig.md A.3
// and wave-r7.md 3.F20 / T-R7-17).
//
//   Run directly:  npx tsx src/next/hubs/rig/profiles/__tests__/rigProfilesDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// WHAT IS WORTH ASSERTING HERE, now that the sheet no longer mounts
// `components/settings/ProfileList.tsx` but a rebuilt editor of its own:
//
//   THE CONFIRMS ARE STILL THE ONES THAT WERE ARGUED OVER. Two of the five
//   actions on a card can destroy something a user cannot get back, and both of
//   them had their copy and their friction level moved into tested pure modules
//   precisely because the JSX kept getting them wrong: `profileActivateConfirm`
//   asks about the rig the activate DROPS (every activate tears the current rig
//   down first, so a simulator profile with no dialog once destroyed a live
//   11-device rig), and `profileDeleteConfirm` escalates the ACTIVE profile to
//   a hold and says the boot consequence out loud. A rebuild that quietly
//   dropped either is the exact defect this wave exists to prevent, so both are
//   driven for real: the dialog must appear, and a cancel must fire nothing.
//
//   ACTIVATE IS NOT DONE WHEN THE POST RESOLVES. The route `_spawn_connect`s
//   and answers `{started}` in ~40 ms with the teardown not yet finished, so a
//   re-list at that moment reads the PREVIOUS rig's active pointer. The fixture
//   below refuses to flip its active pointer for the first second after the
//   POST, which is what makes "did it poll" observable rather than asserted by
//   inspection: an implementation that re-listed immediately reports the old
//   profile as active and warns.
//
//   A FAILED LOAD IS NOT AN EMPTY LIBRARY. The two states are one hint apart
//   and only one of them means there is nothing there. `Couldn't load profiles`
//   must render with a RETRY that asks again, and `No profiles yet` must not
//   appear at all while the load is broken.
//
//   ONE SENTENCE FOR A PRINCIPAL WHO CANNOT WRITE BACKEND CONFIG, on a screen
//   where every control is a `config.backend` write. Not one per control, not
//   two different wordings, and never the native `disabled` attribute - which
//   would take the reason out of the accessibility tree along with the control.
//
//   THE COPY OF `waitForProfileActive` CANNOT DRIFT. The helper lives inside
//   the 929-line legacy panel, so `next` owns its own copy (section 2.1's
//   finding). Both are imported here and driven against the same server.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The rebuilt editor is an AREA ROOT and imports its own `profiles.css` (wave
// R7's rule: `next.css` belongs to one task, every other area ships its own
// stylesheet). Node has no idea what a `.css` file is, so a synchronous load
// hook answers with an empty module - the same stub `shellDom.test.tsx` uses
// for `NextApp`.
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
  { url: "http://local/#/rig/devices/profiles", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
// jsdom has no object URLs. EXPORT builds one, hands it to an <a download> and
// revokes it; without these the test would fail on the plumbing rather than on
// anything the sheet decides.
win.URL.createObjectURL = () => "blob:profile";
win.URL.revokeObjectURL = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "FocusEvent", "PointerEvent", "Blob", "File",
  "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- the fixture
//
// A tiny profile library that behaves like the real one in the three ways this
// sheet depends on: the list is the authority on which profile is active, the
// active pointer moves LATE (see the header), and `GET /api/profiles/{id}`
// answers with a full record.
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];

const ROW_A = {
  id: "p-a", name: "Backyard SCT", mode: "alpaca", devices_count: 4,
  site_name: "Home", active: true, providers: null, optics: null,
};
const ROW_B = {
  id: "p-b", name: "Dark site refractor", mode: "mixed", devices_count: 6,
  site_name: null, active: false,
  providers: { polar_align: "sim" }, optics: null,
};

let serverRows: any[] = [];
/** Set to a status code to fail the next `GET /api/profiles`. */
let listFails = 0;
/** `{status, body}` for the next activate POST, or null for success. */
let activatePosts: ({ status: number; body: any } | null)[] = [];
/** The id whose active pointer the server has agreed to move, and the earliest
 *  moment it will admit to it. A list before that reads the OLD rig, which is
 *  exactly what `_spawn_connect` does. */
let pendingActive: { id: string; notBefore: number } | null = null;

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

function fullOf(id: string): any {
  const row = serverRows.find((r) => r.id === id);
  return {
    id, name: row?.name ?? id, devices: [{ role: "camera", backend: "sim" }],
    primary_backend: "sim", nina_host: null, site_name: row?.site_name ?? null,
    optics: null, providers: row?.providers ?? null,
  };
}

const ok = (json: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
const fail = (status: number, body: any) => ({
  ok: false, status, statusText: `HTTP ${status}`, json: async () => body,
});

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: u, body });

  if (method === "GET" && /\/api\/profiles$/.test(u)) {
    if (listFails) { listFails -= 1; return fail(500, { detail: "the library is unreadable" }); }
    if (pendingActive && Date.now() >= pendingActive.notBefore) {
      for (const r of serverRows) r.active = r.id === pendingActive.id;
      pendingActive = null;
    }
    return ok(clone(serverRows));
  }
  const activate = /\/api\/profiles\/([^/]+)\/activate$/.exec(u);
  if (method === "POST" && activate) {
    const next = activatePosts.shift() ?? null;
    if (next) return fail(next.status, next.body);
    // The pointer moves only after a second - a re-list now still reads the
    // previous rig, which is what the real route does.
    pendingActive = { id: decodeURIComponent(activate[1]), notBefore: Date.now() + 1000 };
    return ok({ started: "profile" });
  }
  if (method === "POST" && /\/api\/profiles\/capture$/.test(u)) {
    const row = {
      id: `p-${serverRows.length + 1}`, name: body?.name ?? "?", mode: "alpaca",
      devices_count: 2, site_name: null, active: false, providers: null, optics: null,
    };
    serverRows.push(row);
    return ok(clone(row));
  }
  if (method === "POST" && /\/api\/profiles$/.test(u)) return ok(clone(serverRows[0]));
  const one = /\/api\/profiles\/([^/]+)$/.exec(u);
  if (one) {
    const id = decodeURIComponent(one[1]);
    if (method === "GET") return ok(fullOf(id));
    if (method === "PATCH") {
      const row = serverRows.find((r) => r.id === id);
      if (row) row.name = body?.name ?? row.name;
      return ok(clone(row));
    }
    if (method === "DELETE") {
      serverRows = serverRows.filter((r) => r.id !== id);
      return ok({ deleted: id });
    }
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { ProfilesSheet } = await import("../../sheets/profiles");
const model = await import("../profilesModel");
const mine = await import("../profileActive");
const legacy = await import("../../../../../components/settings/ProfileList");

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
const settle = async (rounds = 3) => {
  for (let i = 0; i < rounds; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
/** Drive real time forward inside `act` until `cond` holds. The activate poll
 *  is a real 1.5 s wall-clock loop by design (see `profileActive.ts`), so this
 *  is a wait, not a fake-timer trick. */
async function waitFor(cond: () => boolean, ms: number, what: string): Promise<void> {
  const until = Date.now() + ms;
  while (Date.now() < until) {
    if (cond()) return;
    await act(async () => { await new Promise((r) => setTimeout(r, 60)); });
  }
  if (!cond()) throw new Error(`timed out waiting for ${what}`);
}

// ------------------------------------------------------------------ fixtures
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather", "control.capture", "control.mount",
    "control.guide", "control.power", "config.safety", "config.solar_override",
    "config.backend", "config.site_optics", "config.alerts", "admin.users",
    "system.update"],
};
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};

function seed(principal: unknown = ADMIN, rows: any[] = [clone(ROW_A), clone(ROW_B)]): void {
  serverRows = rows;
  listFails = 0;
  activatePosts = [];
  pendingActive = null;
  asked.length = 0;
  act(() => {
    useStore.setState({
      principal,
      // Rule 1: a confirm dialog only reaches a shell that is actually on
      // screen, so every `confirmDialog()` here resolves false without this.
      authGate: "open",
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      confirm: null,
      sequence: { state: "idle" },
      status: {
        connected: { camera: { name: "sim", connected: true } },
        backend_links: [{ role: "camera", connected: true, error: null }],
        busy_lanes: [],
      },
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(ProfilesSheet as any, {})); });
}
function click(node: any): void {
  assert(node != null, "click on a control that is not rendered");
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
function type(node: any, value: string): void {
  act(() => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!
      .call(node, value);
    node.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
}
function enter(node: any): void {
  act(() => {
    node.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => [...container.querySelectorAll(sel)] as any[];
const text = () => (container.textContent || "") as string;
const id = (marker: string) => q(`[data-testid="${marker}"]`);
const cards = () => qa('[data-testid="profile-card"]');
/** The controls inside the card for `profileId`. */
function card(profileId: string): any {
  const head = q(`[data-profile-id="${profileId}"]`);
  return head?.closest('[data-testid="profile-card"]');
}
function within(root: any, marker: string): any {
  return root?.querySelector(`[data-testid="${marker}"]`);
}
const commands = () => asked.filter((a) => a.method !== "GET");
const toasts = () => (useStore.getState().toasts ?? []) as any[];
const toastText = () => toasts().map((t) => `${t.title ?? ""} ${t.body ?? ""}`).join(" | ");
const confirmReq = () => (useStore.getState() as any).confirm;
function answer(okAnswer: boolean): void {
  act(() => { useStore.getState().resolveConfirm(okAnswer); });
}

// =========================================================== 1. precondition
seed();
mount();
await settle();

test("the sheet mounted the rebuilt editor, not an empty wrapper", () => {
  assert(id("sheet-profiles") != null, "no sheet marker - the sheet did not render at all");
  assert(id("rig-profiles") != null, "no editor marker - the sheet rendered chrome and no library");
  eq(cards().length, 2, "the two saved profiles did not render as cards");
  for (const marker of ["profiles-import", "profiles-refresh", "profiles-list",
    "profiles-save-card", "profile-save-input", "profile-save-current"]) {
    assert(id(marker) != null, `no ${marker} control`);
  }
  const a = card("p-a");
  for (const marker of ["profile-activate", "profile-rename", "profile-update",
    "profile-export", "profile-delete"]) {
    assert(within(a, marker) != null, `no ${marker} control on the active profile's card`);
  }
  // The card must say what it IS, not only what can be done to it.
  assert(within(a, "profile-active") != null, "the active profile wears no ACTIVE badge");
  assert(/auto-connects on boot/.test(a.textContent || ""),
    "the active profile does not say it is what boots next time");
  assert(/4 devices/.test(a.textContent || ""),
    `the card does not say how many devices it stores: ${JSON.stringify(a.textContent)}`);
  // #129: a profile that pins a provider must SAY the value, not badge it.
  const b = card("p-b");
  assert(within(b, "profile-overrides") != null,
    "a profile pinning a provider shows no OVERRIDES block - the defect #129 exists for");
  assert(/these take over when you activate it/.test(b.textContent || ""),
    "the OVERRIDES block does not say when the pins take effect");
  assert(!/—/.test(text()), "the sheet still carries a legacy em-dash");
});

test("the sheet's live line reads the same rows the list does", () => {
  const live = q(".nx-sheet-live");
  assert(live != null, "the sheet has no live line");
  assert(/2 saved/.test(live.textContent || ""),
    `the live line does not count the library: ${JSON.stringify(live.textContent)}`);
  assert(/Backyard SCT active/.test(live.textContent || ""),
    "the live line does not name the active profile");
});

// ============================================================== 2. activate
await testAsync("ACTIVATE asks profileActivateConfirm first, and a cancel fires nothing", async () => {
  seed();
  mount();
  await settle();
  asked.length = 0;
  click(within(card("p-b"), "profile-activate"));
  await settle();
  const req = confirmReq();
  assert(req != null, "ACTIVATE fired with no confirm - the teardown happens with no warning");
  eq(req.title, `Activate "Dark site refractor"?`, "the dialog is not profileActivateConfirm's");
  assert(/tears the current rig down first/.test(req.body || ""),
    `the dialog does not say what the activate DROPS: ${JSON.stringify(req.body)}`);
  answer(false);
  await settle();
  eq(commands().length, 0, "a cancelled activate still POSTed");
});

await testAsync("ACTIVATE holds the row until the RIG answers, not until the POST does", async () => {
  seed();
  mount();
  await settle();
  asked.length = 0;
  click(within(card("p-b"), "profile-activate"));
  await settle();
  answer(true);
  await settle();

  const posts = asked.filter((a) => a.method === "POST" && /\/activate$/.test(a.url));
  eq(posts.length, 1, "the confirmed activate did not POST /api/profiles/{id}/activate");
  assert(/p-b/.test(posts[0].url), "the activate went to the wrong profile");
  eq(posts[0].body.force, false, "the first activate should not be forced");

  // The POST has resolved. The server has NOT moved its pointer yet, so an
  // implementation that re-listed here would already be claiming success.
  assert(/connecting the rig/.test(toastText()),
    `no "connecting" toast - the row claims nothing while the rig swaps: ${toastText()}`);
  assert(!/is active/.test(toastText()),
    "the sheet reported success while the server still names the OLD profile as active");
  const connecting = within(card("p-b"), "profile-activate");
  assert(/CONNECTING/.test(connecting.textContent || ""),
    "the button does not say the connect is still running");
  // Every OTHER row's ACTIVATE is held, because the controller does one at a time.
  const other = within(card("p-a"), "profile-activate");
  eq(other.getAttribute("aria-disabled"), "true",
    "a second profile can be activated while one connect is in flight");
  assert(/one at a time/.test(other.getAttribute("title") || ""),
    `the held button does not say why: ${JSON.stringify(other.getAttribute("title"))}`);

  await waitFor(() => /is active/.test(toastText()), 8000, "the activate to land");
  assert(/and is what boots next time/.test(toastText()),
    "the success toast does not say the profile is what boots next time");
  const listsAfterPost = asked
    .slice(asked.indexOf(posts[0]))
    .filter((a) => a.method === "GET" && /\/api\/profiles$/.test(a.url));
  assert(listsAfterPost.length >= 1,
    "nothing re-listed after the POST - the active pointer was never checked");
  assert(within(card("p-b"), "profile-active") != null,
    "the ACTIVE badge did not move to the profile that was activated");
});

await testAsync("a coded 409 offers the force retry once, and the retry is forced", async () => {
  seed();
  mount();
  await settle();
  activatePosts = [{ status: 409, body: { detail: { detail: "a sequence is running", code: "running" } } }];
  asked.length = 0;
  click(within(card("p-b"), "profile-activate"));
  await settle();
  answer(true);                    // the activate confirm
  await settle();
  const busy = confirmReq();
  assert(busy != null, "the coded 409 offered no way forward at all");
  eq(busy.title, "Rig is busy", "the 409 dialog is not the force-activate one");
  answer(true);                    // force it
  await settle();
  const posts = asked.filter((a) => a.method === "POST" && /\/activate$/.test(a.url));
  eq(posts.length, 2, "the force retry did not re-POST");
  eq(posts[1].body.force, true, "the retry was not forced, so it hits the same guard again");
  await waitFor(() => /is active/.test(toastText()), 8000, "the forced activate to land");
});

// ================================================================ 3. rename
await testAsync("RENAME commits inline and PATCHes the new name", async () => {
  seed();
  mount();
  await settle();
  asked.length = 0;
  click(within(card("p-b"), "profile-rename"));
  await settle();
  const input = within(card("p-b"), "profile-rename-input");
  assert(input != null, "RENAME opened no field - the name is not editable in the card");
  eq(input.getAttribute("aria-label"), "Profile name", "the rename field is not named for a reader");
  eq(input.value, "Dark site refractor", "the field did not start from the current name");
  type(input, "Ridgeline APO");
  enter(input);
  await settle();
  const patches = asked.filter((a) => a.method === "PATCH");
  eq(patches.length, 1, "Enter did not commit the rename");
  assert(/p-b/.test(patches[0].url), "the rename went to the wrong profile");
  eq(patches[0].body.name, "Ridgeline APO", "the rename sent the wrong name");
  assert(/Ridgeline APO/.test(text()), "the list still shows the old name after a successful rename");
  assert(within(card("p-b"), "profile-rename-input") == null,
    "the field stayed open after committing");
});

await testAsync("a rename that changes nothing writes nothing", async () => {
  seed();
  mount();
  await settle();
  asked.length = 0;
  click(within(card("p-b"), "profile-rename"));
  await settle();
  const input = within(card("p-b"), "profile-rename-input");
  type(input, "   ");
  enter(input);
  await settle();
  eq(asked.filter((a) => a.method === "PATCH").length, 0,
    "an empty name was sent to the server, which would blank the profile's name");
});

// ================================================================ 4. delete
await testAsync("DELETE needs the confirm, and a cancel deletes nothing", async () => {
  seed();
  mount();
  await settle();
  asked.length = 0;
  click(within(card("p-b"), "profile-delete"));
  await settle();
  const req = confirmReq();
  assert(req != null, "DELETE fired with no confirm - the one irreversible action had no friction");
  eq(req.title, `Delete "Dark site refractor"?`, "the dialog is not profileDeleteConfirm's");
  assert(/there is no undo/.test(req.body || ""), "the dialog does not say the delete is permanent");
  assert(/does not disconnect the rig you are running now/.test(req.body || ""),
    "the dialog does not say what is NOT lost, which is the question a tired user asks");
  eq(asked.filter((a) => a.method === "DELETE").length, 0,
    "the row was deleted before the dialog was answered");
  answer(false);
  await settle();
  eq(asked.filter((a) => a.method === "DELETE").length, 0, "a cancelled delete still deleted");
  eq(cards().length, 2, "the cancelled row disappeared from the list");
});

await testAsync("deleting the ACTIVE profile escalates to a hold and says the boot cost", async () => {
  seed();
  mount();
  await settle();
  click(within(card("p-a"), "profile-delete"));
  await settle();
  const req = confirmReq();
  assert(req != null, "no confirm for the active profile");
  eq(req.mode, "hold", "deleting the ACTIVE profile took the ordinary tap-confirm");
  assert(/nothing will auto-connect when AstroDeck restarts/.test(req.body || ""),
    "the dialog does not say the rig stops auto-connecting on boot");
  answer(true);
  await settle();
  const dels = asked.filter((a) => a.method === "DELETE");
  eq(dels.length, 1, "the confirmed delete did not DELETE /api/profiles/{id}");
  assert(/p-a/.test(dels[0].url), "the delete went to the wrong profile");
  assert(/the profile only; the rig is untouched/.test(toastText()),
    `the success toast does not say what survived: ${toastText()}`);
  eq(cards().length, 1, "the list was not re-read after the delete");
});

// =========================================== 5. a principal who cannot write
await testAsync("an operator sees the whole sheet read-only, with ONE sentence, and fires nothing", async () => {
  seed(OPERATOR);
  mount();
  await settle();
  asked.length = 0;

  eq(qa(".nx-locknote").length, 1,
    "the sheet says the read-only reason more than once, or not at all");
  const note = id("profiles-locknote");
  eq(note.textContent, "Read-only - needs admin access",
    `the one sentence does not name the policy the server enforces: ${JSON.stringify(note.textContent)}`);

  const a = card("p-a");
  const controls = [
    id("profiles-import"), id("profile-save-current"),
    within(a, "profile-activate"), within(a, "profile-rename"),
    within(a, "profile-update"), within(a, "profile-export"),
    within(a, "profile-delete"),
  ];
  for (const c of controls) {
    assert(c != null, "a control is missing entirely - a viewer must see the same screen");
    eq(c.getAttribute("aria-disabled"), "true", `${c.getAttribute("data-testid")} is not honest-disabled`);
  }
  eq(qa("[disabled]").length, 0,
    "a native `disabled` attribute is on the page, which removes the reason from the a11y tree");
  // The save field is readOnly rather than disabled, so the name stays readable.
  eq(id("profile-save-input").readOnly, true, "the capture name field is writable for a non-admin");

  for (const c of controls) click(c);
  await settle();
  eq(commands().length, 0, "a locked control still wrote to the rig");
  assert(confirmReq() == null, "a locked control opened a confirm dialog");
  // Seven presses, one reason: the store coalesces identical toasts onto a
  // single card with an x N chip, so the count is the evidence that each press
  // answered rather than the number of cards.
  eq(toasts().length, 1,
    "a locked press was silent - the user cannot tell a blocked control from a broken one");
  eq(toasts()[0].count, controls.length,
    "not every locked press said something");
  assert(toasts().every((t) => t.level === "warning" && /needs admin access/.test(t.title || "")),
    `a locked press did not say the reason: ${toastText()}`);
});

// ============================================ 6. the load error and its RETRY
await testAsync("Couldn't load profiles renders with a RETRY that refetches, never an empty list", async () => {
  seed();
  listFails = 1;
  mount();
  await settle();

  assert(id("profiles-error") != null, "a failed load renders no error state at all");
  assert(new RegExp(model.LOAD_FAILED).test(text()),
    `the failure is not named: ${JSON.stringify(text().slice(0, 200))}`);
  assert(/the library is unreadable/.test(text()),
    "the server's own reason was dropped, so the user cannot tell a 500 from a dead link");
  assert(id("profiles-empty") == null,
    "a failed load rendered as 'No profiles yet' - a broken library and an empty one are not the same thing");
  eq(cards().length, 0, "cards rendered from a load that failed");

  const before = asked.filter((a) => a.method === "GET" && /\/api\/profiles$/.test(a.url)).length;
  click(id("profiles-retry"));
  await settle();
  const after = asked.filter((a) => a.method === "GET" && /\/api\/profiles$/.test(a.url)).length;
  assert(after > before, "RETRY asked nothing - the button is a promise nothing keeps");
  assert(id("profiles-error") == null, "the error survived a successful retry");
  eq(cards().length, 2, "the retry succeeded and the list still did not render");
});

await testAsync("an empty library is 'No profiles yet', with what would fill it", async () => {
  seed(ADMIN, []);
  mount();
  await settle();
  assert(id("profiles-empty") != null, "an empty library renders no empty state");
  assert(new RegExp(model.EMPTY_TITLE).test(text()), "the empty state is not named");
  assert(/save it below/.test(text()),
    "the empty state does not say what would fill it");
  assert(id("profiles-error") == null, "an empty library rendered as a failure");
});

// ============================================== 7. save the current rig
await testAsync("SAVE RIG captures under the typed name and re-lists", async () => {
  seed(ADMIN, []);
  mount();
  await settle();
  asked.length = 0;
  type(id("profile-save-input"), "Ridgeline");
  click(id("profile-save-current"));
  await settle();
  const posts = asked.filter((a) => a.method === "POST" && /\/capture$/.test(a.url));
  eq(posts.length, 1, "SAVE RIG did not POST /api/profiles/capture");
  eq(posts[0].body.name, "Ridgeline", "the capture was filed under the wrong name");
  eq(cards().length, 1, "the list was not re-read after the capture");
});

// ================================================= 8. no drift in the copy
await testAsync("no drift: next's waitForProfileActive agrees with the legacy original", async () => {
  // Same server, same budget, same interval: both must poll, both must hand the
  // caller every set of rows they see, and both must resolve with the rows the
  // moment the pointer moves.
  const run = async (fn: typeof mine.waitForProfileActive) => {
    serverRows = [clone(ROW_A), clone(ROW_B)];
    pendingActive = { id: "p-b", notBefore: Date.now() + 120 };
    const seen: number[] = [];
    const out = await fn("p-b", (rows) => seen.push(rows.length), 900, 100);
    return { seen, active: out?.find((r) => r.active)?.id ?? null };
  };
  const a = await run(mine.waitForProfileActive);
  const b = await run(legacy.waitForProfileActive);
  eq(a.active, "p-b", "next's copy did not resolve with the rows once the pointer moved");
  eq(a.active, b.active, "the two copies disagree about which profile landed");
  eq(a.seen.length, b.seen.length, "the two copies polled a different number of times");

  // And the lapsed-budget answer, which is the one a failed connect takes.
  serverRows = [clone(ROW_A)];
  pendingActive = null;
  eq(await mine.waitForProfileActive("p-b", () => {}, 260, 100), null,
    "next's copy hangs instead of answering null when the budget lapses");
  eq(await legacy.waitForProfileActive("p-b", () => {}, 260, 100), null,
    "the legacy copy no longer answers null - the fixture is wrong, not the copy");
});

test("no drift: the two bodies are the same code", () => {
  // Comments and whitespace normalised away, because the copy carries its own
  // header and the legacy one carries an em-dash this repo no longer writes.
  const norm = (fn: unknown): string =>
    String(fn)
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/\/\/[^\n]*/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  eq(norm(mine.waitForProfileActive), norm(legacy.waitForProfileActive),
    "the copy and the legacy original have drifted apart");
});

// ============= 9. and nothing under next/ reaches back into the legacy panel
await testAsync("no next-side module imports components/settings/ProfileList", async () => {
  // The whole point of the copy above is that `ui/src/next/**` never pulls
  // `ProfileList.tsx` in - it is a 929-line presentation module, and importing
  // it for one control-flow helper drags `Panel`, `HoldButton`, `LockedChip`,
  // `Icon` and the Tailwind tree behind them into the lazily split next bundle
  // (wave R7 section 2.1, ruling 6). `devices/rigConnect.ts` was the last
  // importer; T-R7-21a item 20 switched it. This is what stops the next one.
  interface NodeFsLike {
    readdirSync(p: string, o: { withFileTypes: true }): { name: string; isDirectory(): boolean }[];
    readFileSync(p: string, enc: string): string;
  }
  const nodeImport = (m: string): Promise<unknown> =>
    (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(m);
  const fs = (await nodeImport("node:fs")) as NodeFsLike;
  const pathMod = (await nodeImport("node:path")) as { join(...p: string[]): string };
  const urlMod = (await nodeImport("node:url")) as { fileURLToPath(u: URL): string };
  const nextRoot = urlMod.fileURLToPath(new URL("../../../..", import.meta.url));

  const offenders: string[] = [];
  let scanned = 0;
  const walk = (dir: string): void => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = pathMod.join(dir, e.name);
      if (e.isDirectory()) { walk(full); continue; }
      if (!/\.tsx?$/.test(e.name)) continue;
      scanned += 1;
      const src = fs.readFileSync(full, "utf8");
      if (/from "[^"]*components\/settings\/ProfileList"/.test(src)) {
        offenders.push(full.slice(nextRoot.length).replace(/\\/g, "/"));
      }
    }
  };
  walk(nextRoot);

  assert(scanned > 200,
    `only ${scanned} files scanned under next/ - the walk is broken, so an empty `
    + "offender list would mean nothing");
  eq(offenders.join(", "), "",
    "these next-side modules import the legacy ProfileList panel, which puts its whole "
    + "render tree back in the next bundle. Import next/hubs/rig/profiles/profileActive "
    + "instead:");
});

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`rigProfiles.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
