// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// relayFenceDom.test.tsx - every SETTINGS write the rig fences to the LAN,
// mounted OVER THE RELAY and pressed (wave-2 review R8's FIX-U-settings P1).
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/relayFenceDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by
//   `npx tsc --noEmit -p tsconfig.json`.
//
// THE DEFECT THIS FILE EXISTS FOR. `app.py` refuses a dozen route families to
// any session that arrived through the relay - `_REMOTE_LOCAL_ONLY_EXACT`,
// `_REMOTE_LOCAL_ONLY_PREFIXES` and `_REMOTE_LOCAL_ONLY_MUTATION_PREFIXES` -
// answering 403 `code: "local_only"` FOR EVERY ROLE, an admin included. Nothing
// in Settings knew that: UPGRADE, CHECK NOW, SAVE SETTINGS, FACTORY RESET, SAVE
// METHODS, the sky-pack DOWNLOAD and DELETE, PUSH NOW, the solver pick and
// REFRESH ELEMENTS all rendered armed from the sofa and the user learned the
// refusal by pressing them. `gate.ts`'s `needsLan` is the fix, and this file is
// what keeps it wired. (The PEOPLE list was on that list until #685 opened it
// over the relay; the PEOPLE block below pins what is open and what is not.)
//
// WHY THE PRINCIPAL IS AN ADMIN WITH EVERY CAPABILITY. `lockReason` ranks the
// LAN rule ABOVE the capability rule, so a viewer would be locked either way and
// every assertion below would pass over a component that had never heard of the
// fence. An admin has exactly one possible blocker here, which is what makes
// LOCAL_ONLY_REASON an assertion about `needsLan` and nothing else.
//
// NAMED SABOTAGES, each of which turns a named test RED:
//   * drop `needsLan: true` from any one editor's `useLock` -> that editor's
//     block fails on `aria-disabled` (null, not "true") and on its toast.
//   * move the `needsLan` rule below the cap rule in `gate.ts` -> nothing here
//     moves (the admin holds every cap), but `lib/__tests__/gate.test.ts` goes
//     red; that split is deliberate, this file grades the WIRING.
//   * drop the `|| onRelay` guard from `AuthMethodsEditor.refreshSetupUsers` /
//     `FactoryResetEditor.refresh` -> "asks the rig for nothing it will refuse"
//     fails on the recorded GET.
//   * PEOPLE is the one editor that went the OTHER way (#685): the rig now
//     answers the list and four changes over the relay behind a sign-in under
//     five minutes old, so the PEOPLE block below asserts the list IS requested,
//     the controls ARE armed, and a `step_up_required` refusal opens SIGN IN
//     AGAIN. Its named sabotages:
//       - put `|| onRelay` back into `UsersEditor.refresh` -> "the list is
//         requested over the relay" fails on the missing GET and the card;
//       - make `isStepUpRequired` test `status === 403` instead of `code` ->
//         "a capability refusal does not open SIGN IN AGAIN" goes red;
//       - drop `markRequired()` from `UsersEditor.report` -> "a step_up_required
//         refusal opens SIGN IN AGAIN" goes red;
//       - drop the `LAN_CHANGE_PREFIX` pass-through in `errText` -> "a refusal
//         that names its rule keeps its sentence" goes red;
//       - drop `setPhase("fresh")` from `useStepUp.signInLocal` -> "a local
//         sign-in posts /auth/local and closes the form" goes red.
//     And the wave 15 integration's per-control locks and retry (#734, #731),
//     each run from a byte backup of the file named, restored byte-identically
//     (sha256 compared), `node --import tsx` on this file. Observed, all of
//     them out of 30 cases:
//       - `UserRow.tsx`: RESET's `lockedReason={resetLock}` made
//         `lockedReason={lockedReason}`: 29/30, "x PEOPLE: over the relay RESET
//         and the admin and syncer role options are locked with the LAN
//         sentence, and the rest of the row stays armed: RESET renders ARMED over
//         the relay - the rig refuses every password reset there (expected true,
//         got null)";
//       - `UserRow.tsx`: the role options' `lockedReason: ...` made `null`:
//         29/30, the same case, "the ADMIN option renders ARMED over the relay -
//         the rig refuses it (expected true, got null)";
//       - `AddUserForm.tsx`: the same made `null`: 29/30, "x PEOPLE: over the
//         relay the add form locks the admin and syncer roles and nothing else:
//         the add form's ADMIN option renders ARMED over the relay - the rig
//         refuses it (expected true, got null)";
//       - `UserRow.tsx`: the enabled switch locked with `lanOnlyReason ??
//         lockedReason`: 29/30, "the enabled switch is locked over the relay,
//         but the rig does it behind a recent sign-in (expected null, got
//         true)";
//       - `UsersEditor.tsx`: the effect that sends the held change disabled
//         (`if (phase !== "fresh") return;` made `if (true) return;`): 27/30, three
//         cases, among them "x PEOPLE: a change the rig refused for want of a
//         sign-in is sent again once a password sign-in lands: the refused change
//         was not sent again after the sign-in (expected 2, got 1)";
//       - `UsersEditor.tsx`: `onStepUp={hold}` not passed to `AddUserForm`: 29/30,
//         "x PEOPLE: the same holds for ADD USER, and a held create is not sent
//         from a form that has been emptied: a refused create did not open the
//         SIGN IN AGAIN form (expected required, got idle)";
//       - `AddUserForm.tsx`: the retry's `if (now.blocker === null)` guard
//         removed: 29/30, "x ...: a held create was sent from a form that had
//         been emptied (expected 1, got 2)".
//   * lock everything unconditionally instead -> the VACUITY GUARD at the
//     bottom fails: `cal-build` (POST /api/calibration/build, deliberately NOT
//     on the fence) must stay pressable on the same mount, on the same origin.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// Four of these areas ship their own stylesheet and import it from the module
// this file reaches (`tuning/<area>/index.ts`, the area root). Node cannot load
// a `.css` file, so a load hook answers with an empty module - registered
// BEFORE the first `await import` that reaches one.
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
// The URL is the LAN one on purpose. `relay.ts` falls back to the pathname when
// nothing has told it the rig's own answer, so a `/h/<home>/` URL would make
// every block below pass on the fallback alone - and the fallback is not the
// thing under test. `noteRemoteStatus` supplies the rig's `via` instead, which
// is the path a real session takes (the Connection sheet reads
// `GET /api/remote/status` and hands the answer over).
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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "HTMLAnchorElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent", "PointerEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "requestAnimationFrame", "cancelAnimationFrame", "Image",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Ask { url: string; method: string; body?: string }
const asks: Ask[] = [];

