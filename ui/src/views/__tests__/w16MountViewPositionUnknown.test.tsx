// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16MountViewPositionUnknown.test.tsx - WP-126 / #144 (UI half): the CLASSIC
// Mount view while the mount does not know where it points.
//
//   Run directly:  npx tsx src/views/__tests__/w16MountViewPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The classic view has no RA STEP / DEC STEP tiles: its pad is hold-to-move and
// a pulse tap, neither of which computes a destination, so what #144 asks of it
// is narrower than of the new sheet and is graded here:
//
//   1. the note over the pad says what the mount is doing in ordinary terms,
//      and TRUST POSITION says what the operator attests, asks first, posts
//      `/api/mount/trust-position`, and the view lets go when the next status
//      frame says the position is known (never on the press);
//   2. the Altitude and Azimuth stats stop printing the mount's home reading
//      (at the pole the altitude is the site latitude), and say unknown;
//   3. the pad it hosts selects the ceiling rung, on the transition, once.
//
// The pad's own half (altitude guard dropped, the NINA tap refused) is graded
// in components/__tests__/w16SlewPadPositionUnknown.test.tsx.
//
// #850 copy mutants (same procedure). Each puts back the round-3 wording:
//   h1m3_body_goto_back -- TRUST_POSITION_CONFIRM_BODY's last sentence (in
//     lib/slewController.ts) back to "If the tube is anywhere else, cancel, go
//     to a target away from the pole, and solve and sync there instead: the
//     mount refuses every sync at home." 12/13: "x TRUST POSITION asks first, and a
//     cancel posts nothing: the confirmation does not send a tube that is not
//     at home home by eye: You are saying the tube ...".
//   h1m5_view_toast_back -- MountView.tsx's declined toast back to "... Run
//     Solve & Sync instead." 12/13: "x a driver that declines is NOT reported
//     as cleared: the declined toast is not the agreed advice: The mount did
//     not accept that its position is known. Run Solve & Sync instead.".
//
// Named mutants (one-statement source changes in MountView.tsx; each run from a
// byte backup inside the worktree, restored byte-identically, mutant text
// grepped out). The first failing assertion of each is quoted verbatim:
//   w16v1_note_dropped -- the note's `!positionIsKnown &&` made `false &&`. "x
//     the note says what the mount is doing, in ordinary terms: no note over the
//     pad while the mount does not know where it points".
//   w16v2_stats_still_print -- the Altitude stat's unknown branch dropped. "x the
//     Altitude and Azimuth stats say unknown and print no angle: the Altitude
//     stat (expected unknown, got" + the believed altitude + ")".
//   w16v3_trust_without_confirm -- `await confirmDialog(...)` made `true ||
//     await confirmDialog(...)`. "x TRUST POSITION asks first, and a cancel
//     posts nothing: one tap attested the tube's position with no
//     confirmation".
//   w16v4_trust_posts_nothing -- the `api.post(".../trust-position")` replaced by
//     a literal answer. "x a confirmed attestation posts, and the view lets go
//     on the next frame: the attestation never reached the route: []".
//   w16v5_viewer_can_attest -- `!canMount ||` dropped from `trustBlocked`. "x a
//     viewer cannot attest, and the button says why: TRUST POSITION is live for
//     a viewer (expected true, got false)".
//   w16v6_declined_reported_as_cleared -- the `res?.position_known === false`
//     branch made `if (false)`. "x a driver that declines is NOT reported as
//     cleared: a declined attestation was announced as a success: Position
//     trusted".
//   w16v7_viewer_reads_undefined -- the Altitude stat's `typeof m.alt ===
//     "number"` test dropped (the incidental defect fixed beside #144). "x a
//     viewer's Altitude and Azimuth say hidden, not undefined (the server
//     removes them): a viewer's Altitude stat (expected hidden, got undefined)"
//     with the degree sign after "undefined".
//   w16v8_trust_live_while_slewing -- `|| !!m.slewing` dropped from
//     `trustBlocked`. "x TRUST POSITION is out of service while the mount is
//     slewing: TRUST POSITION is live while the mount is slewing (expected true,
//     got false)".
//   w16v9_trust_live_in_flight -- `pending !== null ||` dropped from
//     `trustBlocked`. "x an attestation already on the wire locks the button, and
//     a second press asks nothing: TRUST POSITION is live while its own request
//     is on the wire (expected true, got false)".
//   w16v10_trust_live_on_a_busy_lane -- `laneBusy ||` dropped from
//     `trustBlocked`. "x TRUST POSITION is out of service while the goto lane is
//     busy: TRUST POSITION is live while a park, home or goto holds the lane
//     (expected true, got false)".
// (v8-v10 were added by the independent verifier: the first draft pinned the
// viewer and flow locks only, and these three clauses of `trustBlocked` survived.)

