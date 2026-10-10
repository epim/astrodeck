// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w21GuidedTrustPosition.test.tsx - WP-187 / #929: the guided flows while the
// mount does not know where it points.
//
//   Run directly:  npx tsx src/guided/__tests__/w21GuidedTrustPosition.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// After a power cycle the AM5 reports its HOME position wherever the tube is
// (`status.mount.position_known === false`, #144) and the server refuses every
// goto with 409 `position_unknown`, advising TRUST POSITION or a pad key. The
// two guided pages that point the telescope, the first image and the alignment
// field, offered their point button anyway and showed that advice in an error
// line, with neither control anywhere in Guided. #929: the operator's only
// exit was to leave Guided.
//
// This mounts the real GuidedFirstImage and GuidedFocusPrep against a fake rig
// and asks, per state: is the point button offered (it must not be), is the
// attestation offered in its place, does it ask in the words the other UIs use
// before it posts, and does the page let go only when the STATUS says so.
// Every case asks the same question of a known position first (the control).
//
// Named mutants (each makes a named test below fail):
//   gate_off      -- in guided/GuidedTrustPosition.tsx replace
//                    `needed: unknown || refused,` with `needed: false,`.
//                    The point button comes back beside a refusal it cannot win.
//   refusal_blind -- in the same file replace
//                    `refusedBy: (e) => { if (!isPositionRefusal(e)) return false; setRefused(true); return true; },`
//                    with `refusedBy: () => false,`. The raced 409 reaches the
//                    error line again, advising a pad key that is not on screen.
//   no_confirm    -- in the same file replace `if (!sure) return;` with
//                    `void sure;`. One tap attests the tube's position.
//   viewer_live   -- in the same file replace `if (!canMove) return "Saying where` with
//                    `if (false) return "Saying where`. A viewer's button is live.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { registerHooks } from "node:module";
// The sky dome behind the focus page polls the cloud model and draws canvas;
// nothing here grades it, and the first-image page does not load it.
registerHooks({
  load(url: string, context: any, next: any) {
    if (url.endsWith("/guided/GuidedFocusSky.tsx")) {
      return { format: "module", shortCircuit: true, source: "export function GuidedFocusSky(){return null}" };
    }
    return next(url, context);
  },
} as any);

