// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w16MountPositionUnknown.test.tsx - WP-126 / #144 (UI half): the MOUNT sheet
// while the mount does not know where it points, MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/rig/sheets/__tests__/w16MountPositionUnknown.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE STATE. After a power cycle the AM5 reports its home position, which is the
// pole, wherever the tube physically is, and the server publishes
// `status.mount.position_known = false` (WP-103). It is also the ORDINARY start
// of every night: a mount powered up parked at home reads the same pole, so the
// flag is false until the first plate-solve sync. Everything the sheet computes
// from "where the mount points" is then a precise answer about nothing.
//
// WHAT THE SHEET MUST DO, and what each case holds it to:
//   1. RA STEP / DEC STEP are locked WITH the reason (what a step is, why it
//      cannot run, what unlocks it) and SLEW RATE is not: a step is a goto from
//      the believed position, a hold is not.
//   2. A tap on the pad says the reason instead of posting a nudge. Hold-to-move
//      stays live, and its altitude guard is gone (it read the believed
//      position).
//   3. The ceiling stop is selected on the transition, once.
//   4. No altitude or azimuth is shown: at the pole the altitude IS the site
//      latitude, and the tile is a number about nothing besides.
//   5. A note over the pad says what the mount is doing in terms that are
//      ordinary rather than alarming, and TRUST POSITION says what the operator
//      is attesting, asks first, posts `/api/mount/trust-position`, and the
//      sheet unlocks when the next status frame says so.
//   6. A viewer, and a run that owns the mount, cannot attest.
//   7. The server's own 409 `position_unknown` stays the backstop: a status frame
//      that is a moment stale must not turn the refusal into a bare error.
//
// Named mutants (one-statement source changes in mount.tsx; each run from a
// byte backup inside the worktree, restored byte-identically, mutant text
// grepped out). The first failing assertion of each is quoted verbatim:
//   w16m1_reason_dropped -- `stepReason`'s position clause made `null`. "x RA
//     STEP and DEC STEP are locked with the reason, and SLEW RATE is not:
//     mount-ra-step is live while the mount does not know where it points
//     (expected true, got null)".
//   w16m2_rate_dial_locked_too -- SLEW RATE's `lockedReason` made `stepReason`.
//     "x RA STEP and DEC STEP are locked with the reason, and SLEW RATE is not:
//     SLEW RATE is locked too: driving the tube home by eye is the one thing
//     that must stay live (expected null, got true)".
//   w16m3_tap_not_refused -- `nudge()`'s position refusal made `if (false)`.
//     "x a tap on the pad says why instead of posting a nudge: a step was posted
//     from a position the mount cannot vouch for: [{"url":"/api/mount/nudge",
//     "body":{"axis":"ra","arcmin":10}}] (expected 0, got 1)".
//   w16m4_altaz_kept -- the ALT / AZ tile's unknown branch deleted. "x no
//     altitude or azimuth is on the screen, and the ALT / AZ tile says why: the
//     tile prints the believed position: ALT / AZ" + the believed angles.
//   w16m5_trust_without_confirm -- `await confirmDialog(...)` made `true ||
//     await confirmDialog(...)`. "x TRUST POSITION asks first, saying what is
//     being attested: one tap attested the tube's position with no
//     confirmation".
//   w16m6_trust_posts_nothing -- the `api.post(".../trust-position")` replaced
//     by a literal answer. "x a confirmed attestation posts, and the next frame
//     unlocks the sheet: the attestation never reached the route: []".
//   w16m7_trust_unlocked_for_a_run -- `trustReason`'s `flowExtra` made `null`.
//     "x a run that owns the mount owns the attestation too: TRUST POSITION is
//     live while a flow owns the mount (expected true, got null)".
//   w16m8_note_dropped -- the note's `!positionIsKnown &&` made `false &&`. "x
//     the note says what the mount is doing in ordinary terms, and what still
//     works: no note over the pad while the mount does not know where it
//     points".
//   w16m9_declined_reported_as_cleared -- the `res?.position_known === false`
//     branch made `if (false)`. "x a driver that declines is NOT reported as
//     cleared: a declined attestation was announced as a success: Position
//     trusted Steps unlock with the next status update. ...".
//   w16m10_trust_live_in_flight -- `trustReason`'s `sending ? SENDING_REASON :
//     null` line removed. "x an attestation already on the wire locks the
//     button, and a second press asks nothing: TRUST POSITION is live while its
//     own request is on the wire (expected true, got null)".
//   w16m11_trust_live_on_a_busy_lane -- `trustReason`'s `laneGoto.lockedReason`
//     removed. "x a motion on the goto lane takes TRUST POSITION out of service:
//     TRUST POSITION is live while the mount is moving on the goto lane
//     (expected true, got null)".
//   w16m12_attestation_line_dropped -- the one-line statement under the button
//     deleted. "x TRUST POSITION asks first, saying what is being attested:
//     nothing next to the button says what pressing it claims".
//   w16m13_altaz_sub_dropped -- the ALT / AZ tile's unknown `sub` made "". "x no
//     altitude or azimuth is on the screen, and the ALT / AZ tile says why: the
//     tile says unknown but not whose reading it would have been: ALT / AZunknown".
// (m10-m13 were added by the independent verifier: the first draft left those
// four statements unpinned.)

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

