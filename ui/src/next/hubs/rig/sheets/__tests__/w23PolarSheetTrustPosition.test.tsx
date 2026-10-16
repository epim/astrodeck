// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w23PolarSheetTrustPosition.test.tsx - WP-218 / #1013: the #/next POLAR
// ALIGNMENT sheet while the mount does not know where it points, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/w23PolarSheetTrustPosition.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `POST /api/polar/start` aims every leg of its arc from the believed position,
// so the server refuses it with 409 `position_unknown` while the mount reports
// its home position wherever the tube is (#888), and the refusal advises TRUST
// POSITION or a pad key. The sheet hosted neither: START was offered anyway, the
// refusal reached the operator as an error toast naming a control that was not
// on the sheet, and the attestation lived on the MOUNT sheet, which nothing
// pointed to. #985 fixed the classic PolarView; this is the same defect at the
// alignment step of the new UI. This mounts the real PolarSheet against a fake
// rig and asks, per state: is START offered (it must not be live), is the
// attestation on THIS sheet, does it ask in the shared words before it posts,
// does a start that raced the status land on the attestation and not in a toast,
// does a declined attestation stay unreported, and does the sheet let go only
// when the STATUS says so. Every case asks a known position first.
//
// Named mutants (one-statement source changes; each run from a byte backup,
// restored byte-identical, md5sum compared). The failing lines are in the
// WP-218 report.
//   w23p1_start_not_locked -- in next/hubs/rig/sheets/polar.tsx drop
//     `?? (position.needed ? POLAR_POSITION_UNKNOWN_REASON : null)` from
//     `startReason`. START is live beside a refusal it cannot win.
//   w23p2_control_not_hosted -- in the same file `position.needed && !live &&`
//     made `false &&`. The sheet names a control it does not host.
//   w23p3_refusal_blind -- in the same file `if (!position.refusedBy(e)) throw e;`
//     made `throw e;`. The raced 409 reaches an error toast again.
//   w23p4_trust_without_confirm -- in guided/GuidedTrustPosition.tsx
//     `await confirmDialog(` made `true || await confirmDialog(`. One tap
//     attests the tube's position.
//   w23p5_declined_reported -- in the same file the `res?.position_known ===
//     false` branch made `if (false)`. A driver that declined is announced as
//     trusted.
//   w23p6_switch_to_pro_words -- in the same file `${inPro ? "Use" : "Switch to
//     Pro and use"}` made `${false ? ...`. The declined line tells the new UI's
//     operator to switch to a mode it does not have.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/mount/polar", pretendToBeVisual: true },
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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "PointerEvent", "Image", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame",
  "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

/** The server's own words for the refusal: it advises a pad key. */
const SERVER_REFUSAL = "Mount position is unknown. Tube really at home: Trust position. If not, hold a pad key to bring it home by eye, then Trust position.";

const posts: string[] = [];
/** What /api/polar/start answers: null is a started run, else the 409 it refuses with. */
let startRefusal: null | { detail: string; code: string } = null;
/** What /api/mount/trust-position answers. */
let trustAnswer: Record<string, unknown> = { ok: true, position_known: true };
const reply = (body: unknown, status = 200): any => ({
  ok: status < 400, status, statusText: status === 409 ? "Conflict" : "OK",
  headers: { get: () => null }, json: async () => body, text: async () => JSON.stringify(body),
});
g.fetch = async (url: any, init: any = {}) => {
  const path = String(url);
  if ((init.method ?? "GET").toUpperCase() === "POST") posts.push(path);
  if (path === "/api/polar/start") {
    if (startRefusal) return reply({ detail: startRefusal }, 409);
    return reply({ started: true, source: "native" });
  }
  if (path === "/api/mount/trust-position") return reply(trustAnswer);
  return reply({});
};

const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { PolarSheet } = await import("../polar");
const {
  TRUST_POSITION_CONFIRM_BODY, TRUST_POSITION_CONFIRM_TITLE, TRUST_POSITION_LABEL,
} = await import("../../../../../lib/slewController");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture",
    "control.mount", "control.guide"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };
