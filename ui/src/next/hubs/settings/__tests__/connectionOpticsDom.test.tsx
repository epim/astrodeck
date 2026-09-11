// connectionOpticsDom.test.tsx - the Connection and Optics sheets, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/connectionOpticsDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THESE TESTS ARE ABOUT. Both sheets make claims a user acts on and cannot
// check: "you are here", "the rig is using a profile's optics, not these
// values", "reachable in 12 ms, signed in as you". Six contracts get an
// assertion:
//
//   1. PRECONDITION MARKERS. Every later assertion is worthless if the sheet
//      never rendered, so each block opens by finding a marker and failing with
//      "the fixture is wrong, not the component".
//   2. THE WRITE PATH AND ITS BODY. SAVE must be `PUT /api/optics` carrying
//      `{optics, version}` - the optimistic-concurrency token is the whole
//      reason a 409 can be reported instead of clobbering someone.
//   3. THE READ-ONLY RENDERING. A viewer sees the same screen, with the
//      `config.site_optics` sentence, and ISSUES NOTHING.
//   4. THE ORIGIN DERIVATION. `/` and `/h/<home>/` must produce opposite cards.
//      jsdom's `reconfigure` moves the window between them inside one file.
//   5. NO INVENTED FACTS. The Connection sheet must contain no uptime and no
//      MB/s figure - the two the design draws and nothing on the wire carries.
//   6. THE OVERRIDE BANNER lists all seven optics keys, because a profile's
//      optics block is swapped WHOLE and the fields nobody was looking at are
//      exactly the ones that moved.
//
// Convention: jsdom by hand, createRoot + act, native events, a hand-written
// fetch recording into `asked`, printed tally plus the `{passed, failed, total}`
// export (shell-and-tests.md section 4).

/* eslint-disable @typescript-eslint/no-explicit-any */

// other area its own stylesheet. Node cannot load a stylesheet, so this
// synchronous hook answers with an empty module, exactly as
// `__tests__/shellDom.test.tsx` does for `NextApp`. It must be registered
// before the first `await import` that reaches one.
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
  { url: "http://local/#/settings/general/connection", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
// The Dial calls both behind a guard; jsdom implements neither.
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
// Patched BEFORE the copy loop, like settingsGatesDom does: the modules below
// close over these at import time.
win.isSecureContext = true;

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "DOMException",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
const asked: string[] = [];
let meAuthorised = true;
let putOptics: any = null;
let remotePayload: any = {
  enabled: true, home_id: "abc123", relay_host: "relay.astrodeck.app",
  connected: true, last_error: null, since_unix: 1_757_000_000, gen: 3, via: "direct",
};

// The rig's driver list, as `GET /api/drivers` answers it. The SOLVER row is
// derived from this (review #23), so the fixture is what proves the derivation:
// `nina-1` offers `solve` and must be offerable BY NAME; `phd2-1` is reachable
// but offers only `guide`; `astap-off` offers `solve` and is disabled. All three
// exist so the test can tell "listed everything" from "applied the rule".
let DRIVERS: any[] = [];
const driver = (over: Record<string, unknown>) => ({
  id: "x", type: "nina", label: "X", enabled: true, implicit: false,
  status: { reachable: true, error: null, detail: null, probed_at: 1 },
  offers: { devices: [], tasks: [] },
  ...over,
});
let clearedOverrides: any = null;

const ok = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});
const fail = (status: number) => ({
  ok: false, status, statusText: "no", json: async () => ({ detail: "no" }),
});

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  const method = String(init?.method ?? "GET").toUpperCase();
  asked.push(`${method} ${u}`);
  if (u.includes("/healthz")) return ok({ ok: true, version: "0.3.28" });
  if (u.includes("/api/me")) {
    return meAuthorised ? ok({ role: "admin", email: "bear@example.com", caps: [] }) : fail(401);
  }
  if (u.includes("/api/remote/status")) {
    return remotePayload ? ok(remotePayload) : fail(404);
  }
  if (u.includes("/api/optics") && method === "PUT") {
    putOptics = JSON.parse(String(init?.body ?? "null"));
    return ok({});
  }
  if (u.includes("/api/drivers")) return ok({ roles: [], drivers: DRIVERS });
  if (u.includes("/clear-overrides")) {
    clearedOverrides = JSON.parse(String(init?.body ?? "null"));
    return ok({});
  }
  if (u.includes("/api/survey/pack")) {
    return ok({ present: true, bytes: 262_144_000, order: 4, fetched_at: 1_756_000_000, fetching: null });
  }
  if (u.includes("/api/config")) return ok(CONFIG);
  return ok({});
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { ConnectionSheet } = await import("../sheets/ConnectionSheet");
const { OpticsSheet } = await import("../sheets/OpticsSheet");
const { CONN_PREF_KEY } = await import("../sheets/connectionModel");
// Imported so the QR assertion can compare the DRAWN path with the encoding of
// the address the card shows. Asserting "a path exists" would pass for a code
// that encodes something else entirely, which is the one failure that matters.
const { encodeQr, qrPath } = await import("../../../lib/qr");
const { OPTICS_AUX_KEY, OPTICS_MIGRATION_TOAST } = await import("../sheets/opticsModel");
const { migrationFlagKey } = await import("../../../lib/storageMigration");

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
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

const container = win.document.getElementById("root") as any;
// ONE root for the whole file: `createRoot` twice on one container warns, and a
// second root would leave the first tree subscribed to the store, so a later
// `setState` would land in a component nothing is asserting against.
const root = createRoot(container);
const render = async (el: any): Promise<void> => {
  await act(async () => { root.render(el); });
  await settle();
};
/** Unmount whatever is up. Store seeding has to happen with NOTHING mounted, or
 *  the update reaches a live subscriber outside `act` and React says so. */
