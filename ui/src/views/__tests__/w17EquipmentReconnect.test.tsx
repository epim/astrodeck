// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w17EquipmentReconnect.test.tsx - the CLASSIC Equipment page's profile ACTIVATE
// for an operator (#759, #839).
//
//   Run directly:  npx tsx src/views/__tests__/w17EquipmentReconnect.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// The Rig page's Profiles panel locked ACTIVATE on `config.backend`, so an
// operator who may bring the rig back (`control.reconnect`, owner ruling
// 2026-10-07) met a stated lock on the one control the ruling is about, on the
// screen that is still the production root. WHAT IS WORTH ASSERTING, mounted:
//
//   AN OPERATOR'S ACTIVATE IS LIVE AND SENDS THE UNFORCED REQUEST. The page's
//   other write - Save - stays a stated lock naming admin access. The record the
//   dialog judgement reads is the operator's REDACTED one (no host, port, extra,
//   nina_host) and the answer is still right.
//   A VIEWER'S ACTIVATE IS A STATED LOCK naming operator or admin access, and a
//   press sends nothing.
//   THE FAILURE POINTER IS THE CALLER'S. "force-activate from Settings ->
//   Profiles" is true for an admin and sends an operator to a dialog they are
//   never offered (`ProfileList`), so an operator is told who can force instead.
//   THE READ-ONLY NOTE no longer calls the whole page read-only while ACTIVATE
//   is armed.
//
// MUTANTS RUN: see the comment above each case (each from a byte backup,
// restored byte-identically with sha256 compared).

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
const ROWS = [
  { id: "a", name: "Deep sky rig", mode: "alpaca", devices_count: 4, active: true, site_name: null },
  { id: "b", name: "Planetary rig", mode: "empty", devices_count: 0, active: false, site_name: null },
];
let rows = ROWS.map((r) => ({ ...r }));
const activates: { url: string; body: any }[] = [];
const writes: string[] = [];
let activateAnswer: { status: number; body: any } | null = null;

const ok = (json: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => json } as any);
const fail = (status: number, body: unknown) =>
  ({ ok: false, status, statusText: `HTTP ${status}`, json: async () => body } as any);

g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  if (method !== "GET") writes.push(`${method} ${path}`);
  if (path.includes("/activate") && method === "POST") {
    let body: any = null;
    try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
    activates.push({ url: path, body });
    if (activateAnswer) return fail(activateAnswer.status, activateAnswer.body);
    rows = rows.map((r) => ({ ...r, active: path.includes(`/profiles/${r.id}/`) }));
    return ok({ started: "profile" });
  }
  if (path.match(/\/api\/profiles\/[^/]+$/) && method === "GET") {
    // The OPERATOR's record: redacted. A mount-bearing native rig, so the dialog
    // judgement has something to say from the fields that survive redaction.
    const id = path.split("/").pop()!;
    const row = rows.find((r) => r.id === id)!;
    return ok({
      id: row.id, name: row.name, primary_backend: "none", devices: [],
      nina_port: 0, phd2_port: 0, optics: null, providers: null,
    });
  }
  if (path.includes("/api/profiles")) return ok(rows);
  if (path.includes("/api/drivers")) return ok({ roles: ["camera", "mount"], drivers: [] });
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const EquipmentViewModule = await import("../EquipmentView");
const EquipmentView = EquipmentViewModule.default;
const { activateErrorMessage } = EquipmentViewModule;
const { ApiError } = await import("../../api");
const { forceNeedsBackend } = await import("../../next/hubs/rig/profiles/profilesModel");

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

const ADMIN = { role: "admin", email: null, caps: ["view.status", "control.reconnect", "config.backend"] };
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
  writes.length = 0;
  activateAnswer = null;
  if (root) await act(async () => { root!.unmount(); });
  root = createRoot(container);
  useStore.setState({
    principal, authGate: "open", toasts: [], confirm: null,
    status: { connected: {}, looping: false, mode: "none" },
    sequence: { state: "idle" },
  } as never);
  await act(async () => { root!.render(createElement(EquipmentView)); });
  await flush();
}

/** The Activate control on a profile row: the button whose row names `name`. */
function activateOn(name: string): any {
  const row = [...container.querySelectorAll("div")].find(
    (d: any) => d.className?.includes?.("border-line") && (d.textContent || "").includes(name)
      && [...d.querySelectorAll("button")].some((b: any) => (b.textContent || "").trim() === "Activate"),
  ) as any;
  if (!row) throw new Error(`no profile row rendered for "${name}"`);
  return [...row.querySelectorAll("button")].find((b: any) => (b.textContent || "").trim() === "Activate");
}
const click = (node: any) =>
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
const toastText = () => ((useStore.getState().toasts ?? []) as any[]).map((t) => String(t.title ?? "")).join(" | ");
const confirmReq = () => (useStore.getState() as any).confirm;
const text = () => (container.textContent || "") as string;

// ============================================================== the operator
await mountAs(OPERATOR);

// MUTANT "EQUIP_ACTIVATE_RIDES_BACKEND" (EquipmentView.tsx: the reason's
// `!canReconnect` made `!canConfig`). Observed from a byte backup (restored,
// sha256 compared): "w17EquipmentReconnect: 7/9 passed", this case red - "x an
// operator's ACTIVATE is live and sends the unforced request once: the
// operator's Activate is a stated lock (Activating a profile needs operator or
// admin access.) but control.reconnect is held" - and the running-conflict case
// ("the operator's activate was not sent exactly once (expected 1, got 0)").
await testAsync("an operator's ACTIVATE is live and sends the unforced request once", async () => {
  const b = activateOn("Planetary rig");
  assert(b.getAttribute("aria-disabled") !== "true",
    `the operator's Activate is a stated lock (${b.getAttribute("title")}) but control.reconnect is held`);
  await click(b);
  await flush();
  await act(async () => { await sleep(1900); });
  await flush();
  eq(activates.length, 1, "the activate was not sent exactly once");
  eq(activates[0].body?.force ?? false, false, "an operator's activate was forced");
  assert(/is active/.test(toastText()), `the activate did not land and say so: ${toastText()}`);
  assert(confirmReq() == null, "a dialog was left open");
});

