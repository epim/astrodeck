// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w17RedactedActivate.test.ts - activating a saved profile as an OPERATOR, who
// receives the REDACTED profile record (#840, found by #759).
//
//   Run directly:  npx tsx src/next/hubs/rig/devices/__tests__/w17RedactedActivate.test.ts
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `activateProfileRow` used to say `getProfile(row.id) as Profile` on the claim
// "every action here is config.backend-gated". Since #759 an operator
// (control.reconnect, not config.backend) reaches it, and the route strips
// `nina_host`, `host`, `port` and `extra` from them. The two judgements the
// record feeds - `profileConnectsNothing` and `profileResolvesRealMotion` -
// read only `primary_backend`, `devices[].role` / `.backend` and `nina_host`,
// and take a STRUCTURAL parameter, so the record goes straight in with no cast.
//
// What is worth pinning is the ANSWER on the shape an operator really gets, in
// two places: the helpers themselves (a record typed `RedactedProfile`, so the
// compiler checks that the helpers still accept it), and the dialog
// `activateProfileRow` opens from a fetched operator-shaped record.
//
// MUTANT "a judgement depends on a field the operator never receives"
// (lib/equipment.ts `profilePrimary`: the first clause `p.nina_host &&
// devices.length === 0` made `p.nina_host!.length > 0 && devices.length ===
// 0`). Run from a byte backup in this worktree, restored byte-identically
// (sha256 compared): "w17RedactedActivate (rigConnect): 2/7 passed", red on
// five: "x an operator-shaped simulator record connects something and moves
// nothing real: Cannot read properties of undefined (reading 'length')", the
// stores-nothing and stripped-NINA pure cases with the same TypeError, "x an
// operator activating a profile that stores nothing is told so: no dialog:
// tearing down two devices for an empty rig went unasked" and "x an operator
// activating a simulator profile with nothing live is not asked: the activate
// was not sent exactly once (expected 1, got 0)". The two real-mount cases stay
// green because `profileConnectsNothing` short-circuits on a non-empty
// `devices` before it reaches `profilePrimary`.
//
// MUTANT "the parameter type names a field the operator never receives"
// (lib/equipment.ts `profileResolvesRealMotion`: `nina_host?: string | null`
// made `nina_host: string | null`). `tsc -b` is red, and among its errors:
// "src/next/hubs/rig/devices/__tests__/w17RedactedActivate.test.ts(L,32):
// error TS2345: Argument of type 'RedactedProfile' is not assignable to
// parameter of type '{ primary_backend?: string | null | undefined; devices?:
// ...; nina_host: string | null; }'. Types of property 'nina_host' are
// incompatible." That one is a compile-time guard: red in the UI gate (`tsc
// -b`), not in `run-tests.mjs`.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body></body></html>`, { url: "http://local/" });
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

import type { RedactedProfile } from "../../../../../api/backends";
import type { ProfileRow } from "../../../../../types";

const { profileConnectsNothing, profileResolvesRealMotion } = await import("../../../../../lib/equipment");
const { activateProfileRow } = await import("../rigConnect");
const { useStore } = await import("../../../../../store");

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

// ------------------------------------------------------------------ fixtures
// What `GET /api/profiles/{id}` sends a principal holding neither config.backend
// nor view.site_precise: `nina_host`, `nina_port`, `phd2_host`, `phd2_port` and
// `site_name` ABSENT (not null), and every device row without `host`, `port` or
// `extra` (server: api/redact.py `_redact_profile_for`).
const REAL_MOUNT: RedactedProfile = {
  id: "p-real", name: "Backyard SCT", primary_backend: "native",
  optics: null,
  devices: [
    { role: "camera", backend: "native", dev_type: "camera", dev_num: 0, name: "cam" },
    { role: "telescope", backend: "native", dev_type: "telescope", dev_num: 0, name: "mount" },
  ],
};
const SIM_ONLY: RedactedProfile = {
  id: "p-sim", name: "Simulator", primary_backend: "sim", optics: null,
  devices: [],
};
const NOTHING: RedactedProfile = {
  id: "p-none", name: "Empty", primary_backend: "none", optics: null,
  devices: [],
};
// A legacy NINA-only profile: for an operator the host that names it is gone,
// so the judgement must fall to the clause that over-prompts.
const NINA_STRIPPED: RedactedProfile = {
  id: "p-nina", name: "Old NINA rig", primary_backend: "nina", optics: null,
  devices: [],
};

// -------------------------------------------------------- the pure helpers
test("an operator-shaped record that drives a real mount still answers true from both helpers", () => {
  eq(profileConnectsNothing(REAL_MOUNT), false, "a rig with device rows connects something");
  eq(profileResolvesRealMotion(REAL_MOUNT), true,
    "a native telescope row is real motion, host or no host");
});

