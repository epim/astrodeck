// focusDialSplit.test.tsx — the Focus screen has ONE control per camera
// setting, and the two radial controls over its preview do not overlap.
//
//   Run directly:  npx tsx src/views/__tests__/focusDialSplit.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WENT WRONG (#180 / #179). The stage carried two fan-out controls: the
// FocusPod bottom-right, and a CameraDial bottom-left. The pod's cycling badge
// showed the exposure the SHUTTER would use; the dial's ring showed the
// exposure the SWEEP would use — two different numbers, both spelled "Ns",
// 200px apart over one image, with nothing on either saying which question it
// answered. Behind them the sweep kept a private trio of `useState`s
// (afExposure/afGain/afBin) that duplicated the `focus` scope the Camera panel
// already held, and the same three settings had a picker in the Autofocus
// panel AND a typed box in its disclosure AND a ring on the dial: three
// controls per setting, two of which nothing else on the rig could see.
//
// So this file grades the shape of the fix rather than any one control:
//   1. ONE control per setting on the whole screen, every panel open.
//   2. The one that is over the frame writes the SHARED scope — a pick has to
//      leave the screen it was made on.
//   3. The sweep still receives those settings; removing its private copies
//      must not have orphaned it.
//   4. A refusal survived the move: an exposure the rig will not take must be
//      refused on the surface it moved to, and must not change the setting.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
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

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLSelectElement", "Element", "Node", "Event", "KeyboardEvent",
  "PointerEvent", "CustomEvent", "MouseEvent", "localStorage",
  "getComputedStyle", "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
g.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
// The dial's bloom is a two-frame affair and its cleanup CANCELS the frame it
// asked for, so both halves have to exist or unmounting an open dial throws.
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.cancelAnimationFrame = (id: unknown) => clearTimeout(id as never);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
// Dynamic, for the reason cameraSettingsAgreement.test.tsx states: a static
// import is evaluated before the bootstrap above, so react-dom would decide
// there is no DOM and fall back to a path where typing into a box does nothing.
const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");

let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg: string) { if (!cond) throw new Error(msg); },
  equal(a: unknown, b: unknown, msg: string) {
    if (a !== b) throw new Error(`${msg} — expected ${String(b)}, got ${String(a)}`);
  },
};

const { api } = await import("../../api");
const { useStore } = await import("../../store");
const { EMPTY_FRAME_SETTINGS } = await import("../../lib/authGate");
const FocusView = (await import("../FocusView")).default;
const { FOCUS_DIAL_INSET } = await import("../FocusView");
const { dialRadius, DIAL_MIN_R } = await import("../../lib/ringDial");
const { POD_MIN_STAGE_H } = await import("../../components/focus/FocusPod");

// THE RIG. A 5-slot wheel parked on Oiii with a blackout slot at 4, a focuser
// with room to move, and a camera whose ceilings the pickers respect.
const STATUS: any = {
  busy_lanes: [],
  looping: false,
  camera: { max_bin: 4, max_gain: 500 },
  filterwheel: { position: 3, names: ["L", "R", "G", "Oiii", "Dark"],
                 opaque: [false, false, false, false, true] },
  focuser: { position: 18000, max: 60000, moving: false, can_set_position: true },
};
const ADMIN = {
  role: "admin", email: null,
  caps: ["view.status", "view.preview", "control.mount", "control.capture",
         "control.guide"],
};
// Values no client constant contains, so a face showing them proves the server
// reached the screen rather than a default agreeing by coincidence.
const FOCUS_FRAME = { exposure_s: 41, gain: 29, offset: 11, binning: 2, filter: null };

const puts: Array<{ path: string; body: any }> = [];
const posts: Array<{ path: string; body: any }> = [];
const server: Record<string, any> = {};
(api as any).put = async (path: string, body: any) => {
  puts.push({ path, body });
  const scope = new URL(path, "http://local").searchParams.get("scope")!;
  server[scope] = { ...(server[scope] ?? {}), ...body };
  return { scope, settings: server[scope], frames: server };
};
(api as any).post = async (path: string, body: any) => { posts.push({ path, body }); return {}; };
(api as any).get = async () => ({});

let root: any = null;
let host: any = null;

