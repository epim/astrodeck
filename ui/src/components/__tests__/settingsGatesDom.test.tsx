// settingsGatesDom.test.tsx — three Settings controls that must track the RIG
// rather than the page load, MOUNTED and driven over time.
//
//   Run directly:  npx tsx src/components/__tests__/settingsGatesDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Every claim here is about state CHANGING under a mounted component, which is
// why this is a jsdom file and not a renderToStaticMarkup one: a snapshot of
// the first paint cannot tell a gate that tracks the rig from a gate that was
// photographed at mount and never looked again. The three:
//
//   * UpdatePanel — the `update` WS event carries `update_state.snapshot()`,
//     which does NOT include can_apply / apply_blocked_reason / supervised (the
//     route stamps those on), and the store replaces the whole slice. So a
//     phase frame used to re-arm "Upgrade" mid-sequence and delete both the
//     blocked reason and the unsupervised warning.
//   * UpdatePanel — "Upgrade" POSTs to a `_spawn` route that returns the moment
//     the task is created, so the button has to follow the `system.update` lane,
//     not the promise.
//   * FactoryResetPanel — its rig-idle gate came from a GET issued once at
//     mount: a sequence that had since ENDED still refused the reset, and one
//     that had since STARTED reached a 409 on press.
//
// The globals are planted before the store or any component is imported (the
// api client reads window.location at module scope). run-tests.mjs gives this
// file its own process, so that cannot leak into another suite.

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="u"></div><div id="f"></div><div id="a"></div>` +
    `<div id="s"></div><div id="p"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true },
);
const win = dom.window as any;

// SitePanel gates "Use my location" on a secure context and a geolocation API;
// jsdom offers neither. The stub HOLDS the callback so a test can decide when
// the fix arrives — which is the whole point of the finding (it can arrive ten
// seconds late, on top of coordinates typed since).
Object.defineProperty(win, "isSecureContext", { value: true, configurable: true });
let geoResolve: ((lat: number, lon: number) => void) | null = null;
Object.defineProperty(win.navigator, "geolocation", {
  configurable: true,
  value: {
    getCurrentPosition(onOk: any) {
      geoResolve = (latitude, longitude) =>
        onOk({ coords: { latitude, longitude, altitude: null } });
    },
  },
});

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "localStorage", "sessionStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- network
// A stub rather than a real server, but a HONEST one: every request is recorded
// and answered by path, so a test can assert what the panel asked for.
//
// /api/update/status deliberately FAILS. The panel re-reads it when a partial
// frame lands, and if that read succeeded the store would be repaired from the
// network — which would make the WS-clobber test below pass whether or not the
// component remembers anything. A failing route is the only way the assertion
// is about the component.
const PREVIEW = {
  site_is_default: false, profiles: 2, plans: 3, drivers: 4, alert_sinks: 1,
  users: 2, captures: { frames: 12, entries: 3, bytes: 4096 },
  preserved_capture_entries: ["logs"],
  // Measured DURING a run and never re-measured: the frozen gate this panel
  // used to be stuck behind for the rest of the visit.
  can_reset: false, blocked_reason: "a sequence is running",
};
const USERS = [{
  id: "u1", username: "nightowl", email: "owl@example.test",
  role: "operator", enabled: true, created: 1_750_000_000,
}];
/** Flipped by the password-reset tests: the server can refuse a reset with a
 *  200-shaped UI on either side of it, which is the whole finding. */
let pwWorks = false;
const asked: string[] = [];
const ok = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});
/** Set to a promise to make the NEXT matching write hang, so a test can look at
 *  the button while the request is genuinely in flight. */