test("the page's other write stays a stated lock for the operator: Save names admin access", () => {
  assert(/Saving a profile needs admin access\./.test(text()),
    "the operator's Save no longer says it needs admin access");
});

// MUTANT "EQUIP_NOTE_IGNORES_OPERATOR" (EquipmentView.tsx: `{canReconnect &&
// " You can reconnect a saved profile below."}` removed). Observed from a byte
// backup (restored, sha256 compared): "w17EquipmentReconnect: 8/9 passed", this
// case red - "x the read-only note stops calling the page read-only while
// ACTIVATE is armed: the note does not say the operator can still reconnect a
// saved profile".
test("the read-only note stops calling the page read-only while ACTIVATE is armed", () => {
  assert(/Read-only — connecting equipment needs admin access\./.test(text()), "the note is gone");
  assert(/You can reconnect a saved profile below\./.test(text()),
    "the note does not say the operator can still reconnect a saved profile");
});

// MUTANT "EQUIP_POINTER_IGNORES_CALLER" (EquipmentView.tsx: the toast call's
// third argument `canConfig` made `true`). Observed from a byte backup
// (restored, sha256 compared): "w17EquipmentReconnect: 8/9 passed", this case
// red - "x an operator's running conflict says who can force, and points at no
// dialog: the refusal does not say who can force: A sequence is running. Stop it
// first, or force-activate from Settings → Profiles."
// MUTANT "EQUIP_MESSAGE_IGNORES_CAPABILITY" (`activateErrorMessage`: `: canForce
// ?` made `: true ?`): 7/9, this case and the pure one red - "x
// activateErrorMessage: the pointer is the caller's, and the relay still wins:
// an operator on the LAN (expected A sequence is running. Stop it first: forcing
// the switch needs admin access., got A sequence is running. Stop it first, or
// force-activate from Settings → Profiles.)".
await testAsync("an operator's running conflict says who can force, and points at no dialog", async () => {
  await mountAs(OPERATOR);
  activateAnswer = { status: 409, body: { detail: { detail: "a sequence is running", code: "running" } } };
  await click(activateOn("Planetary rig"));
  await flush();
  eq(activates.length, 1, "the operator's activate was not sent exactly once");
  assert(/forcing the switch needs admin access/.test(toastText()),
    `the refusal does not say who can force: ${toastText()}`);
  assert(!/force-activate from Settings/.test(toastText()),
    `the operator was sent to a force dialog they are never offered: ${toastText()}`);
  assert(/A sequence is running\./.test(toastText()), `the server's own reason was dropped: ${toastText()}`);
});

// =================================================================== the admin
await mountAs(ADMIN);

await testAsync("an admin's running conflict still points at Settings -> Profiles", async () => {
  activateAnswer = { status: 409, body: { detail: { detail: "a sequence is running", code: "running" } } };
  await click(activateOn("Planetary rig"));
  await flush();
  assert(/force-activate from Settings/.test(toastText()),
    `the admin lost the pointer to the force dialog: ${toastText()}`);
  assert(!/needs admin access/.test(toastText()), `the admin was told to ask for admin access: ${toastText()}`);
});

test("the admin's page carries no read-only note", () => {
  assert(!/Read-only/.test(text()), "an admin was shown a read-only note");
});

// ================================================================= the viewer
await mountAs(VIEWER);

await testAsync("a viewer's ACTIVATE is a stated lock naming operator or admin access, and a press sends nothing", async () => {
  const b = activateOn("Planetary rig");
  eq(b.getAttribute("aria-disabled"), "true", "a viewer's Activate is live");
  eq(b.getAttribute("title"), "Activating a profile needs operator or admin access.",
    "a viewer's Activate reason");
  await click(b);
  await flush();
  eq(writes.length, 0, "a locked Activate wrote to the rig");
  assert(confirmReq() == null, "a locked Activate opened a dialog");
  assert(/Activating a profile needs operator or admin access/.test(toastText()),
    `the press did not explain itself: ${toastText()}`);
});

test("a viewer's note is the plain read-only one", () => {
  assert(/Read-only — connecting equipment needs admin access\./.test(text()), "the note is gone");
  assert(!/You can reconnect a saved profile below/.test(text()), "a viewer was told they can reconnect");
});

// ============================================================== the pure copy
const RUNNING = (m = "a sequence is running") => new ApiError(m, 409, false, "running");

test("activateErrorMessage: the pointer is the caller's, and the relay still wins", () => {
  eq(activateErrorMessage(RUNNING(), false, true),
    "A sequence is running. Stop it first, or force-activate from Settings → Profiles.",
    "an admin on the LAN");
  eq(activateErrorMessage(RUNNING(), false, false),
    forceNeedsBackend("a sequence is running", "admin access"),
    "an operator on the LAN");
  assert(/forcing the switch needs the LAN/.test(activateErrorMessage(RUNNING(), true, false)),
    "over the relay the sentence is not the LAN one");
  eq(activateErrorMessage(RUNNING()), activateErrorMessage(RUNNING(), false, true),
    "the default is the admin pointer");
});

console.log(`w17EquipmentReconnect: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