function mount(over: Partial<typeof STATUS> = {}): HTMLElement {
  for (const k of Object.keys(server)) delete server[k];
  Object.assign(server, { focus: { ...FOCUS_FRAME } });
  puts.length = 0;
  posts.length = 0;
  useStore.setState({
    status: { ...STATUS, ...over }, principal: ADMIN,
    frameSettings: EMPTY_FRAME_SETTINGS(),
  } as any);
  act(() => {
    useStore.getState().handleEvent({ type: "hello", data: { frames: server } } as any);
  });
  if (root) act(() => root.unmount());
  if (host) host.remove();
  host = win.document.createElement("div");
  win.document.body.appendChild(host);
  root = createRoot(host);
  act(() => root.render(React.createElement(FocusView)));
  return host as HTMLElement;
}
const unmount = () => { if (root) { act(() => root.unmount()); root = null; } };

const all = (sel: string): any[] => Array.from(win.document.querySelectorAll(sel));
const byLabel = (prefix: string): any =>
  all("[aria-label]").find((el) => (el.getAttribute("aria-label") ?? "").startsWith(prefix));
const click = (el: any) => act(() => el.click());
const dialItem = (key: string): any =>
  all("[data-dial-item]").find((b) => b.getAttribute("data-dial-item") === key);
/** The autofocus hero. It carries an aria-label only while it is REFUSING
 *  (the reason is the label), so an unblocked one is found by its face. */
const hero = (): any =>
  byLabel("Focus my scope")
  ?? all("button").find((b: any) => (b.textContent ?? "").includes("Focus my scope"));

// ================================ 0. the control it moved TO has to exist
await test("the dial still fits on the shortest stage this screen allows",
  async () => {
    // THE DEFECT THIS APP HAS ALREADY SHIPPED ONCE: a setting reachable only
    // through a radial dial, and the dial deletes itself rather than draw an
    // overlapping arc below DIAL_MIN_R — so the control existed on a desktop
    // and was gone on the phone the screen is used from. Moving the exposure
    // onto the dial (#180) is only safe if the dial cannot vanish, and
    // FocusView's own stage floor is what decides that. 326px is a 390px
    // phone's compact stage; the HEIGHT is the binding constraint here.
    const r = dialRadius({ w: 326, h: POD_MIN_STAGE_H }, FOCUS_DIAL_INSET);
    assert.ok(r >= DIAL_MIN_R,
      `the settings dial gets radius ${r} on the shortest stage FocusView allows `
      + `(${POD_MIN_STAGE_H}px) — under ${DIAL_MIN_R} it renders nothing, and the `
      + "exposure, gain and binning over the frame go with it");
  });

// ============================================ 1. one control per setting
await test("every camera setting has exactly ONE control, with every panel open",
  async () => {
    mount();
    // Worst case on purpose: open the autofocus disclosure so that every
    // settings surface this screen has is on screen at the same time. That is
    // the state in which three exposures used to be visible at once.
    const gear = byLabel("Autofocus settings");
    assert.ok(gear, "no autofocus settings toggle — the panel never opened");
    click(gear);

    const labels = all("span.label").map((s: any) => (s.textContent ?? "").trim());
    for (const [name, re] of [
      ["Exposure", /^Exposure/], ["Gain", /^Gain/], ["Binning", /^Binning/],
    ] as const) {
      const n = labels.filter((t: string) => re.test(t)).length;
      assert.equal(n, 1,
        `${name} has ${n} fields on one screen (${labels.join(" | ")}) — the ` +
        "sweep's private copy is back beside the camera's");
    }
    // …and no flat picker strip either: EXP/GAIN/BIN/FILT as PickerButtons in
    // the Autofocus panel were a THIRD copy of the same four settings, on a
    // screen that already had the dial over the frame.
    assert.equal(all("[data-picker]").length, 0,
      "a legacy picker strip is back on the Focus screen: "
      + all("[data-picker]").map((p: any) => p.getAttribute("data-picker")).join(","));
    unmount();
  });

// ================================= 2. the one over the frame is the shared one
await test("the dial over the frame writes the SHARED focus scope", async () => {
  mount();
  const disc = win.document.querySelector("[data-dial-disc]");
  assert.ok(disc, "no camera-settings dial over the preview");
  assert.ok(/41s g29/.test(disc.getAttribute("aria-label") ?? ""),
    `the dial's face is not the server's settings: ${disc.getAttribute("aria-label")}`);

  click(disc);
  const gain = dialItem("gain");
  assert.ok(gain, "the dial has no gain ring");
  click(gain);
  const pick = dialItem("300");
  assert.ok(pick, "gain 300 is not offered");
  click(pick);
  await act(async () => {});

  const put = puts.find((p) => /scope=focus/.test(p.path) && p.body.gain != null);
  assert.ok(put, `the pick never left the screen it was made on: ${JSON.stringify(puts)}`);
  assert.equal(put!.body.gain, 300, "the wrong gain reached the server");
  assert.equal(useStore.getState().frameSettings.focus.gain, 300,
    "the store still holds the old gain");
  // …and the Camera panel's own box agrees, because it is the same value and
  // not a copy of it.
  const box: any = win.document.querySelector('[data-frame-field="gain"]');
  assert.equal(box?.value, "300", "the panel's box and the dial disagree about one camera");
  unmount();
});