/** What the next `PATCH /api/users/<id>` answers. The default is a plain 200;
 *  the PEOPLE block flips it to each refusal the rig can send and flips it back. */
type PatchAnswer = "ok" | "step_up" | "capability" | "lan_reason" | "fence";
const usersScenario: { patch: PatchAnswer; create: "ok" | "step_up" } = { patch: "ok", create: "ok" };
const refuse = (status: number, data: any) =>
  ({ ok: false, status, statusText: "Forbidden", json: async () => data });

const CONFIG: any = {
  version: 11,
  site: { is_default: false },
  optics: {
    focal_length_mm: 530, pixel_size_um: 3.76, sensor_width_px: 6248,
    sensor_height_px: 4176, auto_from_camera: false, guide_focal_length_mm: 200,
    telescope_name: "Askar FRA400", aperture_mm: 0, reducer: 1,
  },
  survey: { online_fetch: false },
  calibration: {
    dark_temp_tolerance_c: 1, dark_exposure_tolerance_pct: 10,
    dark_temp_bin_c: 2, flat_max_age_days: 30, min_frames: 5,
  },
  sync_push: { enabled: false, kind: "local_dir", path: "", label: "", limit_per_pass: 0 },
  naming: { template: "{target}/{filter}/{ts}" },
  standards: {},
  wcs_stamp: { solver: "auto", downsample: 2, min_stars: 10 },
  solve_saved_lights: false,
  safety: { preset: "balanced" },
  escalation: { cooling: "pause", guiding: "pause" },
  alerts: [],
  deadman_configured: false,
  update: {
    enabled: true, auto_check: false, check_interval_hours: 24, channel: "stable",
    repo: "epim/astrodeck", signing_pubkey: "key", health_timeout_s: 60, last_check_ts: null,
  },
  auth: {
    methods: ["local"], provider: "local", google_configured: false,
    admin_token_configured: false, session_signing_configured: true,
    role_allowlist: {}, default_role: "viewer", trust_loopback: true,
    session_ttl_s: 28800, local_enabled_first_run: true,
  },
  providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" },
};

const ok = (data: any) => ({ ok: true, status: 200, statusText: "OK", json: async () => data });

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = (init?.method ?? "GET").toUpperCase();
  asks.push({ url: u, method, body: typeof init?.body === "string" ? init.body : undefined });
  if (u.includes("/api/survey/pack")) {
    return ok({ present: true, bytes: 262_144_000, order: 4, fetched_at: 1_756_000_000, fetching: null });
  }
  if (u.includes("/api/sync/push")) {
    return ok({ configured: true, running: false, last: null, last_ok_at: null });
  }
  if (u.includes("/api/calibration/masters")) return ok([]);
  // THESE TWO ANSWER PROPERLY ON PURPOSE. They are the routes the relay guards
  // stop this UI from ever asking for, and a fixture that answered `{ok:true}`
  // would make the sabotage (deleting a guard) CRASH on an undefined field
  // instead of failing the assertion that names the defect. A real shape means
  // the removed guard reads as "a request went out", which is the finding.
  if (u.includes("/api/system/factory-reset")) {
    return ok({
      profiles: 3, plans: 2, drivers: 4, alert_sinks: 1, users: 2,
      captures: { frames: 170, entries: 12, bytes: 42_000_000_000 },
      preserved_capture_entries: ["logs", "sky packs"],
      can_reset: true, blocked_reason: null, remote_paired: true, update_credential: false,
    });
  }
  if (u.includes("/api/users")) {
    if (method === "POST" && usersScenario.create === "step_up") {
      return refuse(403, { detail: {
        code: "step_up_required",
        detail: "Sign in again to manage people from outside the LAN.",
      } });
    }
    if (method === "POST") {
      return { ok: true, status: 201, statusText: "Created", json: async () => (
        { id: "u3", username: "new@rig", email: "new@rig", role: "operator", enabled: true }) };
    }
    if (method === "PATCH") {
      switch (usersScenario.patch) {
        case "step_up":
          return refuse(403, { detail: {
            code: "step_up_required",
            detail: "Sign in again to manage people from outside the LAN.",
          } });
        case "capability":
          return refuse(403, { detail: "capability not held" });
        case "lan_reason":
          return refuse(403, { detail: {
            code: "local_only",
            detail: "This change needs the LAN: an administrator can only be changed at the rig",
          } });
        case "fence":
          return refuse(403, {
            detail: "this security-sensitive operation is LAN-only", code: "local_only",
          });
        default:
          return ok({ id: "u2", username: "guest@rig", email: "guest@rig", role: "operator", enabled: true });
      }
    }
    return ok({ users: [
      { id: "u1", username: "bear", email: "bear@rig", role: "admin", enabled: true },
      { id: "u2", username: "guest@rig", email: "guest@rig", role: "viewer", enabled: true },
    ] });
  }
  // A re-sign-in mints a cookie and the principal is read again. The principal
  // answers as the same admin the tests seed, so a sign-in that "worked" does
  // not swap the identity under the editor.
  if (u.includes("/api/me")) {
    return ok({ role: "admin", email: "admin@rig", caps: ALL_CAPS });
  }
  if (u.includes("/api/ephemeris/status")) {
    return ok({
      satellites: { present: true, count: 200, source: "celestrak", fetched_unix: 1_756_000_000, age_days: 2 },
      comets: { present: false, count: 0, source: null, fetched_unix: null, age_days: null },
      fetching: [],
    });
  }
  if (u.includes("/api/update/status")) {
    return ok({
      current: "0.3.28", latest: "0.3.29", update_available: true, channel: "stable",
      phase: "idle", progress: 0, supervised: true, can_apply: true,
      apply_blocked_reason: null, last_check_ts: 1_756_000_000, notes_md: "", last_result: null,
    });
  }
  if (u.includes("/api/config")) return ok(CONFIG);
  return ok({ ok: true });
};