const clearTree = async (): Promise<void> => {
  await act(async () => { root.render(null); });
  await settle();
};
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => Array.from(container.querySelectorAll(sel)) as any[];
const byId = (id: string) => q(`[data-testid="${id}"]`);
const text = () => String(container.textContent ?? "");

// ------------------------------------------------------------- the stylesheet
// jsdom loads no stylesheet and computes no layout, so the rules are read from
// disk instead: they are what the browser gets, so they are what is graded.
// Same shape as `shellCss.test.ts`'s guard on the header row.
const { readFileSync } = await import("node:fs");
const NEXT_CSS = readFileSync(new URL("../../../next.css", import.meta.url), "utf8");
/** The declaration block of ONE rule, by its exact selector. Throws when the
 *  selector is gone, which is the interesting half of the failure. */
function cssRule(css: string, selector: string): string {
  const at = css.indexOf(`${selector} {`);
  if (at < 0) throw new Error(`next.css has no rule for \`${selector}\``);
  const end = css.indexOf("}", at);
  return css.slice(at, end);
}
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};

// ------------------------------------------------------------------ fixtures
const ALL_CAPS = [
  "view.status", "view.preview", "view.media", "view.weather", "view.site_precise",
  "view.site_derived", "control.capture", "control.guide", "control.mount",
  "control.power", "config.backend", "config.safety", "config.solar_override",
  "config.site_optics", "config.alerts", "admin.users", "system.update",
];

const OPTICS = {
  focal_length_mm: 530,
  pixel_size_um: 3.76,
  sensor_width_px: 6248,
  sensor_height_px: 4176,
  auto_from_camera: true,
  guide_focal_length_mm: 200,
  telescope_name: "Askar FRA400",
  // D-SET-1: rig fields now. 0 is the unset convention, which is why the
  // primary fixture leaves aperture unset - "not invented until the user
  // says" is exactly the case that must survive the move to the server.
  aperture_mm: 0,
  reducer: 1,
};

const CONFIG: any = {
  version: 42,
  site: { is_default: false, horizon_min_deg: 25 },
  optics: OPTICS,
  optics_computed: {
    have_optics: true, source: "config",
    focal_length_mm: 530, pixel_size_um: 3.76,
    sensor_width_px: 6248, sensor_height_px: 4176,
    image_scale_arcsec_px: 1.46, fov_w_deg: 2.54, fov_h_deg: 1.70, fov_diag_deg: 3.06,
    aperture_mm: 0, reducer: 1, f_ratio: null,
  },
  active_profile_id: null,
  safety: {}, escalation: {}, alerts: [], deadman_url: "",
  providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" },
  auth: { methods: ["local"], provider: "none", google_configured: false,
          admin_token_configured: false, session_signing_configured: true,
          role_allowlist: {}, default_role: "viewer", trust_loopback: true },
  solve_saved_lights: false,
};

function seed(role: "admin" | "viewer"): void {
  useStore.setState({
    config: CONFIG,
    status: { disk: { free_gb: 412.4, low: false, critical: false } },
    principal: {
      role,
      email: role === "admin" ? "bear@example.com" : "watcher@example.com",
      caps: role === "admin" ? ALL_CAPS : ["view.status", "view.preview"],
    },
    wsPhase: "up",
    equipConnected: true,
    preview: null,
    toasts: [],
  } as never);
}

// =====================================================================
// CONNECTION
// =====================================================================
seed("admin");
try { localStorage.removeItem(CONN_PREF_KEY); } catch { /* nothing stored is the default */ }

await render(createElement(ConnectionSheet));

test("connection: the sheet rendered its rig card and both reach cards", () => {
  assert(byId("sheet-connection") != null, "no sheet found - the fixture is wrong, not the component");
  assert(byId("conn-rig-card") != null, "no rig-computer card found - the fixture is wrong, not the component");
  assert(byId("conn-card-direct") != null, "no DIRECT card");
  assert(byId("conn-card-relay") != null, "no RELAY card");
  assert(byId("conn-pair-url") != null, "no pairing link block");
  assert(byId("conn-secure") != null, "no secure-context note");
});

test("connection: the LAN origin is the one you are on, and the relay is a link", () => {
  eq(byId("conn-status-direct").textContent, "you are here", "the DIRECT card does not claim this origin");
  assert(byId("conn-open-direct") == null, "the card you are already on offered to open itself");
  eq(byId("conn-status-relay").textContent, "connected", "the live tunnel did not reach the RELAY card");
  const open = byId("conn-open-relay");
  assert(open != null, "the RELAY card has no OPEN VIA RELAY link");
  assert(/OPEN VIA RELAY/.test(open.textContent), "the relay button is not labelled as a link");
  assert(
    /relay\.astrodeck\.app\/h\/abc123\//.test(byId("conn-pair-url").textContent),
    "the pairing block does not carry the relay url",
  );
});

test("connection: the engine version came from /healthz and the disk from status", () => {
  assert(/engine 0\.3\.28/.test(byId("conn-rig-card").textContent), "the engine version never arrived");
  assert(/412 GB free/.test(byId("conn-rig-card").textContent), "the free-space clause never arrived");
  assert(/signed in as bear@example\.com/.test(text()), "the signed-in-as line is missing");
});

test("connection: no fact the wire does not carry", () => {
  const t = text();
  assert(!/MB\/s/.test(t), "a throughput figure appeared before anything measured one");
  assert(!/\bup \d+ d\b/.test(t), "an uptime appeared that nothing on the wire carries");
  assert(!/Orange Pi/.test(t), "a hardware model appeared that nothing on the wire carries");
  assert(!/owner key paired/.test(t), "a pairing date appeared that nothing on the wire carries");
});