const IDLE_POLAR = {
  state: "idle", az_error: 0, alt_error: 0, total_error: 0,
  progress: 0, message: "", source: null,
};
// Made-up numbers; the home reading is the pole, wherever the tube is.
const HOME_READING = { ra_hours: 11.2, dec_deg: 89.9 };

function seed(mount: Record<string, unknown>, over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      wsPhase: "up", telemetryStale: false, authGate: "open", toasts: [], confirm: null,
      equipConnected: true,
      principal: OPERATOR,
      polar: IDLE_POLAR,
      logs: [],
      config: { providers: { autofocus: "auto", polar_align: "auto", solve: "auto", guide: "auto" } },
      status: {
        mode: "sim", busy_lanes: [], busy: null, looping: false,
        connected: { camera: { connected: true }, telescope: { connected: true, name: "ZWO AM5N" } },
        backend_links: [{ role: "telescope", connected: true, error: null }],
        meridian: { status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east" },
        mount: { ...HOME_READING, tracking: true, slewing: false, parked: false, can_find_home: true, ...mount },
      },
      ...over,
    } as never);
  });
}
const patchStatus = (extra: Record<string, unknown>, mountExtra: Record<string, unknown> = {}) => act(() => {
  const s = useStore.getState().status as any;
  useStore.setState({ status: { ...s, ...extra, mount: { ...s.mount, ...mountExtra } } } as never);
});
const publishPolar = (state: string) => act(() => {
  useStore.setState({ polar: { ...IDLE_POLAR, state, az_error: 20, alt_error: 15, total_error: 25 } } as never);
});

const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;
const settle = async (ms = 0) => { await act(async () => { await new Promise(r => setTimeout(r, ms)); }); };
async function mountSheet(): Promise<void> {
  if (root) await act(async () => { root!.unmount(); });
  root = createRoot(container);
  await act(async () => { root!.render(h(PolarSheet as any, { params: {}, depth: 1 })); });
  await settle();
}
const q = (sel: string): any => container.querySelector(sel);
const startBtn = (): any => q('[data-testid="polar-start"]');
const trustBtn = (): any => q('[data-testid="polar-trust-position"]');
const stopBtn = (): any => q('[data-testid="polar-stop"]');
const block = (): string => String(q('[data-testid="polar-position"]')?.textContent ?? "");
const locked = (el: any): boolean => el?.getAttribute("aria-disabled") === "true";
const click = async (node: any) => {
  assert(node != null, "click target missing - the fixture is wrong, not the sheet");
  await act(async () => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const confirmReq = (): any => (useStore.getState() as any).confirm;
const answerConfirm = async (ok: boolean) => { await act(async () => { useStore.getState().resolveConfirm(ok); }); await settle(); };
const trustPosts = () => posts.filter(p => p === "/api/mount/trust-position").length;
const startPosts = () => posts.filter(p => p === "/api/polar/start").length;
const toasts = (): any[] => (useStore.getState() as any).toasts as any[];
const errorToasts = (): any[] => toasts().filter(t => t.level === "error");

// ================================================================== controls
seed({ position_known: true });
await mountSheet();
await test("control: a mount that knows where it points offers a live START and no attestation", () => {
  assert(startBtn() != null, "no START on the sheet");
  assert(!locked(startBtn()), `START is locked for a known position: ${startBtn().getAttribute("title")}`);
  eq(trustBtn(), null, "TRUST POSITION is offered to a mount that knows where it points");
  eq(q('[data-testid="polar-position"]'), null, "the position block is up for a known position");
});

seed({ ra_hours: 5, dec_deg: 10 });
await mountSheet();
await test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
  assert(!locked(startBtn()), "START is locked for an absent flag");
  eq(trustBtn(), null, "an absent flag offered the attestation");
});