test("an operator-shaped simulator record connects something and moves nothing real", () => {
  eq(profileConnectsNothing(SIM_ONLY), false, "a captured simulator rig reconnects");
  eq(profileResolvesRealMotion(SIM_ONLY), false, "a sim primary resolves no real mount");
});

test("an operator-shaped record that stores nothing connects nothing and resolves no motion", () => {
  eq(profileConnectsNothing(NOTHING), true, "primary none with no rows connects nothing");
  eq(profileResolvesRealMotion(NOTHING), false,
    "the over-prompt must not fire on a profile that attaches nothing");
});

test("a record whose NINA host was stripped fails toward asking", () => {
  eq(profileConnectsNothing(NINA_STRIPPED), false, "a nina primary is not an empty rig");
  eq(profileResolvesRealMotion(NINA_STRIPPED), true,
    "with the host gone the non-sim primary clause still asks");
});

// --------------------------------------------- the dialog, from a fetched record
let profileBody: RedactedProfile = REAL_MOUNT;
const posts: string[] = [];
g.fetch = async (url: string, init?: { method?: string }) => {
  const u = String(url);
  const method = init?.method ?? "GET";
  const ok = (json: unknown) => ({ ok: true, status: 200, statusText: "OK", json: async () => json });
  if (method === "POST") { posts.push(u); return ok({ started: "profile" }); }
  if (/\/api\/profiles$/.test(u)) {
    return ok([{ id: profileBody.id, name: profileBody.name, mode: "alpaca",
      devices_count: profileBody.devices.length, site_name: null, active: true }]);
  }
  if (/\/api\/profiles\/[^/]+$/.test(u)) return ok(JSON.parse(JSON.stringify(profileBody)));
  return ok({});
};

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount", "control.reconnect"],
};

function seed(): void {
  posts.length = 0;
  useStore.setState({
    principal: OPERATOR, authGate: "open", wsPhase: "up", toasts: [], confirm: null,
    sequence: { state: "idle" },
  } as never);
}

const rowOf = (p: RedactedProfile): ProfileRow => ({
  id: p.id, name: p.name, mode: "alpaca", devices_count: p.devices.length,
  site_name: null, active: false,
});
const hooks = { setBusy() {}, onRows() {}, reload() {} };

/** Start the activate, wait for the dialog it opens (or for it to finish), and
 *  report what it showed. A dialog is answered "cancel" so nothing proceeds. */
async function drive(p: RedactedProfile, liveDevices: number): Promise<{
  dialog: { title: string; body: string; mode: string } | null;
}> {
  profileBody = p;
  seed();
  const done = activateProfileRow(rowOf(p), liveDevices, hooks);
  let dialog: { title: string; body: string; mode: string } | null = null;
  const until = Date.now() + 3000;
  let finished = false;
  void done.then(() => { finished = true; });
  while (Date.now() < until && !finished) {
    const c = useStore.getState().confirm as any;
    if (c) {
      dialog = { title: String(c.title), body: String(c.body), mode: String(c.mode) };
      useStore.getState().resolveConfirm(false);
    }
    await new Promise((r) => setTimeout(r, 10));
  }
  await done;
  return { dialog };
}

await testAsync("an operator activating a real-mount profile is asked, with a hold, before it attaches", async () => {
  const { dialog } = await drive(REAL_MOUNT, 0);
  assert(dialog !== null, "no dialog: the redacted record was read as a rig that moves nothing");
  eq(dialog!.mode, "hold", "a real mount is a hold-to-confirm");
  assert(/real mount or focuser/.test(dialog!.body), `the body drops the escalation: ${dialog!.body}`);
  eq(posts.length, 0, "the cancelled dialog still sent an activate");
});

await testAsync("an operator activating a profile that stores nothing is told so", async () => {
  const { dialog } = await drive(NOTHING, 2);
  assert(dialog !== null, "no dialog: tearing down two devices for an empty rig went unasked");
  assert(/stores no devices, so nothing reconnects/.test(dialog!.body),
    `the dialog does not say the rig ends up empty: ${dialog!.body}`);
  eq(dialog!.mode, "hold", "an activate that leaves no rig is a hold-to-confirm");
});

await testAsync("an operator activating a simulator profile with nothing live is not asked", async () => {
  const { dialog } = await drive(SIM_ONLY, 0);
  eq(dialog, null, "a dialog opened for an activate that drops nothing and attaches nothing real");
  eq(posts.filter((u) => /\/p-sim\/activate$/.test(u)).length, 1,
    "the activate was not sent exactly once");
});

console.log(`w17RedactedActivate (rigConnect): ${passed}/${passed + failed} passed`);
for (const f of failures) console.log(f);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