// ------------------------------------------------------------------- the QR
//
// THE ONLY THING ON THIS SHEET NOBODY CAN PROOFREAD. A QR that encodes a
// different host looks exactly like one that encodes the right one, so the
// assertion is not "a path was drawn" but "the path IS the encoding of the
// address printed beside it". `next/lib/__tests__/qr.test.ts` is what proves
// the encoder itself; this proves the code on screen belongs to this URL.
test("connection: the pairing QR encodes the same address the link block shows", () => {
  assert(byId("conn-pair") != null, "no pairing card - the fixture is wrong, not the component");
  const shown = String(byId("conn-pair-url").textContent);
  eq(shown, "https://relay.astrodeck.app/h/abc123/", "precondition: the seeded pairing address");

  const qr = byId("conn-pair-qr");
  assert(qr != null, "the pairing card drew no QR code");
  eq(qr.getAttribute("role"), "img", "the QR is not exposed as an image");
  eq(
    qr.getAttribute("aria-label"),
    `QR code for ${shown}`,
    "the QR's accessible name does not name the address it carries",
  );
  const version = Number(qr.getAttribute("data-qr-version"));
  assert(
    Number.isInteger(version) && version >= 1 && version <= 10,
    `data-qr-version is not a version this encoder builds: "${qr.getAttribute("data-qr-version")}"`,
  );

  const path = qr.querySelector("path");
  assert(path != null, "the QR has no path");
  const d = String(path.getAttribute("d"));
  assert(d.length > 0, "the QR path is empty");
  eq(
    d,
    qrPath(encodeQr(shown)),
    "the drawn code is not the encoding of the address printed beside it",
  );
  eq(version, encodeQr(shown).version, "the declared version is not the drawn symbol's");
});

test("connection: the QR is black on white and stays that way under any theme", () => {
  const qr = byId("conn-pair-qr");
  const rect = qr.querySelector("rect");
  assert(rect != null, "the QR has no background rect, so a dark sheet shows through it");
  eq(rect.getAttribute("fill"), "#ffffff", "the QR ground is not literal white");
  eq(
    qr.querySelector("path").getAttribute("fill"),
    "#000000",
    "the QR modules are not literal black - a themed code does not scan",
  );
  // The quiet zone is inside the viewBox: a decoder needs four light modules of
  // margin and the card behind it cannot be trusted to supply them.
  const box = String(qr.getAttribute("viewBox")).split(" ").map(Number);
  const version = Number(qr.getAttribute("data-qr-version"));
  eq(box[2], 4 * version + 17 + 8, "the viewBox does not include a 4-module quiet zone");
  eq(box[3], box[2], "the QR viewBox is not square");
  const t = String(byId("conn-pair").textContent);
  assert(
    /Scan it with the other phone's camera - it opens the same address as the link\./.test(t),
    `the line under the code is missing or reworded: "${t}"`,
  );
  assert(!/\u2014/.test(t), "an em-dash reached the pairing card");
});

test("connection: COPY LINK still copies with the QR beside it", () => {
  const copy = byId("conn-copy");
  eq(copy.getAttribute("aria-disabled"), null, "precondition: an admin with a relay found COPY LINK locked");
  eq(copy.textContent, "COPY LINK", "precondition: the button has already been pressed");
  click(copy);
  eq(copy.textContent, "COPIED", "pressing COPY LINK did not confirm the copy");
});

await testAsync("connection: TEST asks /healthz then /api/me and prints a latency", async () => {
  asked.length = 0;
  click(byId("conn-test"));
  await settle();
  const health = asked.findIndex((a) => a === "GET /healthz");
  const me = asked.findIndex((a) => a === "GET /api/me");
  assert(health >= 0, `TEST did not ask /healthz (asked: ${asked.join(", ")})`);
  assert(me > health, `TEST did not ask /api/me after /healthz (asked: ${asked.join(", ")})`);
  const line = byId("conn-test-result");
  assert(line != null, "TEST produced no result line");
  assert(/reachable in \d+ ms/.test(line.textContent), `no latency in "${line.textContent}"`);
  assert(/signed in as bear@example\.com \(admin\)/.test(line.textContent), "the result does not name the session");
});

await testAsync("connection: a rig that answers with no session says exactly that", async () => {
  meAuthorised = false;
  asked.length = 0;
  click(byId("conn-test"));
  await settle();
  const line = byId("conn-test-result");
  assert(
    /not signed in on this address/.test(line.textContent),
    `the reachable-but-signed-out case is the diagnosable one and it is missing: "${line.textContent}"`,
  );
  meAuthorised = true;
});

// ---------------------------------------------------- origin: /h/<home_id>/
await testAsync("connection: on the relay mount the two cards swap roles", async () => {
  await clearTree();
  dom.reconfigure({ url: "http://relay.astrodeck.app/h/abc123/#/settings/general/connection" });
  remotePayload = { ...remotePayload, via: "relay" };
  // Forget the LAN address this device learned above, so the branch under test
  // is the one a phone that has only ever used the relay actually meets.
  try { localStorage.removeItem(CONN_PREF_KEY); } catch { /* default is no host */ }
  await render(createElement(ConnectionSheet));
  eq(byId("conn-status-relay").textContent, "you are here", "the RELAY card does not claim the relay origin");
  eq(byId("conn-status-direct").textContent, "no address yet", "an unknown LAN address must say so");
  assert(byId("conn-open-relay") == null, "the relay card offered to open the page it is on");
  const openDirect = byId("conn-open-direct");
  assert(openDirect != null, "the DIRECT card lost its OPEN DIRECT link");
  assert(/OPEN DIRECT/.test(openDirect.textContent), "the direct button is not labelled as a link");
  assert(
    openDirect.getAttribute("aria-disabled") === "true",
    "with no remembered LAN address the DIRECT button must be honest-disabled, not live",
  );
  assert(
    /has not reached the rig directly yet/.test(String(openDirect.getAttribute("title"))),
    "the locked DIRECT button carries no reason",
  );
  assert(
    /pairing has to be done on the rig's own network/.test(text()),
    "the LAN-only pairing rule is not stated on the relay origin",
  );
});