// ============================================================ unknown position
seed({ position_known: false });
await mountSheet();
await test("position unknown: TRUST POSITION is on THIS sheet, and START is not live beside it", () => {
  assert(trustBtn() != null, "no TRUST POSITION control on the polar sheet");
  eq(String(trustBtn().textContent).trim(), TRUST_POSITION_LABEL, "the control's label");
  assert(locked(startBtn()), "START is live over a start the server will refuse");
});
await test("position unknown: START's reason names the control that is on this sheet", () => {
  const why = String(startBtn().getAttribute("title") ?? "");
  assert(why.includes(TRUST_POSITION_LABEL), `START's reason does not name ${TRUST_POSITION_LABEL}: "${why}"`);
  assert(/this sheet/i.test(why), `START's reason does not say where the control is: "${why}"`);
});
await test("position unknown: the sheet names MOUNT for the pad key, and the rest of the controls stay", () => {
  assert(/go back to MOUNT/.test(block()), `no by-eye case: ${block()}`);
  assert(/hold a pad key to bring it home by eye/.test(block()), `no by-eye case: ${block()}`);
  assert(new RegExp(`If the tube is at its home or park position now, press ${TRUST_POSITION_LABEL}`).test(block()),
    `no home case: ${block()}`);
  assert(q('[data-testid="polar-solve"]') && q('[data-testid="polar-pause"]') && stopBtn(),
    "the rest of the action row went with START");
});
await test("position unknown: the sheet advises no goto, slew or move to a target", () => {
  const said = `${block()} ${String(startBtn().getAttribute("title") ?? "")}`;
  assert(block().length > 0, "no block to read");
  for (const re of [/\bgo ?to\b/i, /\bslew/i, /\bpoint(ing)? at\b/i, /\btarget/i, /plate/i]) {
    assert(!re.test(said), `the position-unknown copy says ${re}: ${said}`);
  }
});
await test("position unknown: pressing the locked START says why and posts nothing", async () => {
  posts.length = 0;
  act(() => { useStore.setState({ toasts: [] } as never); });
  await click(startBtn());
  await settle();
  eq(startPosts(), 0, "a locked START reached the rig");
  assert(toasts().some(t => String(t.title).includes(TRUST_POSITION_LABEL)),
    `a locked START said nothing about ${TRUST_POSITION_LABEL}: ${JSON.stringify(toasts().map(t => t.title))}`);
});

await test("TRUST POSITION asks first, in the shared words, and a cancel posts nothing", async () => {
  posts.length = 0;
  await click(trustBtn());
  await settle();
  const req = confirmReq();
  assert(req != null, "one tap attested the tube's position with no confirmation");
  eq(req.title, TRUST_POSITION_CONFIRM_TITLE, "the confirmation title");
  eq(req.body, TRUST_POSITION_CONFIRM_BODY, "the confirmation body");
  eq(trustPosts(), 0, "the attestation was posted before the operator confirmed");
  await answerConfirm(false);
  eq(trustPosts(), 0, "a cancelled confirmation still posted");
  assert(trustBtn() != null, "cancelling cleared the control");
});

await test("a confirmed attestation posts once, and the sheet lets go only when the status says so", async () => {
  posts.length = 0;
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  eq(trustPosts(), 1, "the attestation never reached the route (or reached it twice)");
  assert(trustBtn() != null && locked(startBtn()),
    "the sheet let go on the operator's own word, before the rig said so");
  patchStatus({}, { position_known: true });
  await settle();
  eq(trustBtn(), null, "TRUST POSITION outlived the latch");
  assert(!locked(startBtn()), "START did not come back with the position");
});