/* eslint-disable @typescript-eslint/no-explicit-any */

const { JSDOM } = await import("jsdom");
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
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
win.Element.prototype.hasPointerCapture = function () { return true; };

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

const posts: { path: string; body: unknown }[] = [];
/** What `/api/mount/trust-position` answers: the driver's own verdict afterwards. */
let trustAnswer: unknown = { ok: true, position_known: true };
/** When set, `/api/mount/trust-position` does not answer until it resolves: the
 *  request stays ON THE WIRE, which is the window the in-flight lock is for. */
let trustGate: Promise<void> | null = null;
g.fetch = async (url: string, init?: any) => {
  const path = String(url);
  if ((init?.method ?? "GET") === "POST") {
    posts.push({ path, body: init?.body ? JSON.parse(init.body) : undefined });
  }
  if (trustGate && path.includes("/api/mount/trust-position")) await trustGate;
  const json = path.includes("/api/catalog") ? []
    : path.includes("/api/mount/trust-position") ? trustAnswer
      : { started: "ok" };
  return { ok: true, status: 200, statusText: "OK", json: async () => json } as any;
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const MountView = (await import("../MountView")).default;
const {
  POSITION_UNKNOWN_NOTE, TRUST_POSITION_LABEL, TRUST_POSITION_CONFIRM_BODY,
  TRUST_POSITION_CONFIRM_TITLE,
} = await import("../../lib/slewController");

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
function eq<T>(a: T, b: T, msg: string): void {
  if (a !== b) throw new Error(`${msg} (expected ${String(b)}, got ${String(a)})`);
}

// Made-up numbers; 45 / 120 is what a KNOWN position prints for these two stats,
// so seeing either while the position is unknown is the defect.
const BELIEVED_ALT = 45;
const BELIEVED_AZ = 120;

const OPERATOR = { role: "operator", email: null, caps: ["view.status", "control.mount"] };
const VIEWER = { role: "viewer", email: null, caps: ["view.status"] };

function seed(mount: Record<string, unknown> = {}, over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: {
        connected: {}, looping: false, mode: "sim", busy_lanes: [],
        mount: {
          ra_hours: 5.6, dec_deg: -5.4, ra_str: "05h35m", dec_str: "-05°23'",
          alt: BELIEVED_ALT, az: BELIEVED_AZ, tracking: true, parked: false,
          slewing: false, can_find_home: true, can_set_tracking_rate: true,
          tracking_rate: "sidereal", max_rate_deg_s: 1.44,
          ...mount,
        },
      },
      principal: OPERATOR,
      authGate: "open",
      toasts: [],
      confirm: null,
      ...over,
    } as never);
  });
}

seed();
const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (root) act(() => { root!.unmount(); });
  root = createRoot(container);
  act(() => { root!.render(createElement(MountView)); });
}
mount();