// ------------------------------------------------------------- fetch recorder
const asked: string[] = [];
const sent: { url: string; body: unknown }[] = [];
/** A refusal to plant for the next request whose URL contains this key. */
let refuse: { match: string; status: number; body: unknown } | null = null;
/** What `/api/mount/trust-position` answers. The driver's own verdict afterwards. */
let trustAnswer: unknown = { ok: true, position_known: true };
/** When set, `/api/mount/trust-position` does not answer until it resolves: the
 *  request stays ON THE WIRE, which is the window the in-flight lock is for. */
let trustGate: Promise<void> | null = null;
g.fetch = async (url: string, init?: { method?: string; body?: string }) => {
  asked.push(`${init?.method ?? "GET"} ${String(url)}`);
  if (trustGate && String(url).includes("/api/mount/trust-position")) await trustGate;
  let parsed: unknown = undefined;
  try { parsed = init?.body != null ? JSON.parse(init.body) : undefined; } catch { /* not json */ }
  sent.push({ url: String(url), body: parsed });
  if (refuse && String(url).includes(refuse.match)) {
    const r = refuse;
    refuse = null;
    return { ok: false, status: r.status, statusText: "REFUSED", json: async () => r.body };
  }
  if (String(url).includes("/api/catalog")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => [] };
  }
  if (String(url).includes("/api/mount/trust-position")) {
    return { ok: true, status: 200, statusText: "OK", json: async () => trustAnswer };
  }
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { MountSheet, FLOW_OWNS_MOUNT, SENDING_REASON } = await import("../mount");
const {
  POSITION_UNKNOWN_STEPS_REASON, POSITION_UNKNOWN_NOTE, TRUST_POSITION_LABEL,
  TRUST_POSITION_CONFIRM_BODY, TRUST_POSITION_CONFIRM_TITLE,
} = await import("../../../../../lib/slewController");

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
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture",
    "control.mount", "control.guide"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

// Made-up numbers throughout; the believed alt/az below are NOT anybody's site.
// 62 / 41 is what the sheet would print for a known position, so seeing either
// number on screen while the position is unknown is the defect.
const BELIEVED_ALT = 62;
const BELIEVED_AZ = 41;

function mountStatus(over: Record<string, unknown> = {}) {
  return {
    connected: { telescope: { connected: true, name: "ZWO AM5N" } },
    looping: false,
    busy_lanes: [],
    busy: null,
    backend_links: [{ role: "telescope", connected: true, error: null }],
    mount: {
      ra_hours: 20.5, dec_deg: 60.1, ra_str: "20h 30m", dec_str: "+60° 06'",
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

function seed(mountOver: Record<string, unknown> = {}, over: Record<string, unknown> = {}): void {
  act(() => {
    useStore.setState({
      status: mountStatus(mountOver),
      equipConnected: true,
      wsPhase: "up",
      authGate: "open",
      principal: OPERATOR,
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
      ...over,
    } as never);
  });
}
/** The 2 s status frame: the only thing that ever tells the sheet the truth. */
function frame(mountOver: Record<string, unknown> = {}): void {
  act(() => { useStore.setState({ status: mountStatus(mountOver) } as never); });
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(createElement(MountSheet as any, { params: {}, depth: 0 })); });
}
function click(node: any): void {
  assert(node != null, "click target missing");
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}
const q = (sel: string) => container.querySelector(sel) as any;
const text = () => (container.textContent || "") as string;
const lastToast = (): string => {
  const t = (useStore.getState().toasts ?? []) as { title?: string; detail?: string }[];
  const top = t[t.length - 1];
  return top ? `${top.title ?? ""} ${top.detail ?? ""}` : "";
};
const track = (id: string) => q(`[data-testid="${id}"] .nx-dial-track`);
const stepIds = ["mount-ra-step", "mount-dec-step"];
const confirmReq = () => (useStore.getState() as any).confirm;
const answerConfirm = (ok: boolean) => act(() => { useStore.getState().resolveConfirm(ok); });

function pointer(type: string, id: number): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  ev.pointerId = id;
  ev.pointerType = "touch";
  ev.button = 0;
  ev.isPrimary = true;
  return ev;
}
/** Press one pad key and ALWAYS release it (the pad's multi-touch guard would
 *  otherwise reject every later press and fail the next case for no reason). */
