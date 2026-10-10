// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w22SlewWaitPositionUnknown.test.tsx - WP-202 / #986: a guided goto whose
// position gate closes AFTER the route has answered.
//
//   Run directly:  npx tsx src/guided/__tests__/w22SlewWaitPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `POST /api/mount/goto` answers `{started}` and spawns the move. The hub asks
// the position gate once more at its own motion seam, under the motion lock,
// and a latch set meanwhile (an AM5 link reopen that reads the home pole) makes
// it send nothing: the status then says `position_known === false`, the mount
// never slews, and `slewAndWait` waited out its whole 180 s deadline for an
// arrival that could not come, ending in "The telescope hasn't confirmed
// arrival". The attestation the pages show for this state (#929) stayed hidden
// behind "Pointing and checking" the whole time.
//
// The fixture is that race: the route answers `{started}` and the status is
// flipped to the home reading with the flag false a beat later, the mount never
// slewing. Every case first asks the same question of a status that does NOT
// flip (the control), so "it ended early" cannot be a wait that always ends.
// The deadline cases are bounded by an abort a few seconds out, so the unfixed
// code fails in seconds with its own words instead of sitting out 180 s.
//
// Named mutants (each makes the tests named in brackets fail):
//   wait_blind    -- in guided/motion.ts delete the line that starts
//                    `if (!positionKnown(mount)) throw new ApiError(`. The wait
//                    ends only on the abort. [the three slewAndWait cases and
//                    both mounted pages]
//   not_the_page  -- in the same line replace `POSITION_UNKNOWN_CODE` with
//                    `"other"`. The wait still ends early, but in an error the
//                    pages do not recognise, so they print it instead of the
//                    attestation. [the code assertion and both mounted pages]

/* eslint-disable @typescript-eslint/no-explicit-any */
import { registerHooks } from "node:module";
// The sky dome behind the focus page polls the cloud model and draws canvas;
// nothing here grades it.
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

// A target well away from the pole, so the home reading never matches it.
const TARGET = { id: "M 13", name: "Test cluster", type: "Globular cluster", ra_hours: 16, dec_deg: 36, alt: 60, az: 200 };
const FIELD = { ra_hours: 8, dec_deg: 40, alt: 55, az: 210, clearance_deg: 25 };
const PLAN = { field: FIELD, arc_deg: 24, expires_at: Date.now() / 1000 + 3600, config_version: 11, reason: null };
// Made-up numbers; the home reading is the pole, wherever the tube is.
const HOME_READING = { ra_hours: 11.2, dec_deg: 89.9 };
const START = { ra_hours: 5, dec_deg: 10 };

const posts: string[] = [];
/** What the route does after it answers {started}: `null` leaves the status
 *  alone (the control); a number flips it to the unknown-position reading after
 *  that many ms, the mount never having slewed. */
let flipAfterMs: number | null = null;
/** True: the mount is seen slewing first, and the flip lands mid-move. */
let slewFirst = false;
const reply = (body: unknown, status = 200): any => ({
  ok: status < 400, status, statusText: "OK",
  headers: { get: () => null }, json: async () => body,
});
const mountBlock = (over: Record<string, unknown>) =>
  ({ ...START, slewing: false, parked: false, position_known: true, pointing: { verified: true }, ...over });
const statusWith = (mount: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  mode: "sim", busy_lanes: [],
  connected: { camera: { connected: true }, telescope: { connected: true } },
  mount, ...extra,
});
const flipToUnknown = () => useStore.setState({
  status: statusWith(mountBlock({ ...HOME_READING, position_known: false, pointing: { verified: false } })),
} as never);
g.fetch = async (url: any, init: any = {}) => {
  const path = String(url);
  if ((init.method ?? "GET").toUpperCase() === "POST") posts.push(path);
  if (path === "/api/guided/first-targets") return reply({ picks: [TARGET], reason: null, simulation: true });
  if (path === "/api/guided/polar-field") return reply(PLAN);
  if (path === "/api/mount/goto" && flipAfterMs !== null) {
    if (slewFirst) {
      setTimeout(() => useStore.setState({
        status: statusWith(mountBlock({ slewing: true }), { busy_lanes: ["goto"] }),
      } as never), Math.max(0, flipAfterMs - 80));
    }
    setTimeout(flipToUnknown, flipAfterMs);
  }
  return reply({ started: "goto" });
};

const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { useGuidedSetup } = await import("../setup");
const { ApiError } = await import("../../api");
const { slewAndWait } = await import("../motion");
const FirstImage = (await import("../GuidedFirstImage")).default;
const { GuidedFocusPrep } = await import("../GuidedFocusPrep");
const { TRUST_POSITION_LABEL } = await import("../../lib/slewController");

let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }

const OPERATOR = { role: "operator", email: null, caps: ["view.status", "control.mount", "control.capture"] };
function seed(setup: Record<string, unknown> = {}): void {
  act(() => {
    useGuidedSetup.setState({ location: true, horizon: true, focus: true, alignment: true, field: null, recovering: false, ...setup } as any);
    useStore.setState({
      wsPhase: "up", telemetryStale: false, authGate: "open", toasts: [], confirm: null,
      config: { version: 11 }, previews: [], livePreviewId: null,
      sequence: { state: "idle" }, polar: { state: "idle" }, focus: null, filterOffsetsLearn: { state: "idle" },
      principal: OPERATOR,
      status: statusWith(mountBlock({})),
    } as never);
  });
}