// ------------------------------------------ imports, AFTER the globals are set
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { LOCAL_ONLY_REASON } = await import("../../../lib/gate");
const { noteRemoteStatus, resetRelayForTests, onRelay } = await import("../../../lib/relay");

const { UpdateEditor, FactoryResetEditor } = await import("../tuning/system");
const { UsersEditor, AuthMethodsEditor } = await import("../tuning/people");
const {
  CalibrationLibraryEditor, CalibrationTolerancesEditor, SkyPackEditor,
} = await import("../tuning/calibration");
const { SyncEditor, NamingEditor, StandardsEditor, WcsStampEditor } = await import("../tuning/files");
const { SafetyTuningPanel } = await import("../tuning/safety");
const { EphemerisCard } = await import("../sheets/EphemerisCard");

// -------------------------------------------------------------------- harness
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
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
const q = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const click = async (el: any) => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};

const ALL_CAPS = [
  "view.status", "view.preview", "view.media", "view.weather", "view.site_precise",
  "view.site_derived", "control.capture", "control.guide", "control.mount",
  "control.power", "config.backend", "config.safety", "config.solar_override",
  "config.site_optics", "config.alerts", "admin.users", "system.update",
];

function seed(cfg: any = CONFIG): void {
  useStore.setState({
    config: cfg,
    principal: { role: "admin", email: "admin@rig", caps: ALL_CAPS },
    authGate: "open",
    wsPhase: "up",
    wsConnected: true,
    equipConnected: true,
    status: { connected: {}, looping: false, busy: null, busy_lanes: [] },
    sequence: { state: "idle" },
    update: {
      current: "0.3.28", latest: "0.3.29", update_available: true, channel: "stable",
      phase: "idle", progress: 0, supervised: true, can_apply: true,
      apply_blocked_reason: null, last_check_ts: 1_756_000_000, notes_md: "", last_result: null,
    },
    toasts: [],
  } as never);
}

/** Mount one editor on the relay origin. The log is cleared BEFORE the render,
 *  not after: three of these editors must ask the rig for NOTHING on a tunnelled
 *  origin (the people list, the guided card's copy of it, the factory-reset
 *  scope), and a log cleared after mount would have thrown away the very request
 *  those assertions are looking for - the guard could be deleted and the test
 *  would stay green. Mounting fires no writes, so `writes()` can safely read the
 *  whole log. */
const mount = async (el: any, cfg: any = CONFIG): Promise<void> => {
  await act(async () => { root.render(null); });
  await settle();
  seed(cfg);
  asks.length = 0;
  await act(async () => { root.render(el); });
  await settle();
  await act(async () => { useStore.setState({ toasts: [] } as never); });
};

/** Every non-GET request this editor has made. The fenced routes answer 403 in
 *  production, so "the control fired nothing" is the assertion that separates an
 *  honest lock from a button that presses and then apologises. */
const writes = () => asks.filter((a) => a.method !== "GET");
const toastTitles = () =>
  ((useStore.getState() as any).toasts as Array<{ title?: string }>).map((t) => t.title);

/** The whole assertion for one fenced control, in the words a failure needs:
 *  dimmed, aria-disabled, carrying THIS sentence, and firing nothing. */
