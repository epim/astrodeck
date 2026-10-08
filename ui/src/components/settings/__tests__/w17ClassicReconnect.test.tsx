// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w17ClassicReconnect.test.tsx - the CLASSIC root's Settings -> Profiles panel
// and the not-connected interstitial, for an operator who may reconnect the rig
// (#759 for the rig; #839 for this root, which is still the production root).
//
//   Run directly:  npx tsx src/components/settings/__tests__/w17ClassicReconnect.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// #759 (owner ruling 2026-10-07: "permitted for Admin and operator roles") made
// an UNFORCED profile activate `control.reconnect`; the #/next surfaces were
// re-gated by wave 17's WP-146 and the classic ones were not, so an operator who
// could bring the rig back by API was locked out of the button on the screen
// that is the root. WHAT IS WORTH ASSERTING, on the mounted panel:
//
//   AN OPERATOR HAS ONE LIVE CONTROL AND A ROW OF STATED LOCKS. Activate is a
//   real, pressable button; Rename, Update, Export, Delete, the header Import and
//   Save Rig are `aria-disabled` chips that name `config.backend`'s role phrase
//   (admin access). Opening the panel to the operator must not open the writes.
//   A VIEWER HAS NONE: the Activate chip names operator or admin access, and a
//   press sends nothing.
//   AN ADMIN SEES THE PANEL AS BEFORE: no chip anywhere.
//   AN OPERATOR IS NEVER OFFERED THE FORCE. A running conflict is a toast naming
//   who can force (admin access), never the force dialog whose yes the rig
//   answers 403; the activate goes out once, unforced.
//   THE INTERSTITIAL TELLS EACH ROLE THE TRUTH: an operator is offered the way to
//   the Rig page (Activate is there), a viewer is told whom to ask, by the role
//   table's own phrase.
//
// MUTANTS RUN (each from a byte backup, restored byte-identically with sha256
// compared, the mutant text grepped out). The failing assertion each produced,
// verbatim, is in the comment above the case that pins it.

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
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element", "Node",
  "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "FocusEvent", "PointerEvent",
  "Blob", "File", "localStorage", "requestAnimationFrame",
  "cancelAnimationFrame", "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- stub server
type Row = { id: string; name: string; mode: string; devices_count: number; active: boolean; site_name: string | null };
const ROWS: Row[] = [
  { id: "a", name: "Deep sky rig", mode: "alpaca", devices_count: 4, active: true, site_name: null },
  { id: "b", name: "Planetary rig", mode: "empty", devices_count: 0, active: false, site_name: null },
];
let rows: Row[] = [];
/** What each activate POST sent, and what it should answer. */
const activates: { url: string; body: any }[] = [];
let activateAnswer: { status: number; body: any } | null = null;
const commands: { method: string; url: string }[] = [];

const ok = (json: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => json } as any);
const fail = (status: number, body: unknown) =>
  ({ ok: false, status, statusText: `HTTP ${status}`, json: async () => body } as any);

g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  if (method !== "GET") commands.push({ method, url: path });
  if (path.includes("/activate") && method === "POST") {
    let body: any = null;
    try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
    activates.push({ url: path, body });
    if (activateAnswer) return fail(activateAnswer.status, activateAnswer.body);
    // The pointer moves with the connect, which here lands immediately.
    rows = rows.map((r) => ({ ...r, active: path.includes(`/profiles/${r.id}/`) }));
    return ok({ started: "profile" });
  }
  if (path.match(/\/api\/profiles\/[^/]+$/) && method === "GET") {
    // WHAT AN OPERATOR IS SERVED: the REDACTED record. No nina_host, host, port
    // or extra; the judgements the dialog needs are still in it.
    const id = path.split("/").pop()!;
    const row = rows.find((r) => r.id === id)!;
    return ok({
      id: row.id, name: row.name, primary_backend: "none", devices: [],
      nina_port: 0, phd2_port: 0, optics: null, providers: null,
    });
  }
  if (path.includes("/api/profiles")) return ok(rows);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const ProfileList = (await import("../ProfileList")).default;