const settle = async (ms = 0) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};
const text = () => (container.textContent || "") as string;
const buttons = (): any[] => [...container.querySelectorAll("button")];
const trustBtn = (): any => buttons().find((b: any) => (b.textContent || "").trim() === TRUST_POSITION_LABEL);
const click = (node: any) => act(() => {
  node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});
const radios = (): any[] =>
  [...container.querySelectorAll('[role="radiogroup"][aria-label="Slew rate"] [role="radio"]')];
const checkedIdx = () => radios().findIndex((r: any) => r.getAttribute("aria-checked") === "true");
const confirmReq = () => (useStore.getState() as any).confirm;
const answerConfirm = (ok: boolean) => act(() => { useStore.getState().resolveConfirm(ok); });
/** The Pointing panel's stat by its label: the value is the next sibling text. */
const statValue = (label: string): string => {
  const labels = [...container.querySelectorAll("*")].filter(
    (n: any) => n.children.length === 0 && (n.textContent || "").trim() === label);
  const el = labels[0];
  return el ? ((el.parentElement?.textContent || "").replace(label, "").trim()) : "";
};

await settle();

// ============================================================ 1. precondition
test("the view mounted with a KNOWN position: no note, the stats print, GUIDE selected", () => {
  assert(buttons().some((b: any) => /^Park$/.test((b.textContent || "").trim())),
    "no Park button - this is not the mount view");
  assert(radios().length === 4, `the pad is not on the 1.44 deg/s ladder (${radios().length} rungs)`);
  eq(trustBtn(), undefined, "TRUST POSITION is on screen for a mount that knows where it points");
  assert(!text().includes(POSITION_UNKNOWN_NOTE), "the note is up for a known position");
  assert(statValue("Altitude").includes(`${BELIEVED_ALT}°`),
    `the control is broken: a known position must print the altitude (${statValue("Altitude")})`);
  eq(checkedIdx(), 0, "a known position must leave the tap-only GUIDE rung selected");
});

// ================================================== 2. the mount stops knowing
seed({ position_known: false });
await settle();

test("the note says what the mount is doing, in ordinary terms", () => {
  assert(text().includes(POSITION_UNKNOWN_NOTE),
    "no note over the pad while the mount does not know where it points");
  assert(/normal/.test(POSITION_UNKNOWN_NOTE) && /start of a night/.test(POSITION_UNKNOWN_NOTE),
    "the note does not say this is the ordinary start of a night");
  assert(!/error|lost|failed|warning/i.test(POSITION_UNKNOWN_NOTE),
    "the note is alarming where the state is ordinary");
});

test("the Altitude and Azimuth stats say unknown and print no angle", () => {
  eq(statValue("Altitude"), "unknown", "the Altitude stat");
  eq(statValue("Azimuth"), "unknown", "the Azimuth stat");
  assert(!text().includes(`${BELIEVED_ALT}°`) && !text().includes(`${BELIEVED_AZ}°`),
    "the mount's home reading is printed on the view");
});

test("the pad it hosts moved to the ceiling rung on the transition", () => {
  eq(checkedIdx(), 3, "the pad did not select the ceiling rung");
  assert(/1\.44 deg\/s/.test(radios()[checkedIdx()].textContent || ""),
    "the selected rung is not the mount's ceiling");
});

test("TRUST POSITION says on screen what it attests", () => {
  assert(trustBtn() != null, "no TRUST POSITION button");
  assert(text().includes(`${TRUST_POSITION_LABEL} says the tube is physically at its home or park position.`),
    "nothing next to the button says what pressing it claims");
});