async function press(label: string, id: number, between: () => void = () => {}): Promise<void> {
  const node = q(`[aria-label="slew ${label}"]`);
  assert(node != null, `no ${label} key on the pad`);
  act(() => { node.dispatchEvent(pointer("pointerdown", id)); });
  try { between(); }
  finally { act(() => { node.dispatchEvent(pointer("pointerup", id)); }); }
  await settle();
}
const nudges = () => sent.filter((s) => s.url.includes("/api/mount/nudge"));
const moves = () => sent.filter((s) => s.url.includes("/api/mount/move"));
const selectedRate = (): string | null => {
  const sel = q('[data-testid="mount-slew-rate"] [data-selected="true"]');
  return sel ? (sel.textContent || "") : null;
};

// ============================================================ 1. precondition
seed();
mount();

test("the sheet mounted with a KNOWN position: steps live, no note, alt/az on the tile", () => {
  assert(q('[data-testid="rig-mount"]') != null, "no sheet marker - nothing rendered");
  assert(q('[aria-label="Stop all mount motion"]') != null, "SlewPad did not mount");
  for (const id of [...stepIds, "mount-slew-rate"]) {
    assert(track(id) != null, `${id} did not render its dial`);
    eq(track(id).getAttribute("aria-disabled"), null, `${id} is locked on a known position`);
  }
  eq(q('[data-testid="mount-position-note"]'), null,
    "the position note is up for a mount that knows where it points");
  assert(text().includes(`${BELIEVED_ALT}° / ${BELIEVED_AZ}°`),
    "the control is broken: a known position must still show alt / az");
});

// ================================================ 2. the transition to unknown
seed({ position_known: false });
mount();
await settle();

test("RA STEP and DEC STEP are locked with the reason, and SLEW RATE is not", () => {
  for (const id of stepIds) {
    const t = track(id);
    assert(t != null, `${id} did not render its dial`);
    eq(t.getAttribute("aria-disabled"), "true",
      `${id} is live while the mount does not know where it points`);
    eq(t.getAttribute("title"), POSITION_UNKNOWN_STEPS_REASON,
      `${id} is locked without saying what a step is and why it cannot run`);
  }
  eq(track("mount-slew-rate").getAttribute("aria-disabled"), null,
    "SLEW RATE is locked too: driving the tube home by eye is the one thing that must stay live");
});

test("the reason says what a step is, why it cannot run, and what unlocks it", () => {
  const r = POSITION_UNKNOWN_STEPS_REASON;
  assert(/measured from where the mount thinks it points/.test(r), `what a step is: ${r}`);
  assert(/does not know/.test(r), `why it cannot run: ${r}`);
  assert(/plate-solve sync/.test(r) && /TRUST POSITION/.test(r), `what unlocks it: ${r}`);
  assert(!/[—–]/.test(r), "an em or en dash in a new-UI string (ARCHITECTURE 5)");
});

test("the ceiling stop is selected on the transition", () => {
  eq(selectedRate(), "1.44 deg/s", "the SLEW RATE dial did not move to the mount's ceiling");
  const checked = q('[aria-label="Slew rate"] [aria-checked="true"]');
  assert(checked != null && /1\.44 deg\/s/.test(checked.textContent || ""),
    "the pad's centre cell did not follow to the ceiling rung");
});

