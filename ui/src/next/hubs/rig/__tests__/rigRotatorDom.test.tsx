// rigRotatorDom.test.tsx - the ROTATOR sheet, MOUNTED (plan hub-rig.md D.2,
// T-RIG-5 row; deviation E28).
//
//   Run directly:  npx tsx src/next/hubs/rig/__tests__/rigRotatorDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH ASSERTING HERE. The sheet's stage-1 decision is that
// `RotatorCard` is mounted whole rather than re-implemented, so the tests have
// to prove two different things:
//
//   THE SHEET IS NOT AN EMPTY WRAPPER. `RotatorCard` returns `null` when no
//   rotator is connected, so a sheet that mounted it and nothing else would look
//   fine and be a dead end. The precondition asserts the sheet's own three tiles
//   AND the card's own controls, by name.
//
//   THE SPLIT GATE SURVIVES. Motion needs `control.capture`; the range of motion
//   needs `config.backend`. An operator holds the first and not the second, and
//   that is the ordinary case, not an edge one: a rotator that goes read-only
//   because the user cannot edit its range of motion would be unusable for
//   everyone but an admin.
//
//   HALT IS NOT GATED ON BUSY. Every other motion control goes dim while a move
//   is in flight; HALT is the escape hatch from exactly that state, so it stays
//   live. The test drives the card INTO its busy state with a held request and
//   then reads both, which is what makes it able to fail.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/rotator", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = () => ({
  matches: false, addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "Image", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string; body: any }
const asked: Asked[] = [];
/** A gate that suspends ONE in-flight request, so a test can observe the busy
 *  face the card wears between the press and the rig's answer. */
let release: (() => void) | null = null;
let hold = false;

g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  const method = init?.method ?? "GET";
  const u = String(url);
  let body: any = null;
  try { body = init?.body ? JSON.parse(init.body) : null; } catch { body = init?.body; }
  asked.push({ method, url: u, body });
  if (hold && u.includes("/api/rotator/move")) {
    await new Promise<void>((r) => { release = r; });
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { RotatorSheet } = await import("../sheets/rotator");

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

// ------------------------------------------------------------------ fixtures
const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.weather", "view.site_derived",
    "control.capture", "control.guide", "control.mount"],
};
const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "view.site_precise",
    "view.site_derived", "view.weather", "control.capture", "control.mount",
    "control.guide", "control.power", "config.safety", "config.solar_override",
    "config.backend", "config.site_optics", "config.alerts", "admin.users",
    "system.update"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function rotator(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "ZWO CAA", sky_deg: 42.5, mech_deg: 100.5, moving: false,
    synced: true, can_reverse: true, reverse: false,
    ...over,
  };
}