let hold: { path: string; release: () => void; gate: Promise<void> } | null = null;
g.fetch = async (url: string, init?: { method?: string }) => {
  const u = String(url);
  const method = init?.method ?? "GET";
  asked.push(`${method} ${u}`);
  if (hold && u.includes(hold.path)) await hold.gate;
  if (u.includes("/api/system/factory-reset")) return ok(PREVIEW);
  if (u.includes("/api/update/status")) {
    return { ok: false, status: 503, statusText: "unavailable", json: async () => ({}) };
  }
  // The read-back route, answered with the coordinates it was ASKED about — the
  // only way to tell "the sentence for these fields" from "the sentence for the
  // fields as they were two keystrokes ago".
  const sky = /\/api\/site\/sky\?lat=([-\d.]+)&lon=([-\d.]+)/.exec(u);
  if (sky) return ok({ place_hint: `HINT(${sky[1]},${sky[2]})`, sun_alt_deg: -20 });
  if (u.includes("/password")) {
    return pwWorks
      ? ok({})
      : { ok: false, status: 500, statusText: "boom",
          json: async () => ({ detail: "the password file is read-only" }) };
  }
  if (u.includes("/api/users")) return ok(method === "GET" ? { users: USERS } : {});
  // GET is the library; POST mints one and returns the created row.
  if (u.includes("/api/locations")) {
    return ok(method === "GET"
      ? []
      : { id: "loc1", name: "Ridge", latitude: 40.5, longitude: 70.25, elevation_m: 100, horizon_min_deg: null });
  }
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const UpdatePanel = (await import("../settings/UpdatePanel")).default;
const FactoryResetPanel = (await import("../settings/FactoryResetPanel")).default;
const { AddUserForm } = await import("../settings/UsersPanel");
const UsersPanel = (await import("../settings/UsersPanel")).default;
const SitePanel = (await import("../settings/SitePanel")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** Let effects, fetches and their .then chains settle. */
const settle = async () => {
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const ADMIN = {
  role: "admin", email: "admin@example.test",
  caps: ["view.status", "system.update", "admin.users", "config.site_optics"],
};

/** GET /api/update/status as the route builds it: the snapshot PLUS the three
 *  gate fields it stamps on. */
const UPDATE_FROM_ROUTE = {
  current: "0.2.6", latest: "0.2.7", update_available: true, phase: "idle",
  progress: 0, channel: "stable", last_check_ts: 1_750_000_000,
  notes_md: "", error: null, last_result: null,
  supervised: false, can_apply: false,
  apply_blocked_reason: "no signing public key pinned (cannot verify a release)",
};
/** The `update` WS event: `update_state.snapshot()`, gate fields ABSENT. */
const UPDATE_FROM_WS = {
  current: "0.2.6", latest: "0.2.7", update_available: true, phase: "idle",
  progress: 0, channel: "stable", last_check_ts: 1_750_000_000,
  notes_md: "", error: null, last_result: null,
};

const seed = (over: Record<string, unknown> = {}) => {
  useStore.setState({
    principal: ADMIN,
    status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
    sequence: { state: "idle" },
    config: {
      version: 1,
      update: {
        enabled: true, auto_check: false, check_interval_hours: 24,
        channel: "stable", repo: "epim/astrodeck", signing_pubkey: "",
        health_timeout_s: 60, last_check_ts: null,
      },
    },
    update: UPDATE_FROM_ROUTE,
    ...over,
  } as never);
};

// ===================================================================== UPDATE
seed();
const uHost = win.document.getElementById("u") as any;
const uRoot = createRoot(uHost);
await act(async () => { uRoot.render(createElement(UpdatePanel)); });
await settle();

const upgradeBtn = (): any =>
  [...uHost.querySelectorAll("button")].find((b: any) =>
    /upgrade|starting|updating/i.test(b.textContent || ""));

await test("UpdatePanel mounted with the gate CLOSED — the precondition", () => {
  // Without this, every "still blocked" assertion below could pass on a panel
  // that never rendered an Upgrade button at all.
  assert(upgradeBtn() != null, "no Upgrade button rendered — the fixture is wrong, not the component");
  assert(upgradeBtn().disabled === true, "Upgrade is live even though the server said can_apply:false");
  assert(/no signing public key pinned/.test(uHost.textContent),
    "the blocked reason from the route is not on screen");
  assert(/Not running under the supervisor/.test(uHost.textContent),
    "the unsupervised warning from the route is not on screen");
});

await test("a partial `update` WS frame does not re-arm Upgrade", async () => {
  // THE HAZARD. This is exactly what ws.ts does with the event: replace the
  // slice with update_state.snapshot(), which carries no gate fields.
  await act(async () => { useStore.setState({ update: UPDATE_FROM_WS } as never); });
  await settle();
  assert(upgradeBtn().disabled === true,
    "a WS phase frame re-armed Upgrade — the panel forgot a gate the server never withdrew, " +
    "and the next press starts an update the server will 409 (or worse, allow mid-sequence)");
  assert(/no signing public key pinned/.test(uHost.textContent),
    "the blocked-reason line was deleted by a WS frame that says nothing about the gate");
  assert(/Not running under the supervisor/.test(uHost.textContent),
    "the unsupervised warning was deleted by a WS frame that says nothing about supervision");
});

await test("the partial frame made the panel re-ask the route that computes the gate", () => {
  assert(asked.filter((u) => u.includes("GET /api/update/status")).length >= 2,
    "the panel never re-read /api/update/status after a frame arrived without the gate, " +
    "so its remembered answer could only ever get older");
});

await test("a live sequence closes the gate even when the snapshot says can_apply", async () => {
  await act(async () => {
    useStore.setState({
      update: { ...UPDATE_FROM_ROUTE, supervised: true, can_apply: true, apply_blocked_reason: "" },
      sequence: { state: "running" },
    } as never);
  });
  await settle();
  assert(upgradeBtn().disabled === true,
    "Upgrade is live while a sequence is running — the panel is quoting a photograph of the gate");
  assert(/Can't install now: a sequence is running/.test(uHost.textContent),
    "the sequence is not named as the reason: " + uHost.textContent.slice(0, 400));
});

await test("with the rig idle and the server happy, Upgrade is live again", async () => {
  // The other direction, and the reason a stale block can't just be latched:
  // this is the assertion a permanently-disabled button would fail.
  await act(async () => { useStore.setState({ sequence: { state: "idle" } } as never); });
  await settle();
  assert(upgradeBtn().disabled === false,
    "Upgrade stayed disabled after the sequence ended — the gate now sticks the other way");
  assert(/^\s*Upgrade\s*$/.test(upgradeBtn().textContent || ""),
    `Upgrade should read "Upgrade" when idle, reads "${upgradeBtn().textContent}"`);
});

await test("the `system.update` lane holds Upgrade down without any local flag", async () => {
  // #69: the apply route `_spawn`s and returns immediately, so the promise is
  // not the operation. This is the server's own answer, arriving 2s later.
  await act(async () => {
    useStore.setState({
      status: {
        connected: {}, looping: false, mode: "sim",
        busy: null, busy_lanes: ["system.update"],
      },
    } as never);
  });
  await settle();
  assert(upgradeBtn().disabled === true,
    "Upgrade is pressable while the server reports the system.update lane in flight");
  assert(/Starting…/.test(upgradeBtn().textContent || ""),
    `the button does not say what it is doing: "${upgradeBtn().textContent}"`);
});

await act(async () => { uRoot.unmount(); });

// ============================================================== FACTORY RESET
seed();
const fHost = win.document.getElementById("f") as any;
const fRoot = createRoot(fHost);
await act(async () => { fRoot.render(createElement(FactoryResetPanel)); });
await settle();

await test("FactoryResetPanel loaded its MEASURED scope — the precondition", () => {
  // If the preview GET had failed, `blocked` would be the load error and every
  // gate assertion below would be about the wrong string.
  assert(/2 saved profiles/.test(fHost.textContent),
    "the preview did not load, so the reason on screen is the load error, not the gate");
  assert(!/counting…/.test(fHost.textContent), "still counting — the fixture never settled");
});

await test("a gate that was closed WHEN MEASURED does not outlive the run", () => {
  // PREVIEW.can_reset is false with blocked_reason "a sequence is running" —
  // measured during a run that has since finished. The live store says idle.
  assert(!/a sequence is running/.test(fHost.textContent),
    "the panel is still refusing on a sequence that ended before this render — " +
    "its gate is the mount-time GET, not the rig");
  assert(/Type RESET/.test(fHost.textContent),
    "the remaining reason should be the typed-word interlock: " + fHost.textContent.slice(0, 400));
});

await test("a sequence that starts NOW closes the gate, with an article that reads", async () => {
  await act(async () => { useStore.setState({ sequence: { state: "running" } } as never); });
  await settle();
  assert(/Can't reset while a sequence is running\./.test(fHost.textContent),
    "a running sequence does not reach the reset gate: " + fHost.textContent.slice(0, 400));
  assert(!/while the a sequence/.test(fHost.textContent),
    'the double article is back: "Not while the a sequence is running."');
});

await test("a busy rig closes it too, and that one DOES want the article", async () => {
  await act(async () => {
    useStore.setState({
      sequence: { state: "idle" },
      status: { connected: {}, looping: false, mode: "sim", busy: "capturing", busy_lanes: ["capture"] },
    } as never);
  });
  await settle();
  assert(/Can't reset while the rig is capturing\./.test(fHost.textContent),
    "a capturing rig is not named at the gate: " + fHost.textContent.slice(0, 400));
});

await test("and it opens again when the rig goes quiet", async () => {
  await act(async () => {
    useStore.setState({
      status: { connected: {}, looping: false, mode: "sim", busy: null, busy_lanes: [] },
    } as never);
  });
  await settle();
  assert(!/Can't reset while/.test(fHost.textContent),
    "the gate stayed shut after the rig went idle — it now sticks the other way");
});

await act(async () => { fRoot.unmount(); });

// ============================================================== ADD USER FORM
const aHost = win.document.getElementById("a") as any;
const aRoot = createRoot(aHost);
let createdCalls = 0;
await act(async () => {
  aRoot.render(createElement(AddUserForm, {
    // The guided "Secure this server" card's call site: it keeps the form
    // MOUNTED unless the new account is an enabled admin, which is what made
    // the missing busy-clear visible in the first place.
    onCreated: async () => { createdCalls++; },
    defaultRole: "operator",
  }));
});

const field = (label: string): any => {
  const spans = [...aHost.querySelectorAll("span.label")];
  const s: any = spans.find((x: any) => (x.textContent || "").trim() === label);
  return s?.parentElement?.querySelector("input, select");
};
const type = (el: any, value: string) => {
  const proto = el.tagName === "SELECT" ? win.HTMLSelectElement : win.HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new win.Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
};
const submitBtn = (): any =>
  [...aHost.querySelectorAll("button")].find((b: any) => /create|creating/i.test(b.textContent || ""));

await test("the add-user form is fillable — the precondition", async () => {
  assert(field("Username") != null && field("Email") != null && field("Password") != null,
    "the form did not render its fields");
  await act(async () => {
    type(field("Username"), "nightowl");
    type(field("Email"), "owl@example.test");
    type(field("Password"), "hunter2hunter2");
  });
  assert(field("Username").value === "nightowl", "the value setter did not reach React state");
  assert(submitBtn().textContent.includes("Create user"), "the button is not in its resting state");
});

await test("a successful create leaves the form usable instead of frozen", async () => {
  await act(async () => {
    aHost.querySelector("form").dispatchEvent(
      new win.Event("submit", { bubbles: true, cancelable: true }));
  });
  await settle();
  assert(createdCalls === 1, `onCreated ran ${createdCalls} times — the submit never went through`);
  assert(!/Creating…/.test(submitBtn().textContent || ""),
    "the button is still stuck on 'Creating…' after the account was created — " +
    "on the guided setup card, which keeps this form mounted, the whole form is frozen");
  assert(submitBtn().disabled === false, "the submit button never re-enabled");
  assert(field("Username").disabled === false, "the fields are still disabled");
  assert(/Created "nightowl" as operator/.test(aHost.textContent),
    "nothing on screen says what was created — and the ROLE is exactly what decides " +
    "whether the guided card's step 1 is finished");
});

await act(async () => { aRoot.unmount(); });

// ======================================================================= SITE
// The read-back ("These coordinates point at …") is the panel's sign-flip
// detector, and the save toast repeats it. Both used to describe whatever the
// last completed lookup had been about, which — across a 350ms debounce plus a
// round trip — is the point you just corrected AWAY from.
useStore.setState({
  principal: {
    role: "admin", email: "admin@example.test",
    caps: ["view.status", "config.site_optics", "config.safety", "view.site_precise"],
  },
  config: {
    version: 1,
    site: {
      name: "Ridge", latitude: 40.5, longitude: -70.25, elevation_m: 100,
      is_default: false, horizon_min_deg: 15,
    },
  },
} as never);
const sHost = win.document.getElementById("s") as any;
const sRoot = createRoot(sHost);
await act(async () => { sRoot.render(createElement(SitePanel)); });
const wait = async (ms: number) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};
await wait(450);

/** The whole read-back block — the OUTERMOST match, since the inner label div
 *  is also a div whose entire text is the heading (which would read as an empty
 *  read-back and make every assertion below meaningless). */
const readback = (): string => {
  const d = ([...sHost.querySelectorAll("div")] as any[])
    .find((el) => /These coordinates point at/.test(el.textContent || ""));
  return d?.textContent || "";
};
const hemi = (group: string, label: string): any =>
  [...sHost.querySelectorAll(`[role="radiogroup"][aria-label^="${group}"] [role="radio"]`)]
    .find((b: any) => (b.textContent || "").trim() === label);
const click = (el: any) =>
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
const byAria = (label: string): any => sHost.querySelector(`[aria-label="${label}"]`);
const button = (re: RegExp): any =>
  [...sHost.querySelectorAll("button")].find((b: any) => re.test(b.textContent || ""));

await test("the site read-back describes the CURRENT fields — the precondition", () => {
  // Everything below is about this sentence going stale. If it never arrived,
  // "the stale one is gone" would be true for the wrong reason.
  assert(/HINT\(40.5,-70.25\)/.test(readback()),
    `the read-back never resolved for the seeded site: "${readback()}"`);
});

await test("flipping a hemisphere retires the old sentence instead of leaving it up", async () => {
  await act(async () => { click(hemi("Longitude", "E")); });
  const now = readback();
  assert(!/HINT\(40.5,-70.25\)/.test(now),
    "the read-back still describes the longitude you just corrected away from — " +
    "press Set site now and the toast confirms the wrong hemisphere too");
  assert(/checking…/.test(now), `no pending state on the read-back: "${now}"`);
});

await test("…and lands on the new one once the lookup returns", async () => {
  await wait(450);
  assert(/HINT\(40.5,70.25\)/.test(readback()),
    `the read-back never caught up with the flipped hemisphere: "${readback()}"`);
});

await test("a late browser fix does not overwrite coordinates changed since", async () => {
  await act(async () => { click(button(/Use my location/)); });
  assert(/Locating…/.test(button(/Locating|Use my location/)?.textContent || ""),
    "the geolocation button looks untouched while a 10s fix is in flight");
  assert(button(/Locating/)?.disabled === true, "the in-flight geolocation button is still pressable");

  // The user gives up waiting and types a latitude.
  const lat = byAria("Latitude magnitude (0–90)");
  await act(async () => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!
      .set!.call(lat, "12.5");
    lat.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  assert(byAria("Latitude magnitude (0–90)").value === "12.5", "the typed latitude never took");

  // NOW the fix arrives, describing somewhere else entirely.
  await act(async () => { geoResolve!(60, 20); });
  await wait(10);
  assert(byAria("Latitude magnitude (0–90)").value === "12.5",
    "a browser fix that resolved after the user typed overwrote what they typed");
  const toasts = JSON.stringify(useStore.getState().toasts);
  assert(/kept yours/.test(toasts),
    "nothing told the user their coordinates had won a race they never saw: " + toasts);
  assert(button(/Use my location/) != null, "the button never came back out of its busy state");
});

await test("…while a fix with nothing typed since still fills the form", async () => {
  // The other half. Without this, the guard above could be blocking everything
  // and the test would still pass.
  await act(async () => { click(button(/Use my location/)); });
  await act(async () => { geoResolve!(60, 20); });
  await wait(10);
  assert(byAria("Latitude magnitude (0–90)").value === "60.000000",
    `an uncontested browser fix did not reach the form: "${byAria("Latitude magnitude (0–90)").value}"`);
});

await test("the preset Save says it is saving, and cannot be double-fired", async () => {
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  hold = { path: "/api/locations", release, gate };
  await act(async () => { click(button(/Save as location preset/)); });
  const save = () => [...sHost.querySelectorAll("button")]
    .find((b: any) => /^(Save|Saving…)$/.test((b.textContent || "").trim()));
  assert(save() != null, "the inline name row did not open");
  await act(async () => { click(save()); });
  assert(/Saving…/.test(save().textContent || ""),
    "the preset Save gives no sign it is working — its `busy` guard was never set, " +
    "so a second tap mints the preset twice");
  assert(save().disabled === true, "the in-flight preset Save is still pressable");
  await act(async () => { release(); await new Promise((r) => setTimeout(r, 0)); });
  hold = null;
});

await act(async () => { sRoot.unmount(); });

// ============================================================ PASSWORD RESET
// A refused reset used to end exactly like an accepted one — field cleared, row
// collapsed — with the only sign of failure at the top of the panel, above
// everybody else's row.
const pHost = win.document.getElementById("p") as any;
const pRoot = createRoot(pHost);
await act(async () => { pRoot.render(createElement(UsersPanel)); });
await settle();

const pwField = (): any => pHost.querySelector('input[type="password"]');
const pBtn = (re: RegExp): any =>
  [...pHost.querySelectorAll("button")].find((b: any) => re.test(b.textContent || ""));

await test("the reset row opens on a real user — the precondition", async () => {
  assert(/nightowl/.test(pHost.textContent), "the user list never loaded");
  await act(async () => { click(pBtn(/Reset/)); });
  assert(pwField() != null, "the inline reset row did not open");
  await act(async () => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!
      .set!.call(pwField(), "hunter2hunter2");
    pwField().dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  assert(pwField().value === "hunter2hunter2", "the typed password never reached React state");
});

await test("a refused reset keeps the row, the password and the reason", async () => {
  await act(async () => { click(pBtn(/Set password/)); });
  await settle();
  assert(pwField() != null,
    "the reset row collapsed on FAILURE exactly as it does on success — nothing distinguishes " +
    "'that password is now live' from 'that password was never set'");
  assert(pwField().value === "hunter2hunter2", "the typed password was cleared by a failed reset");
  assert(/password is unchanged/.test(pHost.textContent),
    "the failure is not stated at the row it happened in: " + pHost.textContent.slice(0, 300));
});

await test("…and an accepted one closes the row and says so", async () => {
  pwWorks = true;
  await act(async () => { click(pBtn(/Set password/)); });
  await settle();
  assert(pwField() == null, "the row stayed open after a successful reset");
  // The toast queue caps and dedupes, so the claim is the MESSAGE, not a count.
  const toasts = JSON.stringify(useStore.getState().toasts);
  assert(/New password set for nightowl/.test(toasts),
    "a successful reset said nothing at all: " + toasts);
});

await act(async () => { pRoot.unmount(); });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`settingsGatesDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