/** Run one guided wait to its end, or to the abort `capMs` out. */
async function waitOutcome(center: boolean, capMs: number): Promise<{ error: unknown; ms: number }> {
  const abort = new AbortController();
  const cap = setTimeout(() => abort.abort(), capMs);
  const t0 = Date.now();
  let error: unknown = null;
  try { await slewAndWait({ ra_hours: TARGET.ra_hours, dec_deg: TARGET.dec_deg }, center, abort.signal); }
  catch (e) { error = e; }
  clearTimeout(cap);
  return { error, ms: Date.now() - t0 };
}
const isPositionRefusal = (e: unknown): boolean =>
  e instanceof ApiError && e.status === 409 && e.code === "position_unknown";

// ====================================================== slewAndWait itself
seed(); flipAfterMs = null; slewFirst = false;
await test("control: a status that stays known keeps the wait going until it is stopped", async () => {
  const { error } = await waitOutcome(false, 1200);
  assert(error instanceof Error && /Waiting stopped/.test(error.message),
    `the wait ended on its own for a position that never changed: ${String((error as Error)?.message ?? error)}`);
  assert(!isPositionRefusal(error), "a known position was reported as a position refusal");
});

for (const center of [false, true]) {
  seed(); flipAfterMs = 150; slewFirst = false;
  await test(`${center ? "centred" : "plain"} goto: the latch set after the route answered ends the wait at once, as the position refusal`, async () => {
    const { error, ms } = await waitOutcome(center, 4000);
    assert(error != null, "the wait completed as an arrival from a position nobody vouches for");
    assert(!/Waiting stopped/.test(String((error as Error).message)),
      `the wait was still going when it was stopped ${ms} ms in; it should have ended on the status: ${(error as Error).message}`);
    assert(isPositionRefusal(error), `the wait ended in an error the pages do not recognise as the position refusal: ${(error as Error).message}`);
    assert(ms < 2500, `the refusal took ${ms} ms to surface`);
  });
}

seed(); flipAfterMs = 400; slewFirst = true;
await test("a latch set mid-move ends the wait the same way", async () => {
  const { error, ms } = await waitOutcome(false, 4000);
  assert(isPositionRefusal(error), `mid-move: ${String((error as Error)?.message ?? error)}`);
  assert(ms < 2500, `the refusal took ${ms} ms to surface`);
});
slewFirst = false; flipAfterMs = null;

// ================================================ the pages, mounted
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
/** A click whose handler outlives the press (a goto wait): the wait stays inside one act. */
const clickAndWait = async (node: any, ms: number) => { await act(async () => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); await new Promise(r => setTimeout(r, ms)); }); };
const alertText = (): string => [...container.querySelectorAll('[role="alert"]')].map((n: any) => n.textContent).join(" | ");

const POINT_IMAGE = "Point telescope at this target";
const POINT_FIELD = "Point telescope here";
// Long enough for two polls of the wait (500 ms apart) after the flip at 150 ms,
// far short of the 180 s deadline the unfixed wait runs to.
const AFTER_FLIP_MS = 1400;

seed(); flipAfterMs = null;
await show(h(FirstImage));
await settle();
await click(buttons().find(b => (b.textContent ?? "").includes("Test cluster")));
await settle();
await test("control (image): a known position offers Point and no attestation", () => {
  assert(button(POINT_IMAGE) && !button(POINT_IMAGE).disabled, "no live Point button");
  assert(!trustBtn(), "TRUST POSITION is offered to a mount that knows where it points");
});
flipAfterMs = 150;
posts.length = 0;
await clickAndWait(button(POINT_IMAGE), AFTER_FLIP_MS);
await test("image: a latch set after the route answered shows the attestation at once, not after the deadline", () => {
  assert(posts.includes("/api/mount/goto"), "the fixture never sent the goto");
  assert(trustBtn(), `the page is still pointing and checking ${AFTER_FLIP_MS} ms after the status said the position is unknown: ${text().slice(0, 160)}`);
  assert(!button(POINT_IMAGE), "Point is offered beside the attestation");
  assert(!/hasn't confirmed arrival|Pointing wasn't confirmed/.test(text()), `the wait's own error reached the page: ${alertText()}`);
  assert(alertText() === "", `an error line is showing beside the attestation: ${alertText()}`);
});
flipAfterMs = null;

seed({ focus: false, alignment: false });
await show(h(GuidedFocusPrep));
await settle();
await click(button("Find an alignment field"));
await settle();
await click(container.querySelector('input[type="checkbox"]'));
await test("control (focus): a known position offers Point and no attestation", () => {
  assert(button(POINT_FIELD) && !button(POINT_FIELD).disabled, "no live Point button");
  assert(!trustBtn(), "TRUST POSITION is offered to a mount that knows where it points");
});
flipAfterMs = 150;
posts.length = 0;
await clickAndWait(button(POINT_FIELD), AFTER_FLIP_MS);
await test("focus: a latch set after the route answered shows the attestation at once, not after the deadline", () => {
  assert(posts.includes("/api/mount/goto"), "the fixture never sent the goto");
  assert(trustBtn(), `the page is still moving to the field ${AFTER_FLIP_MS} ms after the status said the position is unknown: ${text().slice(0, 160)}`);
  assert(!button(POINT_FIELD), "Point is offered beside the attestation");
  assert(alertText() === "", `an error line is showing beside the attestation: ${alertText()}`);
});
flipAfterMs = null;

// Unmounting aborts any wait a failing case left running, so the process can end.
if (root) await act(async () => root!.unmount());
const total = passed + failed;
console.log(`w22SlewWaitPositionUnknown: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
if (failed) process.exitCode = 1;