async function refuses(id: string, what: string, pressSel?: string): Promise<void> {
  const el = q(id);
  assert(el != null, `${id} (${what}) is not on the page - the fixture is wrong, not the component`);
  eq(el.hasAttribute("disabled"), false, `${id} uses the native disabled attribute`);
  eq(el.getAttribute("aria-disabled"), "true",
    `${id} (${what}) renders ARMED over the relay - the rig would answer 403 local_only`);
  eq(el.getAttribute("title"), LOCAL_ONLY_REASON,
    `${id} does not name the relay as the blocker`);
  // `Segmented` marks the GROUP and handles the press on each option, so a
  // radiogroup needs the option named rather than its container.
  const target = pressSel ? el.querySelector(pressSel) : el;
  assert(target != null, `${id} has no press target matching ${pressSel}`);
  await act(async () => { useStore.setState({ toasts: [] } as never); });
  await click(target);
  assert(toastTitles().includes(LOCAL_ONLY_REASON),
    `${id} refused in silence: toasts were ${JSON.stringify(toastTitles())}`);
  eq(writes().length, 0,
    `${id} reached the rig anyway: ${JSON.stringify(writes())}`);
}

// ====================================================== the origin under test
// The rig's own `via`, exactly as the Connection sheet hands it over.
noteRemoteStatus({ via: "relay" });

test("precondition: this tab counts as tunnelled, from the rig's own answer", () => {
  eq(onRelay(), true,
    "the relay derivation says LAN - every assertion in this file would be vacuous");
});

// ============================================================ SYSTEM > UPDATE
// /api/update/check, /api/update/apply and /api/update/config are EXACT entries
// on `_REMOTE_LOCAL_ONLY_EXACT`, which catches every method.
await mount(createElement(UpdateEditor));

await testAsync("UPDATE: CHECK NOW, UPGRADE and SAVE SETTINGS all refuse over the relay", async () => {
  assert(q("update-status") != null, "the update editor never rendered");
  await refuses("update-check", "POST /api/update/check");
  await refuses("update-apply", "POST /api/update/apply");
  await refuses("update-save", "POST /api/update/config");
});

test("UPDATE: the read-only note names the relay, not the capability an admin holds", () => {
  const note = q("update-lock-note");
  assert(note != null, "no read-only note on a panel where nothing can be saved");
  assert(String(note.textContent).includes(LOCAL_ONLY_REASON),
    `the note names the wrong blocker: "${note.textContent}"`);
});

// ===================================================== SYSTEM > FACTORY RESET
// `/api/system/factory-reset` is EXACT too, so the scope-preview GET is fenced
// exactly like the POST that performs the reset.
await mount(createElement(FactoryResetEditor));

await testAsync("FACTORY RESET: the button refuses, and the preview GET is never fired", async () => {
  assert(q("reset-scope") != null, "the factory-reset editor never rendered");
  eq(asks.filter((a) => a.url.includes("/api/system/factory-reset")).length, 0,
    "the scope preview was requested over the relay - a 403 and a red box for a "
    + "number this origin is not allowed to have");
  await refuses("reset-run", "POST /api/system/factory-reset");
  await refuses("reset-also-captures", "the delete-frames opt-in");
});

test("FACTORY RESET: the counts say they were not read, never \"counting...\" forever", () => {
  const kept = q("reset-kept");
  assert(kept != null, "the KEPT column is missing - the assertion below would be vacuous");
  assert(!/counting\.\.\./.test(String(kept.textContent)),
    `a count that will never arrive is still spinning: "${kept.textContent}"`);
  assert(/LAN-only/.test(String(kept.textContent)),
    `the scope does not say why it has no numbers: "${kept.textContent}"`);
});

// ============================================================ PEOPLE > PEOPLE
// The ONE editor that went the other way (#685). `/api/users` used to be fenced
// by prefix for every method, so this editor showed a LAN-only card; the rig now
// answers the list read and four changes (add a Google-only viewer or operator,
// move someone between viewer and operator or enable/disable them, delete a
// non-admin) over the relay behind a sign-in under five minutes old. So the
// assertions are the mirror image of every other block here: the list IS
// requested, the controls are armed, and the editor knows what to do when the
// rig says the sign-in is too old.
useStore.setState({
  authMethods: { methods: ["local", "google"], google_configured: true, first_run: false },
} as never);
await mount(createElement(UsersEditor));

const typeInto = async (el: any, value: string) => {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};
/** A press whose answer is a CHAIN of awaits (fetch, then the error path, then a
 *  state update). `click` + `settle` leaves a gap between its two act scopes that
 *  such a chain can land in, which React reports as an update outside act; this
 *  keeps ONE act scope open across the whole chain. */
const pressAndWait = async (el: any) => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
  });
};
const usersGets = () => asks.filter((a) => a.method === "GET" && a.url.includes("/api/users"));
const stepUpState = () => q("users-stepup")?.getAttribute("data-state");
/** Press the OPERATOR option on the second person's role control and wait. */
const changeGuestRole = async () => {
  const rows = container.querySelectorAll('[data-testid="users-row"]');
  eq(rows.length, 2, "the list did not render both people");
  const option = rows[1].querySelector('[data-testid="users-row-role"] [data-value="operator"]');
  assert(option != null, "the role control has no OPERATOR option to press");
  await pressAndWait(option);
};

await testAsync("PEOPLE: the list is requested over the relay, and the controls are armed", async () => {
  assert(q("users-editor") != null, "the people editor never rendered");
  assert(usersGets().length >= 1,
    "GET /api/users was not fired over the relay - the rig answers it now (#685)");
  assert(q("users-lan-only") == null,
    "the LAN-only card is still on the page over a list the rig now serves");
  assert(q("users-list") != null, "the list never rendered over the relay");
  eq(container.querySelectorAll('[data-testid="users-row"]').length, 2,
    "the list is missing a person");
  eq(q("users-add").getAttribute("aria-disabled"), null,
    "ADD USER is locked over the relay, but the rig accepts it behind a recent sign-in");
  eq(q("users-locknote"), null, "an admin is shown a read-only note over the relay");
});