function seed(over: Record<string, unknown> = {}, statusOver: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      principal: OPERATOR,
      wsPhase: "up",
      equipConnected: true,
      toasts: [],
      config: {
        active_profile_id: null,
        rotator: { range_type: "half", range_start_deg: 20, tolerance_deg: 1.5 },
      },
      status: {
        connected: { rotator: { name: "ZWO CAA", connected: true } },
        backend_links: [{ role: "rotator", connected: true, error: null }],
        busy_lanes: [],
        rotator: rotator(),
        ...statusOver,
      },
      ...over,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(RotatorSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => [...container.querySelectorAll(sel)] as any[];
const text = () => (container.textContent || "") as string;
const button = (re: RegExp) =>
  qa("button").find((b: any) => re.test((b.textContent || "").trim())) as any;
const commands = () => asked.filter((a) => a.method !== "GET");

// ============================================================ precondition
seed();
mount();

test("the sheet mounted its own tiles AND the rotator card's controls", () => {
  // The anti-empty-wrapper guard: RotatorCard returns null with no rotator, so
  // "the sheet rendered" is not evidence that the controls did.
  assert(q('[data-testid="rig-rotator"]') != null, "no sheet marker - the sheet did not render at all");
  eq(qa('[data-testid="rotator-tiles"] .nx-readout').length, 3,
    "the readout grid is not showing the three tiles");
  assert(q('[data-testid="tile-sky-pa"]') != null, "no SKY PA tile");
  assert(q('[data-testid="tile-mechanical"]') != null, "no MECHANICAL tile");
  assert(q('[data-testid="tile-range"]') != null, "no RANGE tile");
  // The card's own row, by name - these are what the gap analysis asked for.
  assert(q('[aria-label="Target position angle, degrees"]') != null, "no MOVE TO input");
  assert(button(/^Go$/) != null, "no GO button");
  assert(button(/\+1°/) != null, "no +1 degree nudge");
  assert(button(/^Halt$/) != null, "no HALT");
  assert(button(/Rotate to PA/) != null, "no ROTATE TO PA (plate solve)");
  assert(q("[data-rotator-sync]") != null, "no SYNC TO SKY (no movement)");
  assert(q('[role="radiogroup"][aria-label="Mechanical range"]') != null,
    "no range-of-motion radiogroup");
  assert(q('[aria-label="Range start, mechanical degrees"]') != null, "no range START input");
  assert(q('[aria-label="Rotate tolerance, degrees"]') != null, "no TOLERANCE input");
});

test("the live line and the tiles read the derived mechanical offset, not a wire field", () => {
  // offset = mechanical - sky (lib/rotation.ts:105). It is NOT on the wire; a
  // sheet that printed a wire field here would print nothing at all.
  const live = q(".nx-sheet-live");
  assert(/sky PA 42\.5°/.test(live.textContent || ""),
    `the live line does not carry the sky PA: ${JSON.stringify(live.textContent)}`);
  assert(/mech 100\.5°/.test(live.textContent || ""), "the live line does not carry the mechanical angle");
  assert(/synced/.test(live.textContent || ""), "the live line does not say whether the two are related");
  assert(/offset 58\.0°/.test(q('[data-testid="tile-mechanical"]').textContent || ""),
    `the MECHANICAL tile's offset is wrong: ${JSON.stringify(q('[data-testid="tile-mechanical"]').textContent)}`);
  assert(/HALF/.test(q('[data-testid="tile-range"]').textContent || ""),
    "the RANGE tile is not showing the configured range type");
});

// ==================================================================== motion
await testAsync("pressing +1 degree commands an absolute move", async () => {
  asked.length = 0;
  click(button(/\+1°/));
  await settle();
  const moves = asked.filter((a) => a.url.includes("/api/rotator/move"));
  eq(moves.length, 1, "the +1 degree nudge did not POST /api/rotator/move");
  eq(moves[0].method, "POST", "the nudge used the wrong method");
  // 42.5 + 1, wrapped by mod360 - a nudge is an ABSOLUTE position command, which
  // is why it is computed from the live sky angle rather than sent as a delta.
  eq(moves[0].body.position_deg, 43.5, "the nudge did not send sky + 1 degree");
});

await testAsync("HALT stays live while a move is in flight and the other controls do not", async () => {
  // The never-blocked rule (plan section C). HALT exists to end the state every
  // other motion control is dimmed by, so gating it on that state is the bug.
  seed({}, { busy_lanes: ["rotator", "rotate_to_pa", "capture"] });
  mount();
  hold = true;
  click(button(/\+1°/));
  await settle();
  const halt = button(/^Halt$/);
  const nudge = button(/−1°|-1°/);
  assert(nudge != null, "the -1 degree nudge is missing");
  eq(nudge.disabled, true,
    "precondition failed: the card is not in its busy state, so HALT being live proves nothing");
  eq(halt.disabled, false, "HALT went dim while a move was in flight - it is the way out of that state");
  act(() => { release?.(); });
  await settle();
  hold = false;
  release = null;
});

// ================================================================ split gate
test("an operator keeps the rotator and loses only its range of motion", () => {
  seed();
  mount();
  eq(button(/\+1°/).disabled, false, "an operator cannot move the rotator - motion is control.capture");
  eq(button(/^Halt$/).disabled, false, "an operator cannot halt the rotator");
  const radios = qa('[role="radiogroup"][aria-label="Mechanical range"] [role="radio"]');
  eq(radios.length, 3, "the range-of-motion radiogroup is not offering three ranges");
  assert(radios.every((r: any) => r.disabled === true),
    "an operator can edit the range of motion - that write is config.backend");
  assert(/changing the range of motion needs/.test(text()),
    "the range-of-motion lock has no stated reason");
  assert(!/moving the rotator needs/.test(text()),
    "the sheet claims motion is read-only for an operator, which it is not");
});

test("an admin holds both halves of the split gate", () => {
  seed({ principal: ADMIN });
  mount();
  const radios = qa('[role="radiogroup"][aria-label="Mechanical range"] [role="radio"]');
  assert(radios.every((r: any) => r.disabled === false), "an admin cannot edit the range of motion");
  assert(!/changing the range of motion needs/.test(text()),
    "an admin is shown a lock note for a control they hold");
});

// ==================================================================== viewer
await testAsync("a viewer sees the whole sheet, is told why, and reaches the rig with nothing", async () => {
  seed({ principal: VIEWER });
  mount();
  await settle();
  asked.length = 0;
  assert(q('[data-testid="rotator-tiles"]') != null, "the readouts are HIDDEN from a viewer");
  assert(q('[aria-label="Target position angle, degrees"]') != null,
    "the MOVE TO input is HIDDEN from a viewer - nothing may be hidden");
  assert(/moving the rotator needs/.test(text()),
    "a viewer is given no reason for the dead motion controls");
  eq(button(/^Go$/).disabled, true, "GO is live for a viewer");
  eq(button(/^Halt$/).disabled, true, "HALT is live for a viewer - it is capability-gated even though it is urgent");
  click(button(/\+1°/));
  click(button(/Rotate to PA/));
  await settle();
  eq(commands().length, 0,
    `a viewer's presses reached the rig: ${JSON.stringify(commands().map((a) => `${a.method} ${a.url}`))}`);
});

// ============================================================== framing + empty
test("the framing hand-off is stated here, and warns when the offset is unknown", () => {
  seed({ principal: ADMIN }, { rotator: rotator({ synced: false }) });
  mount();
  assert(q('[data-testid="rotator-framing-row"]') != null,
    "no framing hand-off row - GAP-2 asks for the PA to reach the rotator from framing");
  const warn = q('[data-testid="rotator-unsynced-warning"]');
  assert(warn != null,
    "an unsynced rotator gets no warning - the angle framing sends would be off by the mechanical offset");
  assert(/not synced/.test(q('[data-testid="tile-sky-pa"]').textContent || ""),
    "the SKY PA tile does not say the angle is unsynced");
});

test("no rotator is an EmptyCard with a way forward, never a blank route", () => {
  seed({ principal: ADMIN }, { rotator: undefined, connected: {}, backend_links: [] });
  mount();
  assert(q('[data-testid="rotator-empty"]') != null,
    "a route with no rotator renders nothing at all - a dead end");
  assert(/ADD A DEVICE/.test(text()), "the empty state offers no way to connect one");
  assert(q('[data-testid="rotator-tiles"]') == null,
    "tiles are drawn from a device that is not there");
});

act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`rigRotator.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