const { activateFailureOutcome } = await import("../ProfileList");
const { NotConnectedInterstitial } = await import("../../NotConnectedInterstitial");
const { ApiError } = await import("../../../api");
const { forceNeedsBackend, forceNeedsLan } = await import("../../../next/hubs/rig/profiles/profilesModel");

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
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const ADMIN = {
  role: "admin", email: null,
  caps: ["view.status", "control.reconnect", "config.backend"],
};
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "control.capture", "control.guide",
    "control.mount", "control.reconnect"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;
const flush = async () => { for (let i = 0; i < 6; i++) await act(async () => { await Promise.resolve(); }); };

async function mountAs(principal: unknown): Promise<void> {
  rows = ROWS.map((r) => ({ ...r }));
  activates.length = 0;
  commands.length = 0;
  activateAnswer = null;
  if (root) await act(async () => { root!.unmount(); });
  root = createRoot(container);
  useStore.setState({
    principal, authGate: "open", toasts: [], confirm: null,
    // A COLD rig and no sequence: an activate of the empty profile asks nothing,
    // so the press reaches the network without a dialog in between.
    status: { connected: {}, looping: false, mode: "none" },
    sequence: { state: "idle" },
  } as never);
  await act(async () => { root!.render(createElement(ProfileList)); });
  await flush();
}

const cards = () => [...container.querySelectorAll('[data-testid="profile-card"]')] as any[];
const cardOf = (name: string): any => {
  const c = cards().find((el) => (el.textContent || "").includes(name));
  if (!c) throw new Error(`no card rendered for "${name}"`);
  return c;
};
/** The control (button or locked chip) in `scope` whose visible word is `word`. */
function control(scope: any, word: RegExp): any {
  const hit = [...scope.querySelectorAll('button, [role="button"]')].find(
    (el: any) => word.test((el.textContent || "").trim()) || word.test(el.getAttribute("aria-label") || ""),
  );
  return hit ?? null;
}
const isLockedChip = (el: any): boolean =>
  !!el && el.getAttribute("role") === "button" && el.getAttribute("aria-disabled") === "true"
  && /^Unavailable/.test(el.getAttribute("aria-label") || "");
const isLiveButton = (el: any): boolean =>
  !!el && el.tagName === "BUTTON" && !el.hasAttribute("disabled")
  && el.getAttribute("aria-disabled") !== "true";
const reasonOf = (el: any): string => (el?.getAttribute("aria-label") || "").replace(/^Unavailable — /, "");
const click = (node: any) =>
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
const toastText = () => ((useStore.getState().toasts ?? []) as any[]).map((t) => String(t.title ?? "")).join(" | ");
const confirmReq = () => (useStore.getState() as any).confirm;

const EDIT_REASON = "Changing profiles needs admin access.";
const DELETE_REASON = "Deleting a profile needs admin access.";

// ================================================================ the admin
await mountAs(ADMIN);

test("the admin's panel mounted with the two rows, and carries no lock at all", () => {
  eq(cards().length, 2, "the two saved profiles did not render");
  eq(container.querySelectorAll('[aria-label^="Unavailable"]').length, 0,
    "an admin was shown a stated lock - the panel changed for the role that could always use it");
  for (const name of ["Deep sky rig", "Planetary rig"]) {
    const c = cardOf(name);
    assert(isLiveButton(control(c, /Reconnect|Activate/)), `${name}: Activate is not a live button`);
    assert(isLiveButton(control(c, new RegExp(`^Rename ${name}$`))), `${name}: Rename is not a live button`);
    assert(isLiveButton(control(c, new RegExp(`^Export ${name}$`))), `${name}: Export is not a live button`);
    assert(isLiveButton(control(c, new RegExp(`^Delete ${name}$`))), `${name}: Delete is not a live button`);
  }
  assert(isLiveButton(control(container, /^Import$/)), "Import is not a live button for an admin");
  assert(isLiveButton(control(container, /^Save Rig$/)), "Save Rig is not a live button for an admin");
  assert(container.querySelector('[data-testid="profiles-locknote"]') == null,
    "an admin was shown the operator's note");
});