test("PEOPLE: the card says what the five-minute rule is for and what stays on the LAN", () => {
  const card = q("users-stepup");
  assert(card != null, "no step-up card over the relay");
  eq(stepUpState(), "idle", "the card opened before anything asked for a sign-in");
  const text = String(card.textContent);
  assert(/less than 5 minutes old/.test(text), `the rule's window is not stated: "${text}"`);
  assert(/replay/.test(text), `the card does not say why the rule exists: "${text}"`);
  assert(/Passwords, administrators and email changes need the LAN/.test(text),
    `the card does not say what stays on the LAN: "${text}"`);
  assert(!/admin access/.test(text), "an admin was told they need admin access");
});

await testAsync("PEOPLE: a step_up_required refusal opens SIGN IN AGAIN", async () => {
  usersScenario.patch = "step_up";
  asks.length = 0;
  await changeGuestRole();
  assert(writes().some((w) => w.method === "PATCH" && w.url.includes("/api/users/u2")),
    "the change never reached the rig");
  eq(stepUpState(), "required", "the refusal did not open the SIGN IN AGAIN form");
  const card = String(q("users-stepup").textContent);
  assert(/SIGN IN AGAIN/.test(card), `the block does not say SIGN IN AGAIN: "${card}"`);
  assert(/more than 5 minutes old/.test(card), `the block does not say why: "${card}"`);
  assert(q("users-stepup-google") != null, "no Google sign-in offered although Google is enabled");
  assert(q("users-stepup-password") != null, "no local sign-in offered although local is enabled");
  eq(String(q("users-error").textContent), "Sign in again to manage people from outside the LAN.",
    "the failed change did not say what happened");
});

await testAsync("PEOPLE: a local sign-in posts /auth/local and closes the form", async () => {
  usersScenario.patch = "ok";
  asks.length = 0;
  await typeInto(q("users-stepup-username"), "bear");
  await typeInto(q("users-stepup-password"), "correct-horse");
  await pressAndWait(q("users-stepup-submit"));
  const post = asks.find((a) => a.method === "POST" && a.url.includes("/auth/local"));
  assert(post != null, "the sign-in never reached /auth/local");
  eq(JSON.parse(post!.body ?? "{}").username, "bear", "the typed username was not sent");
  eq(JSON.parse(post!.body ?? "{}").password, "correct-horse", "the typed password was not sent");
  eq(stepUpState(), "fresh", "a successful sign-in did not leave the form");
  // The change the rig refused is sent again by itself (#734): one PATCH, the
  // same one, to the same person.
  const resent = writes().filter((w) => w.method === "PATCH" && w.url.includes("/api/users/u2"));
  eq(resent.length, 1, "the refused change was not sent again after the sign-in");
  eq(JSON.parse(resent[0].body ?? "{}").role, "operator", "the retry sent a different change");
  assert(q("users-error") == null, "the refusal's sentence is still on the page after the retry");
  assert(q("users-stepup-password") == null, "the password field is still on the page");
  assert(/open for 5 minutes/.test(String(q("users-stepup").textContent)),
    "the card does not say how long the sign-in lasts");
});

await testAsync("PEOPLE: a capability refusal does not open SIGN IN AGAIN", async () => {
  // A 403 with no `code` is a role the caller lacks. Signing in again cannot
  // help them, and offering it is the wrong-blocker defect the fence work exists
  // to prevent.
  await mount(createElement(UsersEditor));
  usersScenario.patch = "capability";
  await changeGuestRole();
  eq(stepUpState(), "idle", "a capability refusal opened the SIGN IN AGAIN form");
  eq(String(q("users-error").textContent), "capability not held",
    "the capability refusal lost its sentence");
});

await testAsync("PEOPLE: a refusal that names its rule keeps its sentence", async () => {
  await mount(createElement(UsersEditor));
  usersScenario.patch = "lan_reason";
  await changeGuestRole();
  eq(String(q("users-error").textContent),
    "This change needs the LAN: an administrator can only be changed at the rig",
    "the rule the rig named was replaced by a generic sentence");
  eq(stepUpState(), "idle", "a local_only refusal opened SIGN IN AGAIN");
  // ... while the fence middleware's generic refusal still gets the one LAN sentence.
  await mount(createElement(UsersEditor));
  usersScenario.patch = "fence";
  await changeGuestRole();
  eq(String(q("users-error").textContent), LOCAL_ONLY_REASON,
    "the fence's generic refusal did not get the shared LAN sentence");
});

// ------------------------------------- PEOPLE: per-control locks and the retry
// (#734, #731; wave 15 integration of WP-105.) The rig refuses a password reset
// and any role but viewer and operator over the relay, so those controls are
// locked with the LAN sentence before anything is pressed, and every other
// control on the same row stays armed. Both halves are asserted on ONE mount, so
// neither can pass by the other being broken.

const optionOf = (scope: any, group: string, value: string) =>
  scope.querySelector(`[data-testid="${group}"] [data-value="${value}"]`) as any;

