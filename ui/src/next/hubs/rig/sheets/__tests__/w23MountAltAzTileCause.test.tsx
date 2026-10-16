// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w23MountAltAzTileCause.test.tsx - WP-220 / #1023: the MOUNT sheet's ALT / AZ
// tile tells the reasons it has nothing to print apart, and names the right one.
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/w23MountAltAzTileCause.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE DEFECT. `believedPointing` answers null for three different reasons and
// says so on purpose (it is a gate, not an explainer). The tile used to name
// two of them and fold the third into the wrong one: whenever the mount knew
// where it pointed and the selector still answered null, the tile read value
// "hidden" and sub-label "needs operator or admin access". That is true for a
// viewer, whose frame the server redacts (api/redact.py `_MOUNT_DERIVED_KEYS`),
// and false for an operator or admin who holds `view.site_derived` and whose
// frame simply carried no usable angle: the tile blamed access that the
// principal had.
//
// THE THREE CAUSES, and what each reads (the value; the sub-label):
//   - no access (the principal lacks view.site_derived): "hidden" /
//     "needs operator or admin access". Fail-closed: an unresolved principal
//     holds nothing, so it reads this too.
//   - position unknown (the mount reports its HOME reading, the pole, #144):
//     "unknown" / "the mount's home reading, not the tube". Wins over the other
//     two, as it always did: it is the cause that matters most to an operator.
//   - value unavailable (access, known position, no usable angle): "not
//     reported" / the same RA / Dec sub-label a printed pair carries. Neutral:
//     it blames nothing it cannot know, and the sub-label tells the operator
//     something they cannot read off the value.
//
// WHAT EACH CASE HOLDS THE SHEET TO:
//   1. control: a known position prints the angles and the RA / Dec sub-label.
//   2. no access: a viewer reads hidden / needs access, and a principal that
//      has not resolved reads the same.
//   3. the capability decides, not the shape of the value: a viewer handed a
//      non-finite angle still reads hidden / needs access.
//   4. value unavailable: an operator and an admin handed no angle, or a
//      non-finite one (NaN, both infinities, null, on either angle), read "not
//      reported", and the sub-label does not say they need access they hold.
//   5. the sub-label of that arm falls back to "RA / Dec not reported" when the
//      equatorial reading is unusable too.
//   6. position unknown wins for every principal, and prints no number.
//   7. the tile follows the principal: a viewer who becomes an operator in the
//      store stops being told they need access.
//   8. the three causes read three different values (a guard against two arms
//      collapsing to one string).
//
// Named mutants (each is an edit to mount.tsx, run from a byte backup and
// restored byte-identically). The first failing assertion of each is quoted:
//   w23t1_unavailable_says_hidden -- `!canSeeDerived ?` made `true ?`: every
//     null reads hidden / needs access again. The defect itself. 8/31: "x value
//     unavailable: operator with alt and az absent reads "not reported" and is
//     not told they need access: the tile value: ALT / AZhiddenneeds operator
//     or admin access".
//   w23t2_viewer_says_not_reported -- `!canSeeDerived ?` made `false ?`: a viewer
//     is told the mount reported nothing instead of that they lack access.
//     26/31: "x no access: a viewer (alt / az redacted) reads hidden / needs
//     operator or admin access: the tile: ALT / AZnot reportedRA 07h31m · Dec
//     -35d12m".
//   w23t3_unknown_arm_deleted -- the `!positionIsKnown` arm deleted from
//     `altAzTile`: an unknown position reads like an unreadable one. 26/31: "x
//     position unknown: an operator reads unknown / the home reading, and no
//     number: the tile value: ALT / AZnot reportedRA / Dec not reported".

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/rig/devices/mount", pretendToBeVisual: true },
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
win.Element.prototype.hasPointerCapture = function () { return true; };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "PointerEvent", "Image", "localStorage", "getComputedStyle", "matchMedia",
  "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

