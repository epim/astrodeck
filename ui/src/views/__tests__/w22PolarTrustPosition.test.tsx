// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w22PolarTrustPosition.test.tsx - WP-202 / #985: the Polar tool while the mount
// does not know where it points, in Pro and in the Guided alignment step.
//
//   Run directly:  npx tsx src/views/__tests__/w22PolarTrustPosition.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `POST /api/polar/start` aims every leg of its arc from the believed position,
// so the server refuses it with 409 `position_unknown` while the mount reports
// its home position wherever the tube is (#888), and the refusal advises TRUST
// POSITION or a pad key. PolarView hosted neither: Start Alignment was offered
// anyway, the refusal reached the operator as an error toast naming a control
// that was not on the page, and #929's answer for the guided gotos never reached
// this page. This mounts the real PolarView against a fake rig and asks, per
// state: is Start offered (it must not be), is the attestation offered in its
// place, does it ask in the shared words before it posts, does a start that
// raced the status land on the attestation and not in a toast, and does the page
// let go only when the STATUS says so. Every case asks a known position first.
//
// Named mutants (each makes a named test below fail):
//   gate_off      -- in views/PolarView.tsx replace
//                    `{position.needed && !live ? <GuidedTrustPosition` with
//                    `{false ? <GuidedTrustPosition`. Start comes back beside a
//                    refusal it cannot win. [the unknown-position cases]
//   refusal_blind -- in the same file replace
//                    `if (!position.refusedBy(e)) throw e;` with `throw e;`. The
//                    raced 409 reaches an error toast again. [the raced cases]
//   pro_words     -- in guided/GuidedTrustPosition.tsx replace `inPro ? "open Mount"`
//                    with `false ? "open Mount"`. The Pro page tells the operator
//                    to switch to the mode it is already in. [the Pro words case]