await testAsync("PEOPLE: over the relay RESET and the admin and syncer role options are locked with the LAN sentence, and the rest of the row stays armed", async () => {
  usersScenario.patch = "ok";
  await mount(createElement(UsersEditor));
  const rows = container.querySelectorAll('[data-testid="users-row"]');
  eq(rows.length, 2, "the list did not render both people");
  const guest = rows[1];
  const reset = guest.querySelector('[data-testid="users-row-reset"]');
  assert(reset != null, "no RESET on the row - nothing may be hidden");
  eq(reset.getAttribute("aria-disabled"), "true",
    "RESET renders ARMED over the relay - the rig refuses every password reset there");
  eq(reset.getAttribute("title"), LOCAL_ONLY_REASON, "RESET names the wrong blocker");
  for (const role of ["admin", "syncer"]) {
    const opt = optionOf(guest, "users-row-role", role);
    assert(opt != null, `the role control has no ${role.toUpperCase()} option - nothing may be hidden`);
    eq(opt.getAttribute("aria-disabled"), "true",
      `the ${role.toUpperCase()} option renders ARMED over the relay - the rig refuses it`);
    eq(opt.getAttribute("title"), LOCAL_ONLY_REASON, `the ${role.toUpperCase()} option names the wrong blocker`);
  }
  // THE OTHER CONTROLS ON THE SAME ROW STAY ARMED.
  const armed: Array<[string, any]> = [
    ["the OPERATOR option", optionOf(guest, "users-row-role", "operator")],
    ["the VIEWER option", optionOf(guest, "users-row-role", "viewer")],
    ["the enabled switch", guest.querySelector('[data-testid="users-row-enabled"]')],
    ["DELETE", guest.querySelector('[data-testid="users-row-delete"]')],
  ];
  for (const [what, el] of armed) {
    assert(el != null, `${what} is not on the row`);
    eq(el.getAttribute("aria-disabled"), null,
      `${what} is locked over the relay, but the rig does it behind a recent sign-in`);
  }
  // Pressing a locked one explains and sends nothing; pressing an armed one sends.
  asks.length = 0;
  await act(async () => { useStore.setState({ toasts: [] } as never); });
  await click(optionOf(guest, "users-row-role", "admin"));
  assert(toastTitles().includes(LOCAL_ONLY_REASON),
    `the locked ADMIN option refused in silence: ${JSON.stringify(toastTitles())}`);
  await click(reset);
  eq(writes().length, 0, `a locked control reached the rig: ${JSON.stringify(writes())}`);
  assert(guest.querySelector('[data-testid="users-reset-form"]') == null,
    "a locked RESET opened its password form, so a password could be typed for the relay");
  await pressAndWait(optionOf(guest, "users-row-role", "operator"));
  assert(writes().some((w) => w.method === "PATCH" && w.url.includes("/api/users/u2")),
    "the armed OPERATOR option sent nothing");
});

await testAsync("PEOPLE: on the LAN the same row locks nothing for the LAN's sake", async () => {
  usersScenario.patch = "ok";
  act(() => { noteRemoteStatus({ via: "direct" }); });
  try {
    await mount(createElement(UsersEditor));
    const guest = container.querySelectorAll('[data-testid="users-row"]')[1];
    const lan: Array<[string, any]> = [
      ["RESET", guest.querySelector('[data-testid="users-row-reset"]')],
      ["the ADMIN option", optionOf(guest, "users-row-role", "admin")],
      ["the SYNCER option", optionOf(guest, "users-row-role", "syncer")],
    ];
    for (const [what, el] of lan) {
      assert(el != null, `${what} is not on the row`);
      eq(el.getAttribute("aria-disabled"), null,
        `${what} is locked on the LAN, where the rig does it`);
    }
  } finally {
    act(() => { noteRemoteStatus({ via: "relay" }); });
  }
});

await testAsync("PEOPLE: over the relay the add form locks the admin and syncer roles and nothing else", async () => {
  usersScenario.create = "ok";
  await mount(createElement(UsersEditor));
  await click(q("users-add"));
  const form = q("users-add-form");
  assert(form != null, "ADD USER did not open its form");
  for (const role of ["admin", "syncer"]) {
    const opt = optionOf(form, "users-add-role", role);
    assert(opt != null, `the add form has no ${role.toUpperCase()} option - nothing may be hidden`);
    eq(opt.getAttribute("aria-disabled"), "true",
      `the add form's ${role.toUpperCase()} option renders ARMED over the relay - the rig refuses it`);
    eq(opt.getAttribute("title"), LOCAL_ONLY_REASON,
      `the add form's ${role.toUpperCase()} option names the wrong blocker`);
  }
  for (const role of ["viewer", "operator"]) {
    eq(optionOf(form, "users-add-role", role).getAttribute("aria-disabled"), null,
      `the add form's ${role.toUpperCase()} option is locked over the relay, but the rig allows it`);
  }
  eq(q("users-add-username").getAttribute("aria-disabled"), null, "the username field is locked");
  eq(optionOf(form, "users-add-method", "google").getAttribute("aria-disabled"), null,
    "the Google-only method is locked over the relay, where it is the one method the rig allows");
});