await test("a driver that declines is not reported as trusted, and the sheet does not send the operator to a mode it does not have", async () => {
  seed({ position_known: false });
  await mountSheet();
  trustAnswer = { ok: true, position_known: false };
  posts.length = 0;
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  trustAnswer = { ok: true, position_known: true };
  eq(trustPosts(), 1, "the attestation never reached the route");
  assert(!toasts().some(t => t.title === "Position trusted"),
    "a declined attestation was announced as a success");
  const alert = String(q('[role="alert"]')?.textContent ?? "");
  assert(/did not accept that its position is known/.test(alert), `no refusal shown: "${alert}"`);
  assert(!/switch to pro/i.test(alert), `the new UI has no Pro to switch to: "${alert}"`);
  assert(!/\bgoto\b|\bslew/i.test(alert), `the declined line advises a move: "${alert}"`);
  assert(trustBtn() != null && locked(startBtn()), "the sheet let go on a driver that declined");
});

await test("a viewer cannot attest, and the button says why", async () => {
  seed({ position_known: false }, { principal: VIEWER });
  await mountSheet();
  assert(trustBtn() != null, "TRUST POSITION is hidden from a viewer");
  assert(locked(trustBtn()), "TRUST POSITION is live for a viewer");
  assert(/needs operator or admin access/.test(String(trustBtn().getAttribute("title") ?? "")),
    "the button does not say why");
  posts.length = 0;
  await click(trustBtn());
  await settle();
  eq(confirmReq(), null, "a viewer's press opened the attestation");
  eq(trustPosts(), 0, "a viewer's press reached the rig");
});

await test("a mount that is moving cannot be attested for", async () => {
  seed({ position_known: false, slewing: true });
  await mountSheet();
  assert(trustBtn() != null, "TRUST POSITION is not on the sheet while the mount is moving");
  assert(locked(trustBtn()), "TRUST POSITION is live while the mount is moving");
  assert(/moving/.test(String(trustBtn().getAttribute("title") ?? "")),
    `the button does not say the mount is moving: "${trustBtn().getAttribute("title")}"`);
});

// ====================================================== a start that raced
await test("a start that raced the status and was refused shows the attestation, not the pad-key advice in a toast", async () => {
  seed({ position_known: true });
  startRefusal = { detail: SERVER_REFUSAL, code: "position_unknown" };
  await mountSheet();
  posts.length = 0;
  await click(startBtn());
  await settle(60);
  startRefusal = null;
  assert(startPosts() === 1, "the fixture never sent the start");
  assert(trustBtn() != null, "a refused start left no way to act on its advice");
  assert(locked(startBtn()), "START is offered again beside the refusal");
  eq(errorToasts().length, 0, `the refusal reached an error toast: ${errorToasts().map(t => t.title).join(" | ")}`);
  assert(!/STARTING/.test(String(startBtn().textContent)), "the start latch outlived a refusal that never became a run");
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  patchStatus({}, { position_known: true });
  await settle();
  assert(trustBtn() == null && !locked(startBtn()), "a trusted position left the refusal standing");
});

await test("a different refusal is still an error toast, and START stays", async () => {
  seed({ position_known: true });
  startRefusal = { detail: "polar alignment is already running", code: "lane_busy" };
  await mountSheet();
  await click(startBtn());
  await settle(60);
  startRefusal = null;
  eq(trustBtn(), null, "an unrelated refusal brought up the attestation");
  assert(!locked(startBtn()), "START did not come back after an unrelated refusal");
  assert(errorToasts().some(t => /already running/.test(String(t.title))), "an unrelated refusal was swallowed");
});

// ================================================================ a live run
await test("a run already in progress keeps its controls, and STOP stays reachable, when the status flips", async () => {
  seed({ position_known: true });
  await mountSheet();
  publishPolar("running");
  patchStatus({}, { position_known: false });
  await settle();
  eq(trustBtn(), null, "the attestation replaced the controls of a live alignment");
  assert(stopBtn() != null && !locked(stopBtn()), "STOP is out of service during a live alignment");
});

await test("a stale link says nothing either way", async () => {
  seed({ position_known: false }, { telemetryStale: true });
  await mountSheet();
  eq(trustBtn(), null, "the attestation is offered on the strength of a stale status");
});

if (root) await act(async () => root!.unmount());
const total = passed + failed;
console.log(`w23PolarSheetTrustPosition: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