/* eslint-disable @typescript-eslint/no-explicit-any */
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.HTMLCanvasElement.prototype.getContext = () => null;
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.scrollTo = function () {};
const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
]) {
  Object.defineProperty(g, k, { value: k === "window" ? win : win[k], writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

/** The server's own words for the refusal: it advises a pad key. */
const SERVER_REFUSAL = "Mount position is unknown. Tube really at home: Trust position. If not, hold a pad key to bring it home by eye, then Trust position.";

const posts: string[] = [];
/** What /api/polar/start answers: null is a started run, else the 409 it refuses with. */
let startRefusal: null | { detail: string; code: string } = null;
/** While set, the attestation stays ON THE WIRE. */
let trustGate: Promise<void> | null = null;
const reply = (body: unknown, status = 200): any => ({
  ok: status < 400, status, statusText: status === 409 ? "Conflict" : "OK",
  headers: { get: () => null }, json: async () => body,
});
g.fetch = async (url: any, init: any = {}) => {
  const path = String(url);
  if ((init.method ?? "GET").toUpperCase() === "POST") posts.push(path);
  if (path === "/api/polar/start") {
    if (startRefusal) return reply({ detail: startRefusal }, 409);
    return reply({ started: true, source: "native" });
  }
  if (path === "/api/mount/trust-position") {
    if (trustGate) await trustGate;
    return reply({ ok: true, position_known: true });
  }
  return reply({});
};

const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { useExperience } = await import("../../guided/experience");
const { useGuidedSetup } = await import("../../guided/setup");
const PolarView = (await import("../PolarView")).default;
const {
  TRUST_POSITION_CONFIRM_BODY, TRUST_POSITION_CONFIRM_TITLE, TRUST_POSITION_LABEL,
} = await import("../../lib/slewController");

let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg} (expected ${String(b)}, got ${String(a)})`);
}

const OPERATOR = { role: "operator", email: null, caps: ["view.status", "control.mount", "control.capture"] };
const VIEWER = { role: "viewer", email: null, caps: ["view.status"] };
// Made-up numbers; the home reading is the pole, wherever the tube is.
const HOME_READING = { ra_hours: 11.2, dec_deg: 89.9 };

function seed(mount: Record<string, unknown>, over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      wsPhase: "up", telemetryStale: false, authGate: "open", toasts: [], confirm: null,
      principal: OPERATOR,
      polar: { state: "idle", az_error: 0, alt_error: 0, total_error: 0, progress: 0, message: "", source: "native" },
      focus: null, filterOffsetsLearn: { state: "idle" }, sequence: { state: "idle" },
      status: {
        mode: "sim", busy_lanes: [], looping: false,
        connected: { camera: { connected: true }, telescope: { connected: true } },
        mount: { ...HOME_READING, slewing: false, parked: false, ...mount },
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
  useStore.setState({ polar: { state, az_error: 20, alt_error: 15, total_error: 25, progress: 0, message: "", source: "native" } } as never);
});

const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;
async function show(node: any): Promise<void> {
  if (root) await act(async () => { root!.unmount(); });
  root = createRoot(container);
  await act(async () => { root!.render(node); });
}
const settle = async (ms = 0) => { await act(async () => { await new Promise(r => setTimeout(r, ms)); }); };
const text = (): string => String(container.textContent ?? "");
const buttons = (): any[] => [...container.querySelectorAll("button")];
const byText = (re: RegExp): any => buttons().find(b => re.test((b.textContent ?? "").trim()));
const startBtn = (): any => byText(/^(Start Alignment|Starting)/);
const trustBtn = (): any => byText(new RegExp(`^${TRUST_POSITION_LABEL}$`));
const click = async (node: any) => { await act(async () => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); }); };
const confirmReq = (): any => (useStore.getState() as any).confirm;
const answerConfirm = async (ok: boolean) => { await act(async () => { useStore.getState().resolveConfirm(ok); }); await settle(); };
const trustPosts = () => posts.filter(p => p === "/api/mount/trust-position").length;
const errorToasts = (): any[] => ((useStore.getState() as any).toasts as any[]).filter(t => t.level === "error");
const block = (): string => String(container.querySelector('[data-testid="guided-position-unknown"]')?.textContent ?? "");

async function mountPro(): Promise<void> {
  act(() => { useExperience.setState({ mode: "pro", home: false, wizard: null } as any); });
  await show(h(PolarView));
  await settle();
}
async function mountGuided(): Promise<void> {
  act(() => {
    useExperience.setState({ mode: "guided", home: false, wizard: "alignment" } as any);
    useGuidedSetup.setState({ location: true, horizon: true, focus: true, alignment: false, field: null, recovering: false } as any);
  });
  await show(h(PolarView, { key: "guided" }));
  await settle();
  const skip = buttons().find(b => (b.textContent ?? "").includes("Skip lesson"));
  if (skip) await click(skip);
  await settle();
}

// ======================================================================= Pro
seed({ position_known: true });
await mountPro();
await test("control (Pro): a mount that knows where it points offers a live Start and no attestation", () => {
  assert(startBtn() && !startBtn().disabled, "no live Start Alignment for a known position");
  eq(trustBtn(), undefined, "TRUST POSITION is offered to a mount that knows where it points");
});
seed({ ra_hours: 5, dec_deg: 10 });
await mountPro();
await test("control (Pro): an ABSENT flag (an engine older than #144) reads as known", () => {
  assert(startBtn(), "no Start Alignment for an absent flag");
  eq(trustBtn(), undefined, "an absent flag offered the attestation");
});

seed({ position_known: false });
await mountPro();
await test("Pro, position unknown: no Start, the attestation sits in its place", () => {
  eq(startBtn(), undefined, "the page offers an alignment the server will refuse");
  assert(trustBtn(), "no TRUST POSITION control on the Polar tool");
  assert(container.querySelector('[data-testid="guided-position-unknown"]'), "the block is not marked");
  assert(byText(/^Stop$/) && byText(/^Pause$/), "the rest of the controls went with Start");
});
await test("Pro, position unknown: the page names Mount for the pad key and does not send the operator to the mode it is in", () => {
  assert(/open Mount and hold a pad key to bring it home by eye/.test(block()), `no by-eye case: ${block()}`);
  assert(!/switch to Pro/i.test(block()), `a Pro page tells the operator to switch to Pro: ${block()}`);
  assert(/If the telescope is at its home or park position now, press TRUST POSITION/.test(block()), `no home case: ${block()}`);
});
await test("Pro, position unknown: the page advises no goto, slew or move to a target", () => {
  assert(block().length > 0, "no block to read");
  for (const re of [/\bgo ?to\b/i, /\bslew/i, /\bpoint(ing)? at\b/i, /\btarget/i, /plate/i]) {
    assert(!re.test(block()), `the position-unknown block says ${re}: ${block()}`);
  }
});

await test("Pro: TRUST POSITION asks first, in the shared words, and a cancel posts nothing", async () => {
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
  assert(trustBtn(), "cancelling cleared the control");
});

await test("Pro: a confirmed attestation posts once, and the page lets go only when the status says so", async () => {
  posts.length = 0;
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  eq(trustPosts(), 1, "the attestation never reached the route (or reached it twice)");
  assert(trustBtn() && !startBtn(), "the page let go on the operator's own word, before the rig said so");
  patchStatus({}, { position_known: true });
  await settle();
  eq(trustBtn(), undefined, "TRUST POSITION outlived the latch");
  assert(startBtn() && !startBtn().disabled, "Start did not come back with the position");
});

await test("Pro: a viewer cannot attest, and the button says why", async () => {
  seed({ position_known: false }, { principal: VIEWER });
  await mountPro();
  assert(trustBtn(), "TRUST POSITION is hidden from a viewer");
  eq(trustBtn().disabled, true, "TRUST POSITION is live for a viewer");
  assert(/needs operator or admin access/.test(text()), "the button does not say why");
});

await test("Pro: a start that raced the status and was refused shows the attestation, not the pad-key advice in a toast", async () => {
  seed({ position_known: true });
  startRefusal = { detail: SERVER_REFUSAL, code: "position_unknown" };
  await mountPro();
  posts.length = 0;
  await click(startBtn());
  await settle(60);
  startRefusal = null;
  assert(posts.includes("/api/polar/start"), "the fixture never sent the start");
  assert(trustBtn(), "a refused start left no way to act on its advice");
  eq(startBtn(), undefined, "Start is offered again beside the refusal");
  eq(errorToasts().length, 0, `the refusal reached an error toast: ${errorToasts().map(t => t.title).join(" | ")}`);
  assert(!/Starting/.test(text()), "the start latch outlived a refusal that never became a run");
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  assert(!trustBtn() && startBtn(), "a trusted position left the refusal standing");
});

await test("Pro: a different refusal is still an error toast and Start stays", async () => {
  seed({ position_known: true });
  startRefusal = { detail: "polar alignment is already running", code: "lane_busy" };
  await mountPro();
  await click(startBtn());
  await settle(60);
  startRefusal = null;
  eq(trustBtn(), undefined, "an unrelated refusal brought up the attestation");
  assert(startBtn() && !startBtn().disabled, "Start did not come back after an unrelated refusal");
  assert(errorToasts().some(t => /already running/.test(String(t.title))), "an unrelated refusal was swallowed");
});

await test("Pro: a run already in progress keeps Start where it is and Stop reachable when the status flips", async () => {
  seed({ position_known: true });
  await mountPro();
  await publishPolar("running");
  patchStatus({}, { position_known: false });
  await settle();
  eq(trustBtn(), undefined, "the attestation replaced the controls of a live alignment");
  assert(byText(/^Stop$/) && byText(/^Stop$/).getAttribute("aria-disabled") !== "true", "Stop is out of service during a live alignment");
});

// ==================================================================== Guided
seed({ position_known: true });
await mountGuided();
await test("control (Guided): a known position offers a live Start Alignment and no attestation", () => {
  assert(startBtn() && !startBtn().disabled, `no live Start Alignment in the guided step: ${text().slice(0, 120)}`);
  eq(trustBtn(), undefined, "TRUST POSITION is offered to a mount that knows where it points");
});

seed({ position_known: false });
await mountGuided();
await test("Guided, position unknown: no Start, the attestation sits in its place, in the Guided words", () => {
  eq(startBtn(), undefined, "the guided step offers an alignment the server will refuse");
  assert(trustBtn(), "no TRUST POSITION control in the guided alignment step");
  assert(/switch to Pro, open Mount and hold a pad key to bring it home by eye/.test(block()), `no by-eye case: ${block()}`);
});

await test("Guided: a start that raced the status and was refused shows the attestation, not a toast", async () => {
  seed({ position_known: true });
  startRefusal = { detail: SERVER_REFUSAL, code: "position_unknown" };
  await mountGuided();
  posts.length = 0;
  await click(startBtn());
  await settle(60);
  startRefusal = null;
  assert(posts.includes("/api/polar/start"), "the fixture never sent the start");
  assert(trustBtn(), "a refused start left no way to act on its advice");
  eq(errorToasts().length, 0, `the refusal reached an error toast: ${errorToasts().map(t => t.title).join(" | ")}`);
});

if (root) await act(async () => root!.unmount());
const total = passed + failed;
console.log(`w22PolarTrustPosition: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