// ------------------------------------------------------------------- viewer
await testAsync("connection: a viewer sees the same screen, pairing locked, no writes", async () => {
  await clearTree();
  dom.reconfigure({ url: "http://local/#/settings/general/connection" });
  remotePayload = { ...remotePayload, via: "direct" };
  seed("viewer");
  asked.length = 0;
  await render(createElement(ConnectionSheet));
  assert(byId("conn-rig-card") != null, "the viewer lost the rig card - read-only means the same screen");
  const pair = byId("conn-pair-new");
  eq(pair.getAttribute("aria-disabled"), "true", "PAIR A NEW RIG is live for a viewer");
  assert(
    /needs admin access/.test(String(pair.getAttribute("title"))),
    `the pairing lock names no capability: "${pair.getAttribute("title")}"`,
  );
  assert(
    qa('[data-testid="conn-pair-reason"]').some((li) => /needs admin access/.test(li.textContent)),
    "the reason is only in a tooltip, which never fires on touch",
  );
  const writes = asked.filter((a) => !a.startsWith("GET "));
  eq(writes.length, 0, `a viewer issued a write: ${writes.join(", ")}`);
  const reads = asked.filter((a) => a.startsWith("GET "));
  assert(
    reads.every((a) => /\/healthz|\/api\/remote\/status/.test(a)),
    `a viewer issued something beyond the two view.status reads: ${reads.join(", ")}`,
  );
});

// --------------------------------------------------------- no relay paired
await testAsync("connection: with no relay paired there is no QR and no orphan caption", async () => {
  await clearTree();
  dom.reconfigure({ url: "http://local/#/settings/general/connection" });
  remotePayload = null; // `GET /api/remote/status` 404s: no relay on this rig
  try { localStorage.removeItem(CONN_PREF_KEY); } catch { /* nothing stored is the default */ }
  seed("admin");
  await render(createElement(ConnectionSheet));

  assert(byId("conn-pair") != null, "no pairing card - the fixture is wrong, not the component");
  eq(
    String(byId("conn-pair-url").textContent),
    "no relay address is paired with this rig",
    "the no-relay sentence was replaced or dropped",
  );
  assert(
    byId("conn-pair-qr") == null,
    "a QR was drawn with no relay address to encode, so it points somewhere nobody chose",
  );
  assert(
    !/Scan it with the other phone/.test(String(byId("conn-pair").textContent)),
    "the scan instruction outlived the code it describes",
  );
  const copy = byId("conn-copy");
  eq(copy.getAttribute("aria-disabled"), "true", "COPY LINK is live with nothing to copy");
  assert(
    /there is no relay address to copy/.test(String(copy.getAttribute("title"))),
    `the locked COPY LINK carries no reason: "${copy.getAttribute("title")}"`,
  );
  remotePayload = {
    enabled: true, home_id: "abc123", relay_host: "relay.astrodeck.app",
    connected: true, last_error: null, since_unix: 1_757_000_000, gen: 3, via: "direct",
  };
});

// =====================================================================
// OPTICS
// =====================================================================
await clearTree();
try { localStorage.removeItem(OPTICS_AUX_KEY); } catch { /* the default aux is the fixture */ }
seed("admin");
useStore.setState({
  preview: {
    field: {
      source: "solve", solved_at: Math.floor(Date.now() / 1000) - 600,
      id: null, objects: [], fov_w_deg: 2.2, data_width: 6248,
    },
  },
} as never);

await render(createElement(OpticsSheet));

test("optics: four readout tiles, the preview and the live field line", () => {
  assert(byId("sheet-optics") != null, "no optics sheet - the fixture is wrong, not the component");
  assert(byId("optics-fov") != null, "no FoV preview card");
  assert(byId("optics-fov-box") != null, "no sensor rectangle in the preview");
  for (const t of ["focal", "aperture", "reducer", "pixel"]) {
    assert(byId(`optics-tile-${t}`) != null, `no ${t} readout tile`);
  }
  assert(byId("optics-dial") != null, "no dial under the tiles");
  const live = byId("optics-live");
  assert(live != null, "no live field line in the sheet header");
  assert(/530 mm/.test(live.textContent), `the live line does not carry the focal length: "${live.textContent}"`);
  assert(/2\.54° × 1\.70°/.test(live.textContent), `the live line is not the server's field: "${live.textContent}"`);
  assert(/M31 for scale/.test(byId("optics-fov").textContent), "the M31 scale reference is missing");
  assert(
    /1\.46″ per pixel · well sampled/.test(byId("optics-caption").textContent),
    `the caption lost the sampling verdict: "${byId("optics-caption").textContent}"`,
  );
});