// ===================================== 3. the sweep still gets those settings
await test("the sweep is sent the Camera panel's settings, not a deleted copy",
  async () => {
    mount();
    // The panel open is what puts the operator's own settings in force
    // (sweepReadiness `manual`); closed, the sweep derives from the last frame,
    // which is a different claim and a different code path.
    click(byLabel("Autofocus settings"));
    const run = hero();
    assert.ok(run, "no autofocus hero to press");
    click(run);
    await act(async () => {});

    const post = posts.find((p) => p.path === "/api/focuser/autofocus");
    assert.ok(post, `the sweep never left: ${JSON.stringify(posts)}`);
    assert.equal(post!.body.exposure_s, FOCUS_FRAME.exposure_s,
      "the sweep no longer carries the exposure the screen is set to");
    assert.equal(post!.body.gain, FOCUS_FRAME.gain,
      "the sweep no longer carries the gain the screen is set to");
    assert.equal(post!.body.binning, FOCUS_FRAME.binning,
      "the sweep no longer carries the binning the screen is set to");
    unmount();
  });

// ================================= 3b. the pin does not need a hidden mode
await test("a filter picked on the dial is SENT, without opening anything",
  async () => {
    // The dial's picks used to force the autofocus settings panel open behind
    // the operator's back, because the request only carried the filter while
    // that panel was open — a one-handed tap in the dark silently switched the
    // sweep from "derive it" to "use my numbers". The pin rides along
    // unconditionally now, so the tap can mean only what it looks like.
    mount();
    click(win.document.querySelector("[data-dial-disc]"));
    click(dialItem("filter"));
    const r = dialItem("R");
    assert.ok(r, "the filter ring does not offer R");
    click(r);
    await act(async () => {});

    const gear = byLabel("Autofocus settings");
    assert.equal(gear.getAttribute("aria-expanded"), "false",
      "picking a filter opened the settings panel — a tap that changes more than it says");

    click(gear);
    click(hero());
    await act(async () => {});
    const post = posts.find((p) => p.path === "/api/focuser/autofocus");
    assert.ok(post, "the sweep never left");
    assert.equal(post!.body.filter, 1,
      `the pinned filter is not in the request: ${JSON.stringify(post!.body)}`);
    unmount();
  });

// ============================== 4. the refusal survived the move
await test("a restart the rig will not take is refused, and changes nothing",
  async () => {
    // A loop is running AND a sequence owns the camera, so a new exposure
    // cannot be issued: hub.start_loop closed over the exposure it was handed,
    // so a control that only moved the number would be applied on screen and
    // ignored by the camera. The pod's badge used to carry this refusal as a
    // LockedChip; the setting moved to the dial, so the refusal has to be here.
    mount({ looping: true });
    act(() => {
      useStore.setState({
        sequence: { ...(useStore.getState() as any).sequence, state: "running" },
      } as any);
    });

    const presets = all("[aria-label]").filter((el: any) =>
      /^\d+ second exposure/.test(el.getAttribute("aria-label") ?? ""));
    assert.ok(presets.length >= 5, `the rail's presets are missing: ${presets.length}`);
    for (const p of presets) {
      assert.equal(p.getAttribute("aria-disabled"), "true",
        `a preset stayed live over a restart the rig refuses: ${p.getAttribute("aria-label")}`);
      assert.ok(/unavailable: .+/.test(p.getAttribute("aria-label") ?? ""),
        `a blocked preset does not say why: ${p.getAttribute("aria-label")}`);
    }

    // The same refusal, on the control the setting moved to.
    puts.length = 0;
    const disc = win.document.querySelector("[data-dial-disc]");
    click(disc);
    click(dialItem("exposure"));
    const five = dialItem("5");
    assert.ok(five, "the exposure ring has no 5s");
    click(five);
    await act(async () => {});
    assert.equal(puts.filter((p) => p.body.exposure_s != null).length, 0,
      "the dial wrote an exposure the rig has already refused to shoot at");
    assert.equal(useStore.getState().frameSettings.focus.exposure_s,
      FOCUS_FRAME.exposure_s,
      "the refused pick still changed the setting — applied on screen, ignored by the camera");
    unmount();
  });

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`focusDialSplit.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