// ============================================================ 3. TRUST POSITION
await testAsync("TRUST POSITION asks first, and a cancel posts nothing", async () => {
  posts.length = 0;
  click(trustBtn());
  await settle();
  const req = confirmReq();
  assert(req != null, "one tap attested the tube's position with no confirmation");
  eq(req.title, TRUST_POSITION_CONFIRM_TITLE, "the confirmation title");
  eq(req.body, TRUST_POSITION_CONFIRM_BODY, "the confirmation body");
  // #850, the safety rule: a tube that is NOT at home is brought home by eye
  // with a pad key first. A goto now would be aimed from the wrong position, so
  // the dialog never advises one.
  assert(/anywhere else, cancel and hold a pad key to bring it home by eye first/.test(String(req.body))
    && /aimed from the wrong position/.test(String(req.body))
    && !/plate/i.test(String(req.body)),
    `the confirmation does not send a tube that is not at home home by eye: ${String(req.body)}`);
  for (const re of [/go to a target/i, /target away from the pole/i, /goto/i, /go-to/i, /slew/i]) {
    assert(!re.test(String(req.body)),
      `the confirmation advises a goto (${re}) from an unknown position: ${String(req.body)}`);
  }
  assert(!posts.some((p) => p.path.includes("trust-position")),
    "the attestation was posted before the operator confirmed");
  answerConfirm(false);
  await settle();
  assert(!posts.some((p) => p.path.includes("trust-position")),
    `a cancelled confirmation still posted: ${JSON.stringify(posts)}`);
  assert(text().includes(POSITION_UNKNOWN_NOTE), "cancelling cleared the note");
});

await testAsync("a confirmed attestation posts, and the view lets go on the next frame", async () => {
  posts.length = 0;
  click(trustBtn());
  await settle();
  answerConfirm(true);
  await settle();
  assert(posts.some((p) => p.path === "/api/mount/trust-position"),
    `the attestation never reached the route: ${JSON.stringify(posts)}`);
  // Nothing clears on the client's own say-so.
  assert(text().includes(POSITION_UNKNOWN_NOTE),
    "the view let go before the rig said the position is known");
  seed({ position_known: true });
  await settle();
  eq(trustBtn(), undefined, "TRUST POSITION outlived the latch");
  assert(!text().includes(POSITION_UNKNOWN_NOTE), "the note outlived the latch");
  assert(statValue("Altitude").includes(`${BELIEVED_ALT}°`),
    "the Altitude stat did not come back with the position");
});

await testAsync("a driver that declines is NOT reported as cleared", async () => {
  seed({ position_known: false });
  mount();
  await settle();
  trustAnswer = { ok: true, position_known: false };
  act(() => { useStore.setState({ toasts: [] } as never); });
  click(trustBtn());
  await settle();
  answerConfirm(true);
  await settle();
  trustAnswer = { ok: true, position_known: true };
  const toasts = ((useStore.getState() as any).toasts as { title?: string }[])
    .map((t) => t.title ?? "").join(" | ");
  assert(/did not accept that its position is known/.test(toasts),
    `a declined attestation was announced as a success: ${toasts}`);
  assert(!/Position trusted/.test(toasts), "the success toast was shown for a refusal");
  // #850: a sync where the tube points now needs no goto, and the sentence
  // names no button (the classic view's says Solve & Sync, the sheet's SOLVE +
  // SYNC).
  assert(toasts.includes(
    "The mount did not accept that its position is known. Solve and sync where "
    + "the tube points now instead."),
    `the declined toast is not the agreed advice: ${toasts}`);
  for (const re of [/go to a target/i, /goto/i, /slew/i, /Solve & Sync/, /SOLVE \+ SYNC/, /\bRun\b/]) {
    assert(!re.test(toasts), `the declined toast says ${re}: ${toasts}`);
  }
  assert(text().includes(POSITION_UNKNOWN_NOTE), "the view let go on a driver that declined");
});