// The probe caught FOCAL LENGTH one character short at 820: four tiles across
// the 420 px panel leave 59 px of label, `.nx-readout-label` is nowrap with
// nowhere to put the rest, and the tile beside it painted over the H. The name
// of the field cannot be shortened - this sheet's whole subject is telling it
// apart from the GUIDE SCOPE focal length below - so the row wraps instead.
// jsdom lays nothing out; what is graded is the class on the row, the rule in
// the stylesheet, and the label still being the whole phrase.
test("optics: the tile row is taught to wrap inside the panel", () => {
  const tiles = byId("optics-tiles");
  assert(tiles != null, "the tile row lost its marker");
  assert((tiles.className || "").split(" ").includes("nx-readouts-wrap"),
    "the tile row does not carry the class the wrap rule keys on");
  assert(/repeat\(auto-fit/.test(cssRule(NEXT_CSS, ".nx-readouts-wrap[data-cols]")),
    "the tile row is still a fixed four columns, which is 59 px of label a tile in the panel");
  assert(/white-space:\s*normal/.test(cssRule(NEXT_CSS, ".nx-readouts-wrap .nx-readout-label")),
    "a tile label is still nowrap: FOCAL LENGTH goes back to being painted over");
  const label = qa(".nx-readout-label")
    .map((n: any) => (n.textContent || "").trim())
    .find((t: string) => t.startsWith("FOCAL"));
  eq(label, "FOCAL LENGTH",
    "the label was shortened instead of the row being made to fit:");
});

test("optics: the aperture is absent, not invented, until the user says", () => {
  assert(
    /not set/.test(byId("optics-tile-aperture").textContent),
    "an aperture appeared that nobody entered",
  );
  assert(
    /aperture not set/.test(byId("optics-tile-aperture").textContent),
    "the f-ratio sub-line invented a number from the focal length alone",
  );
});

test("optics: the plate-solve row offers the measured focal length", () => {
  const row = byId("optics-solve-row");
  assert(row != null, "no calibrate-from-the-last-solve row");
  // 2.2 deg over 6248 px = 1.2676"/px; 206.265 * 3.76 / 1.2676 = 611.7 mm.
  assert(/1\.27″\/px/.test(row.textContent), `the solved scale is wrong: "${row.textContent}"`);
  assert(/USE 612 MM/.test(row.textContent), `the derived focal length is wrong: "${row.textContent}"`);
});

test("optics: USE THIS puts the solved focal length into the draft", () => {
  click(byId("optics-solve-use"));
  assert(
    /612 mm/.test(byId("optics-tile-focal").textContent),
    `the draft did not take the solved value: "${byId("optics-tile-focal").textContent}"`,
  );
});

await testAsync("optics: SAVE is PUT /api/optics carrying {optics, version}", async () => {
  asked.length = 0;
  putOptics = null;
  click(byId("optics-save"));
  await settle();
  assert(
    asked.some((a) => a === "PUT /api/optics"),
    `SAVE did not write to /api/optics (asked: ${asked.join(", ")})`,
  );
  assert(putOptics != null, "the PUT carried no body");
  eq(putOptics.version, 42, "the optimistic-concurrency version was not sent, so a 409 can never be honest");
  eq(putOptics.optics.focal_length_mm, 612, "the saved focal length is not the one on screen");
  eq(putOptics.optics.telescope_name, "Askar FRA400", "the PUT dropped a field it was not editing");
  eq(
    putOptics.optics.aperture_mm, 0,
    "APERTURE is no longer part of the SAME optics draft SAVE OPTICS writes",
  );
  eq(
    putOptics.optics.reducer, 1,
    "REDUCER is no longer part of the SAME optics draft SAVE OPTICS writes",
  );
});

// ------------------------------------------------- the server's own f-ratio
await testAsync("optics: the f-ratio reads the server's optics_computed.f_ratio when present", async () => {
  await clearTree();
  try { localStorage.removeItem(migrationFlagKey(OPTICS_AUX_KEY)); } catch { /* nothing to clear */ }
  seed("admin");
  useStore.setState({
    config: {
      ...CONFIG,
      optics: { ...OPTICS, aperture_mm: 200, reducer: 1 },
      optics_computed: {
        ...CONFIG.optics_computed,
        aperture_mm: 200, reducer: 1,
        // Deliberately NOT 530/200 (2.65): a figure this module's own
        // arithmetic would never produce, so a component that silently fell
        // back to `fRatioLabel` instead of reading this field is caught.
        f_ratio: 2.5,
      },
    },
  } as never);
  await render(createElement(OpticsSheet));
  assert(
    /f\/2\.5\b/.test(byId("optics-tile-aperture").textContent),
    `the aperture tile did not use the server's own f_ratio: "${byId("optics-tile-aperture").textContent}"`,
  );
  assert(
    /f\/2\.5\b/.test(byId("optics-live").textContent),
    `the live line did not use the server's own f_ratio: "${byId("optics-live").textContent}"`,
  );
});

// ---------------------------------------------------------------- migration
//
// D-FU-1 / D-SET-1: `astrodeck-next-optics-aux` is the last phone-local rig
// key, moved once through `storageMigration.ts`'s `migrateKey`. Both outcomes
// get their own test because they behave oppositely on purpose: the rig had
// nothing yet (the phone's values become the rig's, quietly) versus the rig
// already had one (the phone's copy is discarded, never merged, and that is
// the one outcome worth a toast - "replaced by the rig's" would be a false
// claim on the quiet path, where nothing was replaced).
await testAsync("optics: an unmigrated local key moves up once when the rig has none yet, quietly", async () => {
  await clearTree();
  try { localStorage.removeItem(migrationFlagKey(OPTICS_AUX_KEY)); } catch { /* nothing to clear */ }
  localStorage.setItem(OPTICS_AUX_KEY, JSON.stringify({ apertureMm: 200, reducer: 0.8 }));
  seed("admin");
  useStore.setState({ toasts: [] } as never);
  asked.length = 0;
  putOptics = null;
  await render(createElement(OpticsSheet));
  const puts = asked.filter((a) => a === "PUT /api/optics");
  eq(puts.length, 1, `the migration did not PUT exactly once (asked: ${asked.join(", ")})`);
  assert(putOptics != null, "the migration PUT carried no body");
  eq(putOptics.optics.aperture_mm, 200, "the phone's aperture did not reach the PUT body");
  eq(putOptics.optics.reducer, 0.8, "the phone's reducer did not reach the PUT body");
  assert(
    localStorage.getItem(OPTICS_AUX_KEY) == null,
    "the local key survived a migration the rig accepted",
  );
  const titles = useStore.getState().toasts.map((t: any) => t.title);
  assert(
    !titles.includes(OPTICS_MIGRATION_TOAST),
    "a toast fired for the quiet path, where the phone's values became the rig's rather than being replaced",
  );
});

await testAsync("optics: the same key with a non-zero server aperture is discarded, never merged", async () => {
  await clearTree();
  try { localStorage.removeItem(migrationFlagKey(OPTICS_AUX_KEY)); } catch { /* nothing to clear */ }
  localStorage.setItem(OPTICS_AUX_KEY, JSON.stringify({ apertureMm: 200, reducer: 0.8 }));
  seed("admin");
  useStore.setState({
    toasts: [],
    config: { ...CONFIG, optics: { ...OPTICS, aperture_mm: 106, reducer: 1 } },
  } as never);
  asked.length = 0;
  putOptics = null;
  await render(createElement(OpticsSheet));
  const puts = asked.filter((a) => a === "PUT /api/optics");
  eq(puts.length, 0, `a conflict must not PUT anything (asked: ${asked.join(", ")})`);
  assert(putOptics == null, "the conflict path sent a body, which means it merged rather than discarding");
  assert(
    localStorage.getItem(OPTICS_AUX_KEY) == null,
    "the local copy survived a conflict the rig already won",
  );
  const titles = useStore.getState().toasts.map((t: any) => t.title);
  assert(
    titles.includes(OPTICS_MIGRATION_TOAST),
    `the conflict toast did not fire verbatim (toasts: ${JSON.stringify(titles)})`,
  );
});

// ------------------------------------------------------------------- viewer
await testAsync("optics: a viewer gets the sentence and issues nothing", async () => {
  await clearTree();
  seed("viewer");
  asked.length = 0;
  await render(createElement(OpticsSheet));
  assert(byId("optics-tile-focal") != null, "the viewer lost the readouts - read-only means the same screen");
  const save = byId("optics-save");
  eq(save.getAttribute("aria-disabled"), "true", "SAVE OPTICS is live for a viewer");
  assert(
    /needs admin access/.test(String(save.getAttribute("title"))),
    `the SAVE lock names no capability: "${save.getAttribute("title")}"`,
  );
  assert(
    /Changing optics needs admin access\. The current values are shown for reference\./.test(text()),
    "the read-only sentence is missing from the body",
  );
  eq(asked.length, 0, `a viewer issued a request: ${asked.join(", ")}`);
});

await testAsync("optics: a viewer never fires the migration PUT, and keeps reading local", async () => {
  await clearTree();
  try { localStorage.removeItem(migrationFlagKey(OPTICS_AUX_KEY)); } catch { /* nothing to clear */ }
  localStorage.setItem(OPTICS_AUX_KEY, JSON.stringify({ apertureMm: 200, reducer: 0.8 }));
  seed("viewer");
  asked.length = 0;
  putOptics = null;
  await render(createElement(OpticsSheet));
  eq(asked.length, 0, `a viewer's read-only render issued a request: ${asked.join(", ")}`);
  assert(putOptics == null, "a viewer triggered the migration write");
  assert(
    localStorage.getItem(OPTICS_AUX_KEY) != null,
    "the local copy was deleted for a role that cannot write it - it must keep reading local until it can",
  );
  assert(
    /200 mm/.test(byId("optics-tile-aperture").textContent),
    `the viewer did not fall back to the phone's own copy: "${byId("optics-tile-aperture").textContent}"`,
  );
  const note = byId("optics-legacy-note");
  assert(note != null, "no LockNote explaining the read-only fallback");
  assert(
    /Read-only - needs admin access/.test(String(note.textContent)),
    `the legacy note does not name the capability: "${note.textContent}"`,
  );
  // Cleanup: a viewer's read-only render deliberately leaves the key in place
  // (asserted above), but a later test in this file that seeds an ADMIN
  // principal must not inherit it - it would trigger a real migration this
  // fixture never asked for.
  try { localStorage.removeItem(OPTICS_AUX_KEY); } catch { /* nothing to clear */ }
});

// ---------------------------------------------------------------- override
await testAsync("optics: a profile's optics block raises the banner and lists all nine keys", async () => {
  const entry = (value: unknown, cfg: unknown) => ({
    value, layer: "profile", profile: value, config: cfg, default: cfg,
    profile_id: "p1", profile_name: "Rig1", reason: "override: profile",
  });
  await clearTree();
  seed("admin");
  useStore.setState({
    config: {
      ...CONFIG,
      effective: {
        "optics.focal_length_mm": entry(250, 530),
        "optics.pixel_size_um": entry(2.4, 3.76),
        "optics.sensor_width_px": entry(4144, 6248),
        "optics.sensor_height_px": entry(2822, 4176),
        "optics.auto_from_camera": entry(false, true),
        "optics.guide_focal_length_mm": entry(120, 200),
        "optics.telescope_name": entry("RedCat 51", "Askar FRA400"),
        // D-SET-1: aperture and reducer join the same whole-block swap, so a
        // profile pinning them is exactly the case the banner exists for.
        "optics.aperture_mm": entry(80, 0),
        "optics.reducer": entry(0.8, 1),
        "providers.solve": {
          value: "astap", layer: "config", profile: null, config: "astap",
          default: "auto", profile_id: null, profile_name: null, reason: null,
        },
      },
    },
  } as never);
  await render(createElement(OpticsSheet));
  const banner = byId("optics-override");
  assert(banner != null, "a profile pinning optics raised no banner");
  assert(/Rig1/.test(banner.textContent), "the banner does not name the profile in charge");
  eq(
    qa('[data-testid="optics-override-key"]').length,
    9,
    "the banner must list all nine keys - a whole-block swap moves the ones nobody was looking at, aperture and reducer included",
  );
  assert(
    /Pixel size: 2\.4 µm - this sheet shows 3\.76 µm/.test(banner.textContent),
    "the banner does not print running-vs-panel for a key the user never touched",
  );
  assert(
    /Aperture: 80 mm - this sheet shows not set/.test(banner.textContent),
    "the banner does not list the aperture override now that it is part of the same optics block",
  );
  assert(
    /will keep overriding them/.test(String(byId("optics-save-warning")?.textContent)),
    "the save-time warning is missing, and the save button is where the false belief forms",
  );
});

// ------------------------------------------------------- the solver row (#23)
//
// THE OPTIONS FOLLOW THE DRIVER LIST. They used to be a hard-coded four
// (auto / astrodeck / astap / backend), so a configured NINA or ASIAIR whose
// probe offers `solve` could not be chosen BY NAME on this screen at all - the
// one thing the row exists to do. Re-hard-code the list and the first assertion
// prints the vocabulary it found.
await testAsync("optics: the SOLVER options are the drivers that offer solve, by name", async () => {
  await clearTree();
  DRIVERS = [
    driver({ id: "astap", type: "astap", label: "ASTAP", implicit: true, offers: { devices: [], tasks: ["solve"] } }),
    driver({ id: "nina-1", label: "NINA - astrotown", offers: { devices: [], tasks: ["solve", "autofocus"] } }),
    driver({ id: "phd2-1", type: "phd2", label: "PHD2", offers: { devices: [], tasks: ["guide"] } }),
    driver({ id: "astap-off", type: "astap", label: "ASTAP (spare)", enabled: false, offers: { devices: [], tasks: ["solve"] } }),
    driver({
      id: "nina-down", label: "NINA - shed", offers: { devices: [], tasks: ["solve"] },
      status: { reachable: false, error: "refused", detail: null, probed_at: 1 },
    }),
  ];
  seed("admin");
  useStore.setState({
    status: {
      disk: { free_gb: 412.4, low: false, critical: false },
      providers: { solve: { kind: "astap", label: "ASTAP", reason: "no plate solver configured; falling back" } },
    },
  } as never);
  await render(createElement(OpticsSheet));

  const pick = byId("optics-solver-pick");
  assert(pick != null, "no solver control - the fixture is wrong, not the component");
  const values = qa('[data-testid="optics-solver-pick"] option').map((o: any) => o.value);
  eq(values.join(","), "auto,astap,nina-1",
    "the solver options did not follow the seeded driver list");
  const labels = qa('[data-testid="optics-solver-pick"] option').map((o: any) => o.textContent);
  assert(labels.includes("NINA - astrotown"),
    `a configured solver cannot be picked by name: ${labels.join(" | ")}`);

  // ...and the per-provider resolver reason, which the hard-coded list had no
  // room for: what the rig ACTUALLY resolved, and why.
  const resolved = byId("optics-solver-resolved");
  assert(resolved != null, "the resolver line is missing, so the row cannot say what the rig actually uses");
  assert(/no plate solver configured/.test(resolved.textContent),
    `the resolver line dropped the reason: "${resolved.textContent}"`);
});

await testAsync("optics: choosing a driver writes THAT id, not a vocabulary word", async () => {
  asked.length = 0;
  const pick = byId("optics-solver-pick");
  pick.value = "nina-1";
  await act(async () => {
    pick.dispatchEvent(new win.Event("change", { bubbles: true }));
  });
  await settle();
  assert(asked.some((a) => a === "POST /api/config/providers"),
    `the pick never reached the providers route: ${asked.join(", ")}`);
});

// -------------------------------------------- clearing a solve pin (#24)
//
// A `solve` pin written into a profile could be EDITED but never REMOVED: the
// only unpin on this branch was polar's. Delete the button and the first
// assertion fails; hide it from a viewer instead of locking it and the second
// does.
await testAsync("optics: a profile-pinned solver can be unpinned, and the call names the cap", async () => {
  await clearTree();
  seed("admin");
  clearedOverrides = null;
  useStore.setState({
    config: {
      ...CONFIG,
      effective: {
        "providers.solve": {
          value: "nina-1", layer: "profile", profile: "nina-1", config: "auto",
          default: "auto", profile_id: "p1", profile_name: "Rig1", reason: "override: profile",
        },
      },
    },
  } as never);
  await render(createElement(OpticsSheet));

  const unpin = byId("optics-solver-unpin");
  assert(unpin != null, "a profile-pinned solver offers no way to clear the pin");
  eq(unpin.getAttribute("aria-disabled"), null, "precondition: an admin found the unpin locked");
  click(unpin);
  await settle();
  assert(asked.some((a) => a.includes("/api/profiles/p1/clear-overrides")),
    `the unpin never reached the clear-overrides route: ${asked.join(", ")}`);
  eq(JSON.stringify(clearedOverrides?.providers), '["solve"]',
    "the unpin cleared the wrong capability (it must name solve and nothing else):");
  eq(clearedOverrides?.optics, false,
    "the unpin also cleared the profile's OPTICS block, which nobody asked it to:");
});

await testAsync("optics: a viewer sees the unpin locked with its reason, not hidden", async () => {
  await clearTree();
  seed("viewer");
  useStore.setState({
    config: {
      ...CONFIG,
      effective: {
        "providers.solve": {
          value: "nina-1", layer: "profile", profile: "nina-1", config: "auto",
          default: "auto", profile_id: "p1", profile_name: "Rig1", reason: "override: profile",
        },
      },
    },
  } as never);
  await render(createElement(OpticsSheet));
  const unpin = byId("optics-solver-unpin");
  assert(unpin != null, "the unpin was HIDDEN from a viewer - ARCHITECTURE section 8 says nothing is hidden");
  eq(unpin.getAttribute("aria-disabled"), "true", "the unpin looks live to a viewer");
  assert(unpin.hasAttribute("disabled") === false,
    "the unpin uses the native disabled attribute, which takes the reason out of the accessibility tree");
  assert(/access/.test(unpin.getAttribute("title") ?? ""),
    `the locked unpin does not say who may press it: "${unpin.getAttribute("title")}"`);
});

// =====================================================================
// OPTICS - the four hand-pinned sensor numbers (wave-2 review R8 P1)
// =====================================================================
//
// THE DEFECT. All three were `TextInput`s controlled from
// `String(Number(v) || 0)`, which re-renders the box from a parse of every
// keystroke. Two consequences, both silent:
//
//   * "3." parses to 3 and the box snaps back to "3" before the next digit can
//     land, so a DECIMAL pixel size - the only kind there is - could not be
//     typed at all. The dial was the only way to reach 3.76 um.
//   * a blank box is `Number("") || 0` = 0, a finite and plausible pixel size
//     that the rig reads as "fall back to the camera". Clearing the field to
//     retype it committed that zero.
//
// `NumberField` holds a raw-string draft and parses at blur/Enter only, and
// REJECTS what does not parse instead of reading it as 0. Both halves are
// asserted, because a fix for one is not a fix for the other.

// `pixel_size_um` starts at 2.4, NOT the fixture's 3.76: `NumberField` calls
// `onCommit` only when the committed number DIFFERS from the current value
// (twenty call sites hang a PATCH off it), so typing the number already stored
// would leave the draft clean and the SAVE assertion below would be grading an
// early return rather than a write. `auto_from_camera` is off because that is
// the only state in which the three hand-pinned fields render at all.
const PINNED = { ...CONFIG, optics: { ...OPTICS, auto_from_camera: false, pixel_size_um: 2.4 } };

await clearTree();
seed("admin");
useStore.setState({ config: PINNED } as never);
await render(createElement(OpticsSheet));

/** Type into a controlled React input one character at a time, as a user does:
 *  React installs its own value tracker, so assigning `el.value` alone leaves it
 *  thinking nothing changed - and typing the whole string at once would not
 *  exercise the intermediate "3." state that was the defect. */
const typeChars = async (el: any, value: string): Promise<void> => {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  for (let i = 1; i <= value.length; i++) {
    await act(async () => {
      setter.call(el, value.slice(0, i));
      el.dispatchEvent(new win.Event("input", { bubbles: true }));
    });
  }
};
/** React maps `onBlur` to the DOM's `focusout`, which BUBBLES - a native `blur`
 *  event does not bubble and never reaches React's root listener, so dispatching
 *  one leaves the commit unfired and every assertion after it grades an
 *  uncommitted draft. */
const blur = async (el: any): Promise<void> => {
  await act(async () => {
    el.dispatchEvent(new win.FocusEvent("focusout", { bubbles: true }));
  });
  await settle();
};

await testAsync("optics: a decimal pixel size is typed into a DRAFT, not parsed per keystroke", async () => {
  const px = byId("optics-pixel-size");
  assert(px != null, "the pixel-size field is not on the page - the fixture is wrong");
  eq(px.hasAttribute("disabled"), false, "the pixel-size field uses the native disabled attribute");
  eq(px.value, "2.4", "precondition: the box does not show the rig's own pixel size");
  eq(byId("optics-save").getAttribute("aria-disabled"), "true",
    "precondition: SAVE is already armed before anything was typed, so the "
    + "draft assertion below could not tell a keystroke from a commit");

  await typeChars(px, "3.76");
  eq(px.value, "3.76", "the decimal point was eaten while typing; the box holds:");
  // THE LOAD-BEARING HALF. The old control wrote `Number(v) || 0` into the draft
  // on EVERY keystroke - which is what re-rendered "3" over "3." and made a
  // decimal untypable in a browser. `NumberField` parses at blur and Enter only,
  // so mid-edit the draft is still the rig's number and SAVE is still locked.
  eq(byId("optics-save").getAttribute("aria-disabled"), "true",
    "a half-typed number was written straight into the draft");

  await blur(px);
  eq(px.value, "3.76", "the committed value is not what was typed:");
  eq(byId("optics-save").getAttribute("aria-disabled"), null,
    "the commit at blur never reached the draft - SAVE is still locked as unchanged");
});

await testAsync("optics: the typed pixel size reaches the rig on SAVE, to two decimals", async () => {
  asked.length = 0;
  putOptics = null;
  click(byId("optics-save"));
  await settle();
  assert(asked.some((a) => a === "PUT /api/optics"),
    `SAVE did not PUT /api/optics: ${asked.join(", ")}`);
  eq(putOptics?.optics?.pixel_size_um, 3.76, "the pixel size that went on the wire:");
});

await testAsync("optics: a blank sensor width is REJECTED, never committed as 0", async () => {
  await clearTree();
  seed("admin");
  useStore.setState({ config: PINNED } as never);
  await render(createElement(OpticsSheet));
  const w = byId("optics-sensor-w");
  assert(w != null, "the sensor-width field is not on the page");
  eq(w.value, "6248", "precondition: the field does not show the rig's own width");
  await typeChars(w, "");
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
    setter.call(w, "");
    w.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
  await blur(w);
  eq(w.value, "6248",
    "a cleared box committed a value - 0 px is a real number the rig would store as "
    + "'take it from the camera'");
  asked.length = 0;
  click(byId("optics-save"));
  await settle();
  eq(asked.filter((a) => a === "PUT /api/optics").length, 0,
    "SAVE fired for a draft nothing changed - a blank box was read as an edit");
});

await testAsync("optics: SAVE with nothing changed says so instead of swallowing the press", async () => {
  const save = byId("optics-save");
  assert(save != null, "no SAVE button");
  eq(save.getAttribute("aria-disabled"), "true",
    "SAVE looks live on an untouched draft, and `save()` returns silently on !dirty - "
    + "a press that does nothing and says nothing");
  eq(save.getAttribute("title"), "these values already match what the rig has",
    "the locked SAVE does not say why it would do nothing");
});

await act(async () => { root.unmount(); });

const total = passed + failed;
console.log(`connectionOpticsDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