// ============================================================== the operator
await mountAs(OPERATOR);

// MUTANT "EDIT_RIDES_RECONNECT" (ProfileList.tsx: `const editLock = canConfig ?
// null : ...` made `canReconnect ? null : ...`). Run from a byte backup in this
// worktree, restored byte-identically (sha256 compared): "w17ClassicReconnect:
// 10/11 passed" with this case red: "x an operator has ONE live control -
// Activate - and a stated lock on every write: Deep sky rig: Rename is not a
// stated lock - opening the panel to the operator opened the writes".
// MUTANT "UPDATE_LEFT_LIVE" (the `{editLock ? (<LockedChip ...>Update` branch
// made `{false ? (`): 10/11, red on the same case: "x an operator has ONE live
// control - Activate - and a stated lock on every write: Deep sky rig: Update is
// not a stated lock - opening the panel to the operator opened the writes".
test("an operator has ONE live control - Activate - and a stated lock on every write", () => {
  eq(cards().length, 2, "the operator was not shown the panel");
  for (const name of ["Deep sky rig", "Planetary rig"]) {
    const c = cardOf(name);
    const activate = control(c, /Reconnect|Activate/);
    assert(isLiveButton(activate), `${name}: Activate is not a live button for an operator`);
    const rename = control(c, /Rename/);
    assert(isLockedChip(rename), `${name}: Rename is not a stated lock - opening the panel to the operator opened the writes`);
    eq(reasonOf(rename), EDIT_REASON, `${name}: Rename's reason`);
    const update = control(c, /Update/);
    assert(isLockedChip(update), `${name}: Update is not a stated lock - opening the panel to the operator opened the writes`);
    eq(reasonOf(update), EDIT_REASON, `${name}: Update's reason`);
    const exp = control(c, /Export/);
    assert(isLockedChip(exp), `${name}: Export is not a stated lock - it would serve the redacted record as a backup`);
    eq(reasonOf(exp), EDIT_REASON, `${name}: Export's reason`);
    const del = control(c, /Delete/);
    assert(isLockedChip(del), `${name}: Delete is not a stated lock`);
    eq(reasonOf(del), DELETE_REASON, `${name}: Delete's reason`);
  }
  const imp = control(container, /Import/);
  assert(isLockedChip(imp), "Import is not a stated lock");
  eq(reasonOf(imp), EDIT_REASON, "Import's reason");
  const save = control(container, /Save Rig/);
  assert(isLockedChip(save), "Save Rig is not a stated lock");
  eq(reasonOf(save), EDIT_REASON, "Save Rig's reason");
  const note = container.querySelector('[data-testid="profiles-locknote"]');
  assert(note != null, "the half-open panel says nothing about what the operator can and cannot do");
  assert(/activate a saved profile/.test(note.textContent) && note.textContent.includes(EDIT_REASON),
    `the note does not carry both halves: ${note.textContent}`);
});

// MUTANT "ACTIVATE_RIDES_BACKEND" (ProfileList.tsx: `const canReconnect =
// useCan(RECONNECT_CAP)` made `useCanConfigBackend()`). Observed from a byte
// backup (restored, sha256 compared): "w17ClassicReconnect: 8/11 passed", three
// red - "x an operator has ONE live control - Activate - and a stated lock on
// every write: Deep sky rig: Activate is not a live button for an operator",
// this case ("x an operator ACTIVATEs with the unforced request, once, from the
// redacted record: Planetary rig: Activate is a stated lock, but
// control.reconnect is held") and "x an operator is never offered the force
// dialog; the refusal names who can force: the operator's activate was not sent
// exactly once, or a force followed (expected 1, got 0)".
await testAsync("an operator ACTIVATEs with the unforced request, once, from the redacted record", async () => {
  const b = control(cardOf("Planetary rig"), /Activate/);
  assert(isLiveButton(b), "Planetary rig: Activate is a stated lock, but control.reconnect is held");
  await click(b);
  await flush();
  await act(async () => { await sleep(1900); });
  await flush();
  eq(activates.length, 1, "the activate was not sent exactly once");
  eq(activates[0].body.force, false, "an operator's activate was forced");
  assert(confirmReq() == null, "a dialog was left open after an unforced activate");
  assert(/is active/.test(toastText()),
    `the activate did not land and say so: ${toastText()}`);
});