await testAsync("a viewer's Altitude and Azimuth say hidden, not undefined (the server removes them)", async () => {
  // An INCIDENTAL defect in the lines this package rewrote: the stats printed
  // `${m.alt}°`, so a viewer (no view.site_derived, whose frame has alt and az
  // ABSENT, not null) read "undefined°" in a Pointing panel that is otherwise
  // theirs to see. The fixture deletes the keys, as the wire does.
  seed({ position_known: true }, { principal: VIEWER });
  act(() => {
    const s = useStore.getState().status as any;
    const mountBlock = { ...s.mount };
    delete mountBlock.alt;
    delete mountBlock.az;
    useStore.setState({ status: { ...s, mount: mountBlock } } as never);
  });
  mount();
  await settle();
  eq(statValue("Altitude"), "hidden", "a viewer's Altitude stat");
  eq(statValue("Azimuth"), "hidden", "a viewer's Azimuth stat");
  assert(!/undefined/.test(text()), `the view prints undefined: ${text()}`);
});

await testAsync("a viewer cannot attest, and the button says why", async () => {
  seed({ position_known: false }, { principal: VIEWER });
  mount();
  await settle();
  const btn = trustBtn();
  assert(btn != null, "TRUST POSITION is hidden from a viewer");
  eq(btn.disabled, true, "TRUST POSITION is live for a viewer");
  posts.length = 0;
  click(btn);
  await settle();
  eq(confirmReq(), null, "a viewer's press opened the attestation");
  assert(!posts.some((p) => p.path.includes("trust-position")), "a viewer's press reached the rig");
});

// The claim is about a tube at rest, so TRUST POSITION is out of service while
// the mount moves (its own flag, or the goto lane a park/home/goto holds, which
// survives a reload) and while another command is on the wire. Each case below
// presses the button and asks that nothing was asked or posted.
async function assertOutOfService(why: string): Promise<void> {
  const btn = trustBtn();
  assert(btn != null, `no TRUST POSITION button (${why})`);
  eq(btn.disabled, true, `TRUST POSITION is live ${why}`);
  posts.length = 0;
  click(btn);
  await settle();
  eq(confirmReq(), null, `a press opened the attestation ${why}`);
  assert(!posts.some((p) => p.path.includes("trust-position")), `a press reached the rig ${why}`);
}

await testAsync("TRUST POSITION is out of service while the mount is slewing", async () => {
  seed({ position_known: false, slewing: true });
  mount();
  await settle();
  await assertOutOfService("while the mount is slewing");
  // The control: the same view at rest is live.
  seed({ position_known: false, slewing: false });
  await settle();
  eq(trustBtn().disabled, false, "TRUST POSITION stayed locked after the mount stopped");
});

await testAsync("TRUST POSITION is out of service while the goto lane is busy", async () => {
  seed({ position_known: false });
  act(() => {
    const s = useStore.getState().status as any;
    useStore.setState({ status: { ...s, busy_lanes: ["goto"] } } as never);
  });
  mount();
  await settle();
  await assertOutOfService("while a park, home or goto holds the lane");
  seed({ position_known: false });
  await settle();
  eq(trustBtn().disabled, false, "TRUST POSITION stayed locked after the lane cleared");
});

await testAsync("an attestation already on the wire locks the button, and a second press asks nothing", async () => {
  seed({ position_known: false });
  mount();
  await settle();
  let release: () => void = () => {};
  trustGate = new Promise<void>((res) => { release = res; });
  posts.length = 0;
  click(trustBtn());
  await settle();
  answerConfirm(true);
  await settle();
  try {
    assert(posts.filter((p) => p.path.includes("trust-position")).length === 1,
      `the attestation did not go out exactly once: ${JSON.stringify(posts)}`);
    eq(trustBtn().disabled, true, "TRUST POSITION is live while its own request is on the wire");
    click(trustBtn());
    await settle();
    eq(confirmReq(), null, "a second press while the first is on the wire opened a second confirmation");
  } finally {
    trustGate = null;
    release();
    await settle();
  }
  eq(trustBtn().disabled, false, "TRUST POSITION stayed locked after the answer came back");
});

if (root) act(() => { root!.unmount(); });

const total = passed + failed;
console.log(`w16MountViewPositionUnknown.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