test("no altitude or azimuth is on the screen, and the ALT / AZ tile says why", () => {
  const tile = q('[data-testid="tile-altaz"]');
  assert(tile != null, "the ALT / AZ tile is gone");
  const t = tile.textContent || "";
  assert(!t.includes(String(BELIEVED_ALT)) && !t.includes(String(BELIEVED_AZ)),
    `the tile prints the believed position: ${t}`);
  assert(/unknown/i.test(t), `the tile does not say the position is unknown: ${t}`);
  assert(/home reading, not the tube/.test(t),
    `the tile says unknown but not whose reading it would have been: ${t}`);
  assert(!text().includes(`${BELIEVED_ALT}°`), "a believed altitude is printed somewhere on the sheet");
});

test("the note says what the mount is doing in ordinary terms, and what still works", () => {
  const note = q('[data-testid="mount-position-note"]');
  assert(note != null, "no note over the pad while the mount does not know where it points");
  const t = note.textContent || "";
  assert(t.includes(POSITION_UNKNOWN_NOTE), `the note is not the shared copy: ${t}`);
  assert(/normal/.test(POSITION_UNKNOWN_NOTE) && /start of a night/.test(POSITION_UNKNOWN_NOTE),
    "the note does not say this is the ordinary start of a night");
  assert(/Holding a pad key still moves the tube/.test(POSITION_UNKNOWN_NOTE),
    "the note does not say what still works");
  assert(!/error|lost|failed|warning/i.test(POSITION_UNKNOWN_NOTE),
    "the note is alarming where the state is ordinary");
});

await testAsync("a tap on the pad says why instead of posting a nudge", async () => {
  // Back to the tap-only rung: at any faster rate a tap does nothing, which
  // would make this pass for the wrong reason.
  click(q('[data-testid="mount-slew-rate"] [data-value="0"]'));
  await settle();
  eq(selectedRate(), "GUIDE", "precondition: the GUIDE rung is selected");
  sent.length = 0;
  act(() => { useStore.setState({ toasts: [] } as never); });
  await press("east", 31);
  eq(nudges().length, 0,
    `a step was posted from a position the mount cannot vouch for: ${JSON.stringify(sent)}`);
  assert(lastToast().includes("measured from where the mount thinks it points"),
    `the refused tap said nothing: ${lastToast()}`);
});

await testAsync("hold-to-move stays live, at the ceiling, with the altitude guard gone", async () => {
  // The believed altitude is below the pad's limit: with the guard still reading
  // it, this hold would be stopped at the gate.
  seed({ position_known: false, alt: 3 });
  mount();
  await settle();
  eq(selectedRate(), "1.44 deg/s", "precondition: the ceiling stop is selected");
  sent.length = 0;
  await press("east", 32, () => {
    const live = moves().filter((m) => (m.body as any).rate_deg_s !== 0);
    eq(live.length, 1, "the pad would not drive a reset mount home by eye");
    eq((live[0].body as any).rate_deg_s, 1.44, "the hold was not at the ceiling rate");
  });
});

await testAsync("the ceiling stop is chosen once: the operator's own pick survives a frame", async () => {
  seed({ position_known: false });
  mount();
  await settle();
  click(q('[data-testid="mount-slew-rate"] [data-value="1"]'));
  await settle();
  frame({ position_known: false, alt: BELIEVED_ALT + 1 });
  frame({ position_known: false, alt: BELIEVED_ALT + 2 });
  eq(selectedRate(), "8× SID", "a status frame re-forced the ceiling stop over the operator's pick");
});