// MUTANT "FORCE_OFFERED_TO_OPERATOR" (ProfileList.tsx: the call site's fourth
// argument `canConfig` made `true`). Observed from a byte backup (restored,
// sha256 compared): "w17ClassicReconnect: 10/11 passed" with this case red:
// "x an operator is never offered the force dialog; the refusal names who can
// force: an operator was offered the force dialog, which the rig answers 403
// (config.backend)".
await testAsync("an operator is never offered the force dialog; the refusal names who can force", async () => {
  await mountAs(OPERATOR);
  activateAnswer = { status: 409, body: { detail: { detail: "a sequence is running", code: "running" } } };
  await click(control(cardOf("Planetary rig"), /Activate/));
  await flush();
  assert(confirmReq() == null,
    "an operator was offered the force dialog, which the rig answers 403 (config.backend)");
  eq(activates.length, 1, "the operator's activate was not sent exactly once, or a force followed");
  eq(activates[0].body.force, false, "the operator's activate was forced");
  assert(/forcing the switch needs admin access/.test(toastText()),
    `the refusal does not say who can force: ${toastText()}`);
  assert(/A sequence is running\./.test(toastText()),
    `the toast dropped the server's own reason: ${toastText()}`);
});

// ================================================================= the viewer
await mountAs(VIEWER);

test("a viewer has no live control: Activate names operator or admin access, the writes name admin", () => {
  eq(cards().length, 2, "the panel did not render for the lock to be read");
  const c = cardOf("Planetary rig");
  const activate = control(c, /Activate/);
  assert(isLockedChip(activate), "a viewer's Activate is not a stated lock");
  eq(reasonOf(activate), "Activating a profile needs operator or admin access.",
    "the viewer's Activate reason");
  assert(isLockedChip(control(c, /Rename/)), "a viewer's Rename is not a stated lock");
  assert(isLockedChip(control(c, /Delete/)), "a viewer's Delete is not a stated lock");
  assert(isLockedChip(control(container, /Save Rig/)), "a viewer's Save Rig is not a stated lock");
  assert(container.querySelector('[data-testid="profiles-locknote"]') == null,
    "a viewer was shown the operator's note: they cannot activate anything");
});

await testAsync("pressing a viewer's locked Activate sends nothing and opens nothing", async () => {
  await click(control(cardOf("Planetary rig"), /Activate/));
  await flush();
  eq(commands.length, 0, "a locked Activate wrote to the rig");
  assert(confirmReq() == null, "a locked Activate opened a dialog");
});

// ===================================================== the failure decision
// The pure decision, so the matrix is read without mounting anything. The relay
// is checked FIRST (nobody can force there), then the caller's capability.
const RUNNING = (m = "a sequence is running") => new ApiError(m, 409, false, "running");

// MUTANT "OUTCOME_IGNORES_CAPABILITY" (ProfileList.tsx `activateFailureOutcome`:
// `if (!canForce) {` made `if (false) {`). Observed from a byte backup (restored,
// sha256 compared): "w17ClassicReconnect: 9/11 passed", this case and the
// mounted one red: "x activateFailureOutcome: LAN + may reconnect only is a
// toast naming who can force, never the dialog: a caller without config.backend
// was offered the force dialog".
test("activateFailureOutcome: LAN + may reconnect only is a toast naming who can force, never the dialog", () => {
  const o = activateFailureOutcome(RUNNING(), false, false, false);
  assert(o.kind === "toast",
    "a caller without config.backend was offered the force dialog");
  if (o.kind === "toast") {
    eq(o.message, forceNeedsBackend("a sequence is running", "admin access"),
      "the toast is not the shared forceNeedsBackend sentence");
    eq(o.level, "warning", "the level");
    assert(o.verbatim === true, "the toast is not verbatim, so the log-line shortener can cut who can force");
  }
  const admin = activateFailureOutcome(RUNNING(), false, false, true);
  eq(admin.kind, "force", "an admin on the LAN lost the force dialog");
});