await testAsync("PEOPLE: a change the rig refused for want of a sign-in is sent again once a password sign-in lands", async () => {
  // The sequence the person lives: change a role, be asked to sign in again,
  // sign in, and find the change made. The earlier cases cover the form and the
  // sentence; this one is the retry on its own, on a fresh mount.
  usersScenario.patch = "step_up";
  await mount(createElement(UsersEditor));
  asks.length = 0;
  await changeGuestRole();
  eq(stepUpState(), "required", "the refusal did not open the SIGN IN AGAIN form");
  assert(/sends the change again for you/.test(String(q("users-stepup-why").textContent)),
    `the hint does not say the change is held and will be sent again: "${q("users-stepup-why")?.textContent}"`);
  eq(writes().filter((w) => w.method === "PATCH").length, 1, "premise: one refused PATCH");
  usersScenario.patch = "ok";
  await typeInto(q("users-stepup-username"), "bear");
  await typeInto(q("users-stepup-password"), "correct-horse");
  await pressAndWait(q("users-stepup-submit"));
  const patches = writes().filter((w) => w.method === "PATCH" && w.url.includes("/api/users/u2"));
  eq(patches.length, 2, "the refused change was not sent again after the sign-in");
  eq(patches[1].body, patches[0].body, "the retry sent a different change");
  eq(stepUpState(), "fresh", "the sign-in did not land");
  // ONCE: a held change is not sent again by anything that follows.
  await settle();
  eq(writes().filter((w) => w.method === "PATCH").length, 2,
    "the held change was sent a second time");
});

await testAsync("PEOPLE: nothing held means a sign-in sends nothing but the sign-in", async () => {
  usersScenario.patch = "ok";
  await mount(createElement(UsersEditor));
  await pressAndWait(q("users-stepup-open"));
  eq(stepUpState(), "required", "SIGN IN AGAIN did not open the form");
  assert(!/sends the change again for you/.test(String(q("users-stepup-why").textContent)),
    "the form promised to send a change that nobody had held");
  asks.length = 0;
  await typeInto(q("users-stepup-username"), "bear");
  await typeInto(q("users-stepup-password"), "correct-horse");
  await pressAndWait(q("users-stepup-submit"));
  eq(stepUpState(), "fresh", "the sign-in did not land");
  eq(writes().filter((w) => !w.url.includes("/auth/local")).length, 0,
    `a sign-in with nothing held sent a change: ${JSON.stringify(writes())}`);
});

await testAsync("PEOPLE: the same holds for ADD USER, and a held create is not sent from a form that has been emptied", async () => {
  const posts = () => writes().filter((w) => w.method === "POST" && w.url.endsWith("/api/users"));
  const fillAndCreate = async () => {
    await click(q("users-add"));
    await typeInto(q("users-add-username"), "new@rig");
    await typeInto(q("users-add-email"), "new@rig.example");
    await click(optionOf(q("users-add-form"), "users-add-method", "google"));
    asks.length = 0;
    await pressAndWait(q("users-create"));
  };

  usersScenario.create = "step_up";
  await mount(createElement(UsersEditor));
  await fillAndCreate();
  eq(posts().length, 1, "premise: CREATE reached the rig once");
  eq(stepUpState(), "required", "a refused create did not open the SIGN IN AGAIN form");
  usersScenario.create = "ok";
  await typeInto(q("users-stepup-username"), "bear");
  await typeInto(q("users-stepup-password"), "correct-horse");
  await pressAndWait(q("users-stepup-submit"));
  eq(posts().length, 2, "the refused create was not sent again after the sign-in");
  eq(posts()[1].body, posts()[0].body, "the retry sent a different account");

  // And the guard: the form was emptied between the refusal and the sign-in, so
  // there is nothing to send.
  usersScenario.create = "step_up";
  await mount(createElement(UsersEditor));
  await fillAndCreate();
  eq(posts().length, 1, "premise: the second CREATE reached the rig once");
  await typeInto(q("users-add-username"), "");
  usersScenario.create = "ok";
  await typeInto(q("users-stepup-username"), "bear");
  await typeInto(q("users-stepup-password"), "correct-horse");
  await pressAndWait(q("users-stepup-submit"));
  eq(posts().length, 1, "a held create was sent from a form that had been emptied");
});

await testAsync("PEOPLE: without admin.users the list is not requested and there is no sign-in card", async () => {
  usersScenario.patch = "ok";
  await act(async () => { root.render(null); });
  await settle();
  seed();
  useStore.setState({
    principal: { role: "viewer", email: "view@rig", caps: ["view.status", "view.preview"] },
  } as never);
  asks.length = 0;
  await act(async () => { root.render(createElement(UsersEditor)); });
  await settle();
  eq(usersGets().length, 0, "a viewer asked the rig for the list");
  assert(q("users-hidden") != null, "the viewer's list region is missing");
  eq(q("users-stepup"), null, "a viewer was offered a sign-in for a change it cannot make");
});

// Inside `act`: the viewer's editor above is still mounted and subscribes to this.
await act(async () => { useStore.setState({ authMethods: null } as never); });

// ================================================== PEOPLE > SIGN-IN METHODS
// WITH NO METHOD ENABLED, which is the one state the guided setup card appears
// in - and the card is what reads `GET /api/users`. Mounted on the shipped
// fixture the read never runs at all, so the "asks for no users" assertion would
// be true of a component that had never heard of the fence.
await mount(createElement(AuthMethodsEditor), { ...CONFIG, auth: { ...CONFIG.auth, methods: [] } });

await testAsync("SIGN-IN METHODS: SAVE METHODS refuses, and the guided card asks for no users", async () => {
  assert(q("auth-setup-card") != null,
    "the guided setup card is not on the page, so the user-list read under test "
    + "never had a chance to fire - this assertion would be vacuous");
  assert(q("auth-editor") != null, "the sign-in methods editor never rendered");
  eq(asks.filter((a) => a.url.includes("/api/users")).length, 0,
    "the guided card read the user list over the relay");
  await refuses("auth-save", "POST /api/auth/config");
});