// ============================================================== 3. TRUST POSITION
await testAsync("TRUST POSITION asks first, saying what is being attested", async () => {
  seed({ position_known: false });
  mount();
  await settle();
  const btn = q('[data-testid="mount-trust-position"]');
  assert(btn != null, "no TRUST POSITION button while the mount does not know where it points");
  assert((btn.textContent || "").includes(TRUST_POSITION_LABEL), "the button does not say TRUST POSITION");
  assert(text().includes(`${TRUST_POSITION_LABEL} says the tube is physically at its home or park position.`),
    "nothing next to the button says what pressing it claims");
  asked.length = 0;
  click(btn);
  await settle();
  const req = confirmReq();
  assert(req != null, "one tap attested the tube's position with no confirmation");
  eq(req.title, TRUST_POSITION_CONFIRM_TITLE, "the confirmation title");
  eq(req.body, TRUST_POSITION_CONFIRM_BODY, "the confirmation body");
  assert(/physically at the mount's home or park position/.test(String(req.body)),
    "the confirmation does not say what the operator is attesting");
  assert(!asked.some((a) => a.includes("trust-position")),
    "the attestation was posted before the operator confirmed");
  answerConfirm(false);
  await settle();
  assert(!asked.some((a) => a.includes("trust-position")),
    `a cancelled confirmation still posted: ${JSON.stringify(asked)}`);
  eq(track("mount-ra-step").getAttribute("aria-disabled"), "true",
    "cancelling unlocked the steps");
});

await testAsync("a confirmed attestation posts, and the next frame unlocks the sheet", async () => {
  seed({ position_known: false });
  mount();
  await settle();
  asked.length = 0;
  click(q('[data-testid="mount-trust-position"]'));
  await settle();
  answerConfirm(true);
  await settle();
  assert(asked.includes("POST /api/mount/trust-position"),
    `the attestation never reached the route: ${JSON.stringify(asked)}`);

  // The rig answers with its next frame; nothing is unlocked on the client's own
  // say-so, which is how a driver that declines would be reported as cleared.
  eq(track("mount-ra-step").getAttribute("aria-disabled"), "true",
    "the sheet unlocked before the rig said the position is known");
  frame({ position_known: true });
  await settle();
  for (const id of stepIds) {
    eq(track(id).getAttribute("aria-disabled"), null, `${id} stayed locked after the rig cleared it`);
  }
  eq(q('[data-testid="mount-position-note"]'), null, "the note outlived the latch");
  assert(text().includes(`${BELIEVED_ALT}° / ${BELIEVED_AZ}°`),
    "the ALT / AZ tile did not come back with the position");
});

await testAsync("a driver that declines is NOT reported as cleared", async () => {
  seed({ position_known: false });
  mount();
  await settle();
  trustAnswer = { ok: true, position_known: false };
  act(() => { useStore.setState({ toasts: [] } as never); });
  click(q('[data-testid="mount-trust-position"]'));
  await settle();
  answerConfirm(true);
  await settle();
  trustAnswer = { ok: true, position_known: true };
  assert(/did not accept that its position is known/.test(lastToast()),
    `a declined attestation was announced as a success: ${lastToast()}`);
  assert(!/Position trusted/.test(lastToast()), "the success toast was shown for a refusal");
  eq(track("mount-ra-step").getAttribute("aria-disabled"), "true",
    "the steps unlocked on a driver that declined");
});

await testAsync("a route that refuses says so in the server's words", async () => {
  seed({ position_known: false });
  mount();
  await settle();
  refuse = {
    match: "/api/mount/trust-position", status: 409,
    body: { detail: "no telescope connected" },
  };
  act(() => { useStore.setState({ toasts: [] } as never); });
  click(q('[data-testid="mount-trust-position"]'));
  await settle();
  answerConfirm(true);
  await settle();
  assert(/no telescope connected/.test(lastToast()),
    `the route's refusal was swallowed: ${lastToast()}`);
  refuse = null;
});

await testAsync("a viewer cannot attest, and is told why", async () => {
  seed({ position_known: false }, { principal: VIEWER });
  mount();
  await settle();
  const btn = q('[data-testid="mount-trust-position"]');
  assert(btn != null, "TRUST POSITION is hidden from a viewer - nothing may be hidden");
  eq(btn.getAttribute("aria-disabled"), "true", "TRUST POSITION is live for a viewer");
  assert(/needs/.test(btn.getAttribute("title") || ""),
    `no reason for a viewer: ${btn.getAttribute("title")}`);
  asked.length = 0;
  click(btn);
  await settle();
  eq(confirmReq(), null, "a viewer's press opened the attestation");
  assert(!asked.some((a) => a.includes("trust-position")), "a viewer's press reached the rig");
});