test("activateFailureOutcome: the relay is answered before the capability, and a forced retry is unchanged", () => {
  const relay = activateFailureOutcome(RUNNING(), false, true, false);
  assert(relay.kind === "toast" && relay.message === forceNeedsLan("a sequence is running"),
    `over the relay the sentence is not the LAN one: ${JSON.stringify(relay)}`);
  const forced = activateFailureOutcome(RUNNING(), true, false, false);
  assert(forced.kind === "toast" && !/needs admin access/.test(forced.message),
    `a forced retry's refusal gained the capability sentence: ${JSON.stringify(forced)}`);
  const lane = activateFailureOutcome(new ApiError("'profile' is already running", 409), false, false, false);
  assert(lane.kind === "toast" && /Another profile connect is still running/.test(lane.message),
    `the lane guard changed: ${JSON.stringify(lane)}`);
});

// ============================================================ the interstitial
async function renderInterstitial(principal: unknown): Promise<string> {
  if (root) await act(async () => { root!.unmount(); });
  root = createRoot(container);
  useStore.setState({ principal, authGate: "open" } as never);
  await act(async () => { root!.render(createElement(NotConnectedInterstitial, { view: "capture" })); });
  await flush();
  return (container.textContent || "") as string;
}
const goToRig = () => control(container, /Go to Rig/);

// MUTANT "VIEWER_SENT_TO_ADMIN" (NotConnectedInterstitial.tsx: the viewer
// sentence's `accessPhrase(RECONNECT_CAP)` made `accessPhrase("config.backend")`).
// Observed from a byte backup (restored, sha256 compared): "w17ClassicReconnect:
// 10/11 passed", this case: "x the interstitial: a viewer is told whom to ask by
// the role table's phrase for reconnecting: Equipment not connectedConnect a
// camera to start capturing frames.Ask someone with admin access to reconnect
// the rig, then this view comes alive...." (the page text, flattened).
await testAsync("the interstitial: a viewer is told whom to ask by the role table's phrase for reconnecting", async () => {
  const t = await renderInterstitial(VIEWER);
  assert(/Ask someone with operator or admin access to reconnect the rig/.test(t),
    t.replace(/\s+/g, " "));
  assert(goToRig() == null, "a viewer was offered a Go to Rig that leads to a read-only page");
});

// MUTANT "OPERATOR_TREATED_AS_VIEWER" (NotConnectedInterstitial.tsx: `) :
// canReconnect ? (` made `) : false ? (`). Observed from a byte backup
// (restored, sha256 compared): "w17ClassicReconnect: 10/11 passed", this case:
// "x the interstitial: an operator is offered the way to the Rig page, where
// Activate is, and is not told to ask someone: an operator was told to ask
// someone else".
await testAsync("the interstitial: an operator is offered the way to the Rig page, where Activate is, and is not told to ask someone", async () => {
  const t = await renderInterstitial(OPERATOR);
  assert(!/Ask someone with/.test(t), "an operator was told to ask someone else");
  assert(goToRig() != null, "an operator who may reconnect was given no way to the Rig page");
  assert(/Activate a saved profile there/.test(t) && /needs admin access/.test(t),
    `the operator's line does not say what they can and cannot do there: ${t.replace(/\s+/g, " ")}`);
});

await testAsync("the interstitial: an admin is unchanged", async () => {
  const t = await renderInterstitial(ADMIN);
  assert(/Connect equipment first/.test(t), "an admin lost the headline");
  assert(goToRig() != null, "an admin lost the Go to Rig button");
  assert(!/Ask someone with/.test(t), "an admin was told to ask someone");
});

console.log(`w17ClassicReconnect: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