const { JSDOM } = await import("jsdom");
const dom = new JSDOM(`<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
const g = globalThis as any;
for (const k of ["window", "document", "navigator", "HTMLElement", "Event", "MouseEvent", "localStorage"]) {
  Object.defineProperty(g, k, { value: k === "window" ? win : win[k], configurable: true, writable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// A field that is well away from the pole: the home reading never matches it.
const TARGET = { id: "M 13", name: "Test cluster", type: "Globular cluster", ra_hours: 16, dec_deg: 36, alt: 60, az: 200 };
const FIELD = { ra_hours: 8, dec_deg: 40, alt: 55, az: 210, clearance_deg: 25 };
const PLAN = { field: FIELD, arc_deg: 24, expires_at: Date.now() / 1000 + 3600, config_version: 11, reason: null };
/** The server's own words for a refused goto: it advises a pad key. */
const SERVER_REFUSAL = "Mount position is unknown. Tube really at home: Trust position. If not, hold a pad key to bring it home by eye, then Trust position.";

const posts: string[] = [];
let gotoRefused = false;
/** What `/api/mount/trust-position` answers: the driver's own verdict afterwards. */
let trustAnswer: unknown = { ok: true, position_known: true };
/** While set, the attestation stays ON THE WIRE: the window the in-flight lock is for. */
let trustGate: Promise<void> | null = null;
const reply = (body: unknown, status = 200): any => ({
  ok: status < 400, status, statusText: status === 409 ? "Conflict" : "OK",
  headers: { get: () => null }, json: async () => body,
});
g.fetch = async (url: any, init: any = {}) => {
  const path = String(url);
  if ((init.method ?? "GET").toUpperCase() === "POST") posts.push(path);
  if (path === "/api/guided/first-targets") return reply({ picks: [TARGET], reason: null, simulation: true });
  if (path === "/api/guided/polar-field") return reply(PLAN);
  if (path === "/api/mount/trust-position") {
    if (trustGate) await trustGate;
    return reply(trustAnswer);
  }
  if (path === "/api/mount/goto") {
    if (gotoRefused) return reply({ detail: { detail: SERVER_REFUSAL, code: "position_unknown" } }, 409);
    const at = (slewing: boolean) => ({ ...(useStore.getState().status as any), mount: { ...FIELD, ...TARGET, slewing, parked: false, position_known: true, pointing: { verified: !slewing } } });
    useStore.setState({ status: at(true) } as never);
    useStore.setState({ status: at(false) } as never);
  }
  return reply({ started: "ok" });
};

const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { useGuidedSetup } = await import("../setup");
const FirstImage = (await import("../GuidedFirstImage")).default;
const { GuidedFocusPrep } = await import("../GuidedFocusPrep");
const {
  TRUST_POSITION_CONFIRM_BODY, TRUST_POSITION_CONFIRM_LABEL, TRUST_POSITION_CONFIRM_TITLE,
  TRUST_POSITION_LABEL,
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
      config: { version: 11 }, previews: [], livePreviewId: null,
      principal: OPERATOR,
      status: {
        mode: "sim", busy_lanes: [],
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

// The setup facts a walkthrough has reached by each page; the image page needs focus and alignment done.
const facts = (focus: boolean) => act(() => { useGuidedSetup.setState({ location: true, horizon: true, focus, alignment: focus, field: null, recovering: false } as any); });
facts(true);

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
const button = (label: string): any => buttons().find(b => (b.textContent ?? "").trim() === label);
const trustBtn = (): any => button(TRUST_POSITION_LABEL);
const click = async (node: any) => { await act(async () => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); }); };
/** A click whose handler outlives the press (a goto poll): the wait stays inside one act. */
const clickAndWait = async (node: any, ms: number) => { await act(async () => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); await new Promise(r => setTimeout(r, ms)); }); };
const confirmReq = (): any => (useStore.getState() as any).confirm;
const answerConfirm = async (ok: boolean) => { await act(async () => { useStore.getState().resolveConfirm(ok); }); await settle(); };
const trustPosts = () => posts.filter(p => p === "/api/mount/trust-position").length;
const alertText = (): string => [...container.querySelectorAll('[role="alert"]')].map((n: any) => n.textContent).join(" | ");

const POINT_IMAGE = "Point telescope at this target";
const POINT_FIELD = "Point telescope here";

// ====================================================================== image
async function openImage(): Promise<void> {
  await show(h(FirstImage));
  await settle();
  await click(buttons().find(b => (b.textContent ?? "").includes("Test cluster")));
  await settle();
}

seed({ position_known: true });
await openImage();
await test("control: a mount that knows where it points offers Point and no attestation", () => {
  assert(button(POINT_IMAGE) && !button(POINT_IMAGE).disabled, "no live Point button for a known position");
  eq(trustBtn(), undefined, "TRUST POSITION is offered to a mount that knows where it points");
});
seed({ ra_hours: 5, dec_deg: 10 });
await openImage();
await test("control: an ABSENT flag (an engine older than #144) reads as known", () => {
  assert(button(POINT_IMAGE), "no Point button for an absent flag");
  eq(trustBtn(), undefined, "an absent flag offered the attestation");
});

seed({ position_known: false });
await openImage();
await test("image, position unknown: no Point button, the attestation sits in its place", () => {
  eq(button(POINT_IMAGE), undefined, "the page offers a goto the server will refuse");
  assert(trustBtn(), "no TRUST POSITION control in the guided first-image flow");
  assert(container.querySelector('[data-testid="guided-position-unknown"]'), "the block is not marked");
});
await test("image, position unknown: the page says what to do for a tube that is not at home, using controls Pro has", () => {
  assert(/If the telescope is at its home or park position now, press TRUST POSITION/.test(text()), `no home case: ${text()}`);
  assert(/switch to Pro, open Mount and hold a pad key to bring it home by eye/.test(text()), `no by-eye case: ${text()}`);
});
await test("image, position unknown: the page advises no goto, slew or move to a target", () => {
  const block = String(container.querySelector('[data-testid="guided-position-unknown"]')?.textContent ?? "");
  assert(block.length > 0, "no block to read");
  for (const re of [/\bgo ?to\b/i, /\bslew/i, /\bpoint(ing)? at\b/i, /\btarget/i, /plate/i]) {
    assert(!re.test(block), `the position-unknown block says ${re}: ${block}`);
  }
});

await test("image: TRUST POSITION asks first, in the words the other UIs use, and a cancel posts nothing", async () => {
  posts.length = 0;
  await click(trustBtn());
  await settle();
  const req = confirmReq();
  assert(req != null, "one tap attested the tube's position with no confirmation");
  eq(req.title, TRUST_POSITION_CONFIRM_TITLE, "the confirmation title");
  eq(req.body, TRUST_POSITION_CONFIRM_BODY, "the confirmation body");
  eq(req.confirmLabel, TRUST_POSITION_CONFIRM_LABEL, "the confirmation button");
  eq(trustPosts(), 0, "the attestation was posted before the operator confirmed");
  await answerConfirm(false);
  eq(trustPosts(), 0, "a cancelled confirmation still posted");
  assert(trustBtn(), "cancelling cleared the control");
});

await test("image: a confirmed attestation posts once, and the page lets go only when the status says so", async () => {
  posts.length = 0;
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  eq(trustPosts(), 1, "the attestation never reached the route (or reached it twice)");
  assert(((useStore.getState() as any).toasts as any[]).some(t => t.title === "Position trusted"), "no success toast");
  assert(trustBtn() && !button(POINT_IMAGE), "the page let go on the operator's own word, before the rig said so");
  patchStatus({}, { position_known: true });
  await settle();
  eq(trustBtn(), undefined, "TRUST POSITION outlived the latch");
  assert(button(POINT_IMAGE) && !button(POINT_IMAGE).disabled, "Point did not come back with the position");
});

await test("image: a driver that declines is not reported as trusted", async () => {
  patchStatus({}, { position_known: false });
  await settle();
  trustAnswer = { ok: true, position_known: false };
  act(() => { useStore.setState({ toasts: [] } as never); });
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  trustAnswer = { ok: true, position_known: true };
  assert(/did not accept that its position is known/.test(alertText()), `no refusal shown: ${alertText()}`);
  assert(!((useStore.getState() as any).toasts as any[]).some(t => t.title === "Position trusted"), "a declined attestation was announced as a success");
  assert(!button(POINT_IMAGE) && trustBtn(), "the page let go on a driver that declined");
  assert(!/\bgoto\b|\bslew/i.test(alertText()), `the declined line advises a move: ${alertText()}`);
});

await test("image: a viewer cannot attest, and the button says why", async () => {
  seed({ position_known: false }, { principal: VIEWER });
  await openImage();
  assert(trustBtn(), "TRUST POSITION is hidden from a viewer");
  eq(trustBtn().disabled, true, "TRUST POSITION is live for a viewer");
  assert(/needs operator or admin access/.test(text()), "the button does not say why");
  posts.length = 0;
  await click(trustBtn());
  await settle();
  eq(confirmReq(), null, "a viewer's press opened the attestation");
  eq(trustPosts(), 0, "a viewer's press reached the rig");
});

await test("image: TRUST POSITION is out of service while the mount moves, and while an attestation is on the wire", async () => {
  seed({ position_known: false, slewing: true });
  await openImage();
  eq(trustBtn().disabled, true, "TRUST POSITION is live while the mount is slewing");
  patchStatus({}, { slewing: false });
  patchStatus({ busy_lanes: ["goto"] });
  eq(trustBtn().disabled, true, "TRUST POSITION is live while the goto lane is busy");
  patchStatus({ busy_lanes: [] });
  eq(trustBtn().disabled, false, "TRUST POSITION stayed locked at rest");
  let release: () => void = () => {};
  trustGate = new Promise<void>(res => { release = res; });
  posts.length = 0;
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  eq(trustBtn().disabled, true, "a second press is possible while the first is on the wire");
  await click(trustBtn());
  eq(confirmReq(), null, "a second press asked again");
  release(); trustGate = null;
  await settle();
  eq(trustPosts(), 1, "the attestation was posted more than once");
});

await test("image: a goto that raced the status and was refused shows the attestation, not the pad-key advice", async () => {
  seed({ position_known: true, ra_hours: 1, dec_deg: 1 });
  gotoRefused = true;
  await openImage();
  posts.length = 0;
  await clickAndWait(button(POINT_IMAGE), 550);
  gotoRefused = false;
  assert(posts.includes("/api/mount/goto"), "the fixture never sent the goto");
  assert(trustBtn(), "a refused goto left no way to act on its advice");
  eq(button(POINT_IMAGE), undefined, "Point is offered again beside the refusal");
  assert(alertText() === "" && !text().includes(SERVER_REFUSAL), `the refusal reached an error line: ${alertText()}`);
  await click(trustBtn());
  await settle();
  await answerConfirm(true);
  assert(!trustBtn() && button(POINT_IMAGE), "a trusted position left the refusal standing");
});

// ======================================================================= focus
facts(false);
async function openFocus(): Promise<void> {
  await show(h(GuidedFocusPrep));
  await settle();
  await click(button("Find an alignment field"));
  await settle();
}

seed({ position_known: true });
await openFocus();
await test("control: focus, a known position offers the clearance box and Point, and no attestation", () => {
  assert(button(POINT_FIELD), "no Point button for a known position");
  assert(container.querySelector('input[type="checkbox"]'), "no clearance box");
  eq(trustBtn(), undefined, "TRUST POSITION is offered to a mount that knows where it points");
});

seed({ position_known: false });
await openFocus();
await test("focus, position unknown: no Point and no clearance box, the attestation sits in their place", () => {
  eq(button(POINT_FIELD), undefined, "the page offers a goto the server will refuse");
  eq(container.querySelector('input[type="checkbox"]'), null, "the clearance box stands for a move that cannot be made");
  assert(trustBtn(), "no TRUST POSITION control in the guided focus flow");
  assert(button("Check the sky again"), "finding a field is no longer possible");
});

await test("focus: TRUST POSITION asks in the shared words, posts once, and the page lets go on the status", async () => {
  posts.length = 0;
  await click(trustBtn());
  await settle();
  eq(confirmReq()?.title, TRUST_POSITION_CONFIRM_TITLE, "the confirmation title");
  eq(confirmReq()?.body, TRUST_POSITION_CONFIRM_BODY, "the confirmation body");
  eq(trustPosts(), 0, "posted before the operator confirmed");
  await answerConfirm(true);
  eq(trustPosts(), 1, "the attestation never reached the route");
  assert(trustBtn() && !button(POINT_FIELD), "the page let go before the rig said the position is known");
  patchStatus({}, { position_known: true });
  await settle();
  eq(trustBtn(), undefined, "TRUST POSITION outlived the latch");
  assert(button(POINT_FIELD), "Point did not come back");
});

await test("focus: a goto that raced the status and was refused shows the attestation, not the pad-key advice", async () => {
  seed({ position_known: true, ra_hours: 1, dec_deg: 1 });
  gotoRefused = true;
  await openFocus();
  await click(container.querySelector('input[type="checkbox"]'));
  posts.length = 0;
  await clickAndWait(button(POINT_FIELD), 550);
  gotoRefused = false;
  assert(posts.includes("/api/mount/goto"), "the fixture never sent the goto");
  assert(trustBtn(), "a refused goto left no way to act on its advice");
  assert(alertText() === "" && !text().includes(SERVER_REFUSAL), `the refusal reached an error line: ${alertText()}`);
});

if (root) await act(async () => root!.unmount());
const total = passed + failed;
console.log(`w21GuidedTrustPosition: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