await testAsync("a run that owns the mount owns the attestation too", async () => {
  seed({ position_known: false }, { sequence: { state: "running", target: "NGC 6946" } });
  mount();
  await settle();
  const btn = q('[data-testid="mount-trust-position"]');
  assert(btn != null, "no TRUST POSITION button during a run");
  eq(btn.getAttribute("aria-disabled"), "true",
    "TRUST POSITION is live while a flow owns the mount");
  eq(btn.getAttribute("title"), FLOW_OWNS_MOUNT, "the lock does not carry the flow's reason");
  // And the step dials say the FLOW's reason first: it is the larger lock.
  eq(track("mount-ra-step").getAttribute("title"), FLOW_OWNS_MOUNT,
    "the flow lock lost to the position lock in the dial's reason");
});

await testAsync("a motion on the goto lane takes TRUST POSITION out of service", async () => {
  // The claim is about a tube at rest. A park, a home walk or a goto holds the
  // `goto` lane, and the lane survives a reload and a second tablet.
  seed({ position_known: false });
  act(() => {
    useStore.setState({
      status: { ...mountStatus({ position_known: false }), busy_lanes: ["goto"] },
    } as never);
  });
  mount();
  await settle();
  const btn = q('[data-testid="mount-trust-position"]');
  assert(btn != null, "no TRUST POSITION button while the lane is busy");
  eq(btn.getAttribute("aria-disabled"), "true",
    "TRUST POSITION is live while the mount is moving on the goto lane");
  const why = btn.getAttribute("title") || "";
  assert(/slewing|moving|stop the mount/i.test(why), `the lock does not say the mount is moving: ${why}`);
  asked.length = 0;
  click(btn);
  await settle();
  eq(confirmReq(), null, "a press during a motion opened the attestation");
  assert(!asked.some((a) => a.includes("trust-position")), "a press during a motion reached the rig");
  // The control: the lane clearing puts the button back.
  frame({ position_known: false });
  await settle();
  eq(q('[data-testid="mount-trust-position"]').getAttribute("aria-disabled"), null,
    "TRUST POSITION stayed locked after the lane cleared");
});

await testAsync("an attestation already on the wire locks the button, and a second press asks nothing", async () => {
  // `ActionButton`'s `busy` never blocks a press, so without a `lockedReason`
  // for the request in flight a second tap would open a second confirmation.
  seed({ position_known: false });
  mount();
  await settle();
  let release: () => void = () => {};
  trustGate = new Promise<void>((res) => { release = res; });
  asked.length = 0;
  click(q('[data-testid="mount-trust-position"]'));
  await settle();
  answerConfirm(true);
  await settle();
  try {
    assert(asked.filter((a) => a.includes("trust-position")).length === 1,
      `the attestation did not go out exactly once: ${JSON.stringify(asked)}`);
    const btn = q('[data-testid="mount-trust-position"]');
    eq(btn.getAttribute("aria-disabled"), "true",
      "TRUST POSITION is live while its own request is on the wire");
    eq(btn.getAttribute("title"), SENDING_REASON, "the lock does not say a command is on the wire");
    click(btn);
    await settle();
    eq(confirmReq(), null, "a second press while the first is on the wire opened a second confirmation");
  } finally {
    trustGate = null;
    release();
    await settle();
  }
  eq(q('[data-testid="mount-trust-position"]').getAttribute("aria-disabled"), null,
    "TRUST POSITION stayed locked after the answer came back");
});

// ================================================================ 4. the backstop
await testAsync("the server's 409 position_unknown reaches the user verbatim when the frame is stale", async () => {
  // The status frame says known; the rig has just reset. The sheet cannot know
  // yet, so the server's refusal is the only thing between the tap and a goto
  // from the wrong place.
  seed();
  mount();
  await settle();
  const detail = "mount position is not known (just reset, or never synced) - a nudge "
    + "cannot compute where that lands; use the rate-move pad to drive by eye";
  refuse = {
    match: "/api/mount/nudge", status: 409,
    body: { detail: { detail, code: "position_unknown" } },
  };
  click(q('[data-testid="mount-slew-rate"] [data-value="0"]'));
  await settle();
  act(() => { useStore.setState({ toasts: [] } as never); });
  await press("east", 33);
  assert(lastToast().includes("use the rate-move pad to drive by eye"),
    `the server's refusal did not reach the user: ${lastToast()}`);
  refuse = null;
});

if (rootRef) act(() => { rootRef!.unmount(); });

const total = passed + failed;
console.log(`w16MountPositionUnknown.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