// ======================================================= MORE > CALIBRATION
await mount(createElement(CalibrationTolerancesEditor));

await testAsync("CALIBRATION TOLERANCES: SAVE and RESET refuse (POST /api/config/calibration)", async () => {
  assert(q("cal-tolerances") != null, "the tolerances editor never rendered");
  await refuses("cal-save", "POST /api/config/calibration");
  await refuses("cal-reset", "POST /api/config/calibration");
});

// ================================================== MORE > SKY ATLAS PACK
await mount(createElement(SkyPackEditor));

await testAsync("SKY PACK: the online switch, DOWNLOAD and DELETE all refuse", async () => {
  assert(q("pack-card") != null, "the sky-pack editor never rendered");
  await refuses("pack-online", "POST /api/config/survey");
  await refuses("pack-download", "POST /api/survey/pack/fetch");
  await refuses("pack-delete", "DELETE /api/survey/pack");
});

// ========================================================== MORE > FILE SYNC
await mount(createElement(SyncEditor));

await testAsync("FILE SYNC: the enable switch and PUSH NOW refuse", async () => {
  assert(q("sync-editor") != null, "the sync editor never rendered");
  await refuses("sync-enabled", "POST /api/config/sync");
  await refuses("sync-push-now", "POST /api/sync/push/now");
});

// ======================================================== MORE > FILE NAMING
await mount(createElement(NamingEditor));

await testAsync("FILE NAMING: SAVE refuses (POST /api/config/naming)", async () => {
  assert(q("naming-editor") != null, "the naming editor never rendered");
  await refuses("naming-save", "POST /api/config/naming");
});

// ================================================= MORE > IMAGING STANDARDS
await mount(createElement(StandardsEditor));

await testAsync("IMAGING STANDARDS: the filter-offsets switch refuses (POST /api/config)", async () => {
  assert(q("standards-editor") != null, "the standards editor never rendered");
  await refuses("standards-offsets", "POST /api/config {standards}");
});

// ================================================ MORE > PLATE-SOLVE STAMP
await mount(createElement(WcsStampEditor));

await testAsync("PLATE-SOLVE STAMP: the master switch refuses (POST /api/config/wcs)", async () => {
  assert(q("wcs-editor") != null, "the plate-solve editor never rendered");
  await refuses("wcs-enabled", "POST /api/config/wcs");
});

// ============================================================= MORE > SAFETY
await mount(createElement(SafetyTuningPanel));

await testAsync("SAFETY: the preset picker refuses (POST /api/config {safety})", async () => {
  assert(q("safety-preset-card") != null, "the safety tuning panel never rendered");
  await refuses("safety-preset-seg", "POST /api/config {safety}", '[data-value="remote"]');
});

test("SAFETY: the folded two-capability note prints the relay sentence whole", () => {
  const note = q("safety-tuning-lock");
  assert(note != null, "no read-only note on a panel where neither block can be written");
  assert(String(note.textContent).includes(LOCAL_ONLY_REASON),
    `the folded sentence mangled the relay reason: "${note.textContent}"`);
  assert(!/changing the safety preset \(config\.safety\) This changes/.test(String(note.textContent)),
    "a whole sentence was glued behind a clause - the line does not parse");
});

await testAsync("SAFETY: the escalation rows below refuse too (POST /api/config {escalation})", async () => {
  assert(q("escalation-editor") != null, "the escalation editor never rendered");
  await refuses("escalation-row-cooling", "POST /api/config {escalation}");
});

// ========================================================= SKY > EPHEMERIS
// `/api/ephemeris` is on the mutation fence because the refresh makes the RIG
// dial out to CelesTrak and the MPC. The status GET stays open, so the card
// still reads - and that is asserted, because a card that refused to read would
// pass the lock assertion for the wrong reason.
await mount(createElement(EphemerisCard));

await testAsync("EPHEMERIS: REFRESH ELEMENTS refuses, and the status GET still happened", async () => {
  assert(q("settings-ephemeris") != null, "the ephemeris card never rendered");
  assert(q("ephemeris-row-satellites") != null,
    "the card never read the status - GET /api/ephemeris/status is NOT fenced and "
    + "over-gating it would hide how old the elements are");
  await refuses("ephemeris-refresh", "POST /api/ephemeris/refresh");
});

// ===================================================== the vacuity guard
// One editor on the SAME origin, in the SAME mount, whose route is deliberately
// NOT on the fence: `POST /api/calibration/build` builds masters from frames
// already on this box's disk, which is the science rather than the policy the
// fence protects. If REBUILD locked here, every assertion above would be
// "the relay flag locks everything" rather than "it locks what the rig refuses".
await mount(createElement(CalibrationLibraryEditor));

test("VACUITY GUARD: a route that is NOT fenced stays pressable on the same relay origin", () => {
  const build = q("cal-build");
  assert(build != null, "the master-library editor never rendered");
  eq(build.getAttribute("aria-disabled"), null,
    "REBUILD LIBRARY is locked over the relay, but POST /api/calibration/build is not "
    + "on the rig's fence - this would make every lock assertion in this file vacuous");
});

await act(async () => { root.render(null); });
resetRelayForTests();

// =================================================================== summary
const total = passed + failed;
console.log(`relayFenceDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