g.fetch = async (url: string) => {
  if (String(url).includes("/api/catalog")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => [] };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { ROLE_CAPS } = await import("../../../../../lib/caps");
const { MountSheet } = await import("../mount");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ fixtures
// The principals carry the caps the role table gives them, read from the table
// itself (`ROLE_CAPS`) so a fixture cannot drift from the policy it is testing.
const OPERATOR = { role: "operator", email: "op@rig", caps: [...ROLE_CAPS.operator] };
const ADMIN = { role: "admin", email: "admin@rig", caps: [...ROLE_CAPS.admin] };
const VIEWER = { role: "viewer", email: "viewer@rig", caps: [...ROLE_CAPS.viewer] };

// Made-up, distinctive numbers, so a leak is a plain substring test.
const BELIEVED_ALT = 62;
const BELIEVED_AZ = 41;
const RADEC_SUB = "RA 07h31m · Dec -35d12m";

function mountStatus(over: Record<string, unknown> = {}) {
  return {
    connected: { telescope: { connected: true, name: "ZWO AM5N" } },
    looping: false,
    busy_lanes: [],
    busy: null,
    backend_links: [{ role: "telescope", connected: true, error: null }],
    mount: {
      ra_hours: 7.52, dec_deg: -35.2, ra_str: "07h31m", dec_str: "-35d12m",
      alt: BELIEVED_ALT, az: BELIEVED_AZ, tracking: true, parked: false, slewing: false,
      tracking_rate: "sidereal", can_set_tracking_rate: true, can_find_home: true,
      max_rate_deg_s: 1.44,
      pointing: { verified: false, reason: "solve failed: not enough stars", error_arcmin: 2.4 },
      ...over,
    },
    meridian: {
      status: "counting", hours_to_flip: 1.63, flip_enabled: true, pier_side: "east",
    },
  };
}

/** `drop` names mount keys to REMOVE from the block (an absent key, which is
 *  not the same as one set to undefined on the wire). */
function seed(
  over: Record<string, unknown> = {}, drop: string[] = [], principal: unknown = OPERATOR,
): void {
  const status: any = mountStatus(over);
  for (const k of drop) delete status.mount[k];
  act(() => {
    useStore.setState({
      status,
      equipConnected: true,
      wsPhase: "up",
      authGate: "open",
      principal,
      sequence: { state: "idle" },
      polar: {
        state: "idle", az_error: 0, alt_error: 0, total_error: 0,
        progress: 0, message: "", source: null,
      },
      logs: [],
      config: { safety: { solar_avoidance: true, solar_exclusion_deg: 30 } },
      mountOp: null,
      toasts: [],
      confirm: null,
      locked: false,
    } as never);
  });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(MountSheet as any, { params: {}, depth: 0 })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;
const tile = (): any => {
  const t = q('[data-testid="tile-altaz"]');
  assert(t != null, "the ALT / AZ tile is gone - the sheet did not render");
  return t;
};
const tileText = (): string => tile().textContent || "";
const tileValue = (): string => tile().querySelector(".nx-readout-value")?.textContent || "";
const tileSub = (): string => tile().querySelector(".nx-readout-sub")?.textContent || "";
const tileTone = (): string | null => tile().getAttribute("data-tone");

/** Seed, mount, and hand back what the tile reads. */
function read(
  over: Record<string, unknown> = {}, drop: string[] = [], principal: unknown = OPERATOR,
): { value: string; sub: string; tone: string | null } {
  seed(over, drop, principal);
  mount();
  return { value: tileValue(), sub: tileSub(), tone: tileTone() };
}

const NO_ACCESS_SUB = "needs operator or admin access";

// ============================================================== 1. control
test("control: a KNOWN position prints the angles and the RA / Dec sub-label", () => {
  const r = read();
  assert(r.value === `${BELIEVED_ALT}° / ${BELIEVED_AZ}°`, `the tile: ${tileText()}`);
  assert(r.sub === RADEC_SUB, `the sub-label: ${tileText()}`);
});

// ============================================================= 2. no access
test("no access: a viewer (alt / az redacted) reads hidden / needs operator or admin access", () => {
  const r = read({}, ["alt", "az"], VIEWER);
  assert(r.value === "hidden", `the tile: ${tileText()}`);
  assert(r.sub === NO_ACCESS_SUB, `the sub-label: ${tileText()}`);
});

test("no access, fail-closed: an unresolved principal reads hidden / needs access too", () => {
  const r = read({}, ["alt", "az"], null);
  assert(r.value === "hidden", `the tile: ${tileText()}`);
  assert(r.sub === NO_ACCESS_SUB, `the sub-label: ${tileText()}`);
});

// =========================================== 3. the capability decides, not the value
test("the capability decides: a viewer handed a non-finite angle still reads hidden / needs access", () => {
  const r = read({ alt: Number.NaN }, [], VIEWER);
  assert(r.value === "hidden", `the tile: ${tileText()}`);
  assert(r.sub === NO_ACCESS_SUB, `the sub-label: ${tileText()}`);
});

// ================================================== 4. value unavailable
const UNAVAILABLE: { name: string; over: Record<string, unknown>; drop: string[] }[] = [
  { name: "alt and az absent", over: {}, drop: ["alt", "az"] },
  { name: "alt absent", over: {}, drop: ["alt"] },
  { name: "az absent", over: {}, drop: ["az"] },
  { name: "alt NaN", over: { alt: Number.NaN }, drop: [] },
  { name: "alt -Infinity", over: { alt: Number.NEGATIVE_INFINITY }, drop: [] },
  { name: "alt Infinity", over: { alt: Number.POSITIVE_INFINITY }, drop: [] },
  { name: "az Infinity", over: { az: Number.POSITIVE_INFINITY }, drop: [] },
  { name: "az NaN", over: { az: Number.NaN }, drop: [] },
  { name: "alt null", over: { alt: null }, drop: [] },
  { name: "az null", over: { az: null }, drop: [] },
];

for (const who of [["operator", OPERATOR], ["admin", ADMIN]] as const) {
  for (const u of UNAVAILABLE) {
    test(`value unavailable: ${who[0]} with ${u.name} reads "not reported" and is not told they need access`, () => {
      const r = read(u.over, u.drop, who[1]);
      assert(r.value === "not reported", `the tile value: ${tileText()}`);
      assert(!/hidden|needs|access|operator or admin/.test(tileText()),
        `the tile blames access a ${who[0]} holds: ${tileText()}`);
      assert(r.sub === RADEC_SUB, `the sub-label carries the RA / Dec instead: ${tileText()}`);
      assert(r.tone == null, `a reading nobody could take is toned ${r.tone}`);
      assert(!/NaN|Infinity|undefined|null/.test(tileText()),
        `the tile prints the reading: ${tileText()}`);
    });
  }
}

// ======================================= 5. unavailable, and RA / Dec is not usable either
test("value unavailable and RA / Dec unreadable too: the sub-label says so", () => {
  const r = read({ alt: Number.NaN, ra_hours: Number.NaN }, ["ra_str", "dec_str"]);
  assert(r.value === "not reported", `the tile value: ${tileText()}`);
  assert(r.sub === "RA / Dec not reported", `the sub-label: ${tileText()}`);
});

// ================================================== 6. position unknown wins
for (const who of [["operator", OPERATOR], ["admin", ADMIN], ["viewer", VIEWER]] as const) {
  test(`position unknown: ${who[0] === "viewer" ? "a" : "an"} ${who[0]} reads unknown / the home reading, and no number`, () => {
    const absent = who[0] === "viewer" ? ["alt", "az"] : [];
    const r = read({ position_known: false }, absent, who[1]);
    assert(r.value === "unknown", `the tile value: ${tileText()}`);
    assert(/home reading, not the tube/.test(r.sub), `the sub-label: ${tileText()}`);
    assert(r.tone === "warn", `the tile is toned ${r.tone}, not warn`);
    assert(!text().includes(`${BELIEVED_ALT}°`) && !text().includes(`${BELIEVED_AZ}°`),
      `the home reading is on the sheet: ${text().slice(0, 300)}`);
  });
}

test("position unknown wins over an unreadable angle too", () => {
  const r = read({ position_known: false, alt: Number.NaN });
  assert(r.value === "unknown", `the tile value: ${tileText()}`);
});

// ================================================= 7. the tile follows the principal
test("the tile follows the principal: a viewer who becomes an operator is no longer told to get access", () => {
  seed({}, ["alt", "az"], VIEWER);
  mount();
  assert(tileValue() === "hidden", `precondition: ${tileText()}`);
  act(() => { useStore.setState({ principal: OPERATOR } as never); });
  assert(tileValue() === "not reported", `the tile value after the principal changed: ${tileText()}`);
  assert(!/access/.test(tileText()), `the tile still names access: ${tileText()}`);
});

// =========================================== 8. three causes, three readings
test("the three causes read three different values", () => {
  const noAccess = read({}, ["alt", "az"], VIEWER).value;
  const unknown = read({ position_known: false }).value;
  const unavailable = read({}, ["alt", "az"], OPERATOR).value;
  const set = new Set([noAccess, unknown, unavailable]);
  assert(set.size === 3, `two causes read the same: ${[noAccess, unknown, unavailable].join(" | ")}`);
});

if (rootRef) act(() => { rootRef!.unmount(); });

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`w23MountAltAzTileCause: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
