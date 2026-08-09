// cameraSettingsAgreement.test.tsx — one rig, one answer.
//
//   Run directly:  npx tsx src/__tests__/cameraSettingsAgreement.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// THE CLASS THIS EXISTS FOR. On 2026-08-08 the operator set FILT=R on the Align
// screen and the Capture screen said Oiii. Both were internally honest — Align
// was rendering a server-side pin the polar session only acts on inside a solve
// frame, Capture was rendering the wheel — and neither said which question it
// was answering. Every camera setting behind those screens had the same shape:
// a private `useState` seeded from a hardcoded constant, invisible to every
// other surface and lost on reload.
//
// A test that mounts ONE component cannot see that. So this one mounts SEVERAL
// against one store and asserts they agree — and asserts the server's value
// reaches a COLD screen, because the un-called `GET /api/polar/solve-settings`
// was the other half of the defect.
//
// It reads what a surface DISPLAYS, off the DOM, through stable
// data-attributes. Reading React state would pass over a screen that renders
// something else, which is the failure itself.

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
// The preview stage observes its own box; jsdom has neither observer.
g.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
g.IntersectionObserver = class {
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
};
g.requestAnimationFrame = (cb: (t: number) => void) => setTimeout(() => cb(0), 0);
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------------- imports
// DYNAMIC, and that is load-bearing. A static `import` is evaluated BEFORE any
// of the jsdom bootstrap above, so react-dom would compute `canUseDOM = false`
// at module-load time and fall back to its IE-era onChange polyfill — under
// which setting an input's value and firing `input` does nothing at all, and a
// test that "types" into a box would silently be testing nothing. (That is why
// this file imports React the way settingsGatesDom.test.tsx does and not the
// way the click-only DOM tests do.)
const React = (await import("react")).default;
const { act } = await import("react");
const { createRoot } = await import("react-dom/client");

// House harness (run-tests.mjs scores the printed tally / exported result):
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
const assert = {
  ok(cond: unknown, msg?: string) { if (!cond) throw new Error(msg ?? "not ok"); },
  equal(a: unknown, b: unknown, msg?: string) {
    if (a !== b) throw new Error(msg ?? `${String(a)} !== ${String(b)}`);
  },
  match(text: string, re: RegExp, msg?: string) {
    if (!re.test(text)) throw new Error(msg ?? `no match for ${re} in: ${text.slice(0, 250)}`);
  },
};

const { api } = await import("../api");
const { useStore } = await import("../store");
const { EMPTY_FRAME_SETTINGS } = await import("../lib/authGate");
const CaptureView = (await import("../views/CaptureView")).default;
const FocusView = (await import("../views/FocusView")).default;
const PolarView = (await import("../views/PolarView")).default;

// THE RIG: a 5-slot wheel parked on Oiii (slot 3, opaque Dark at 4).
const WHEEL = {
  position: 3,
  names: ["L", "R", "G", "Oiii", "Dark"],
  opaque: [false, false, false, false, true],
};
const STATUS = {
  busy_lanes: [],
  filterwheel: WHEEL,
  camera: { max_bin: 4, max_gain: 500 },
} as any;
const ADMIN = {
  role: "admin", email: null,
  caps: ["view.status", "view.preview", "control.mount", "control.capture",
         "control.guide"],
};

// Every write goes to the server and comes back through the store, exactly as
// it does in the app. A double that mutated the store directly would pass over
// a UI whose writes never leave the screen — the defect itself.
const server: Record<string, any> = {};
const puts: Array<{ path: string; body: any }> = [];
(api as any).put = async (path: string, body: any) => {
  puts.push({ path, body });
  const scope = new URL(path, "http://local").searchParams.get("scope")!;
  server[scope] = { ...(server[scope] ?? {}), ...body };
  return { scope, settings: server[scope], frames: server };
};
(api as any).get = async () => ({});
(api as any).post = async () => ({});

function reset(frames?: Record<string, any>) {
  for (const k of Object.keys(server)) delete server[k];
  Object.assign(server, frames ?? {});
  // Back to COLD every time — including the frame settings, or a later test
  // would be grading the previous one's writes.
  useStore.setState({
    status: STATUS, principal: ADMIN,
    frameSettings: EMPTY_FRAME_SETTINGS(),
  } as any);
  // The transport the app really uses: the server announces, the store applies.
  act(() => {
    useStore.getState().handleEvent(
      { type: "hello", data: { frames: server } } as any);
  });
}

/** Mount a view into its own detached root; returns the DOM it produced. */
function mount(el: React.ReactElement): { dom: HTMLElement; close: () => void } {
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(el); });
  return {
    dom: host,
    close: () => { act(() => { root.unmount(); }); host.remove(); },
  };
}

/** The value a surface DISPLAYS for one setting, read off the rendered DOM.
 *  Three shapes are legitimate — a picker's face, a typed field, the radial
 *  dial's summary — and this asks for whichever the surface actually uses. */
function shown(dom: HTMLElement, field: string): string {
  const box = dom.querySelector(`[data-frame-field="${field}"]`) as any;
  if (box) return String(box.value ?? "");
  const label = { exposure_s: "EXP", gain: "GAIN", binning: "BIN",
                  offset: "OFFS" }[field] ?? field;
  const face = dom.querySelector(
    `[data-picker="${label}"] [data-picker-summary]`);
  if (face) return (face.textContent ?? "").trim();
  const disc = dom.querySelector("[data-dial-disc]");
  if (disc) return (disc.getAttribute("aria-label") ?? "").trim();
  throw new Error(`no ${field} control on this surface`);
}

/** Type into a controlled input the way React notices: set through the NATIVE
 *  setter (bypassing React's value tracker) then fire `input`. */
function type(el: any, value: string): void {
  const proto = el.tagName === "SELECT" ? win.HTMLSelectElement : win.HTMLInputElement;
  Object.getOwnPropertyDescriptor(proto.prototype, "value")!.set!.call(el, value);
  el.dispatchEvent(new win.Event(
    el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
}

/** What a surface says is in (or going into) the light path. Capture names its
 *  control "Slot" and Align names it "FILT"; both answer the same question. */
function shownFilter(dom: HTMLElement): string {
  const el = dom.querySelector('[data-picker="FILT"] [data-picker-summary]')
    ?? dom.querySelector('[data-picker="Slot"] [data-picker-summary]');
  if (!el) throw new Error("no filter control on this surface");
  return (el.textContent ?? "").trim();
}

// ============================================================ the disagreement

await test("every surface names the filter the WHEEL is actually on", async () => {
  reset();
  for (const [name, make] of [
    ["Capture", () => React.createElement(CaptureView)],
    ["Align", () => React.createElement(PolarView)],
  ] as const) {
    const m = mount(make());
    const face = shownFilter(m.dom);
    m.close();
    assert.match(face, /Oiii/,
      `${name} does not name the wheel's actual filter (Oiii); it says ` +
      `"${face}". A pin may be shown IN ADDITION ("Oiii → R") but never ` +
      "INSTEAD.");
  }
});

await test("a filter pinned on Align is never rendered as the wheel's position",
  async () => {
    reset();
    const align = mount(React.createElement(PolarView));
    // The reported gesture: FILT → R on the Align screen.
    act(() => { useStore.getState().setFrameSettings("solve", { filter: "R" }); });
    await act(async () => {});

    // 1. The pin took — it reached the server, not just this screen.
    assert.equal(server.solve?.filter, "R",
      "the pick never left the screen it was made on");
    // 2. The wheel did NOT move. Asserted rather than assumed: a fix that
    //    moved the wheel here is also defensible, and would have to change
    //    this line on purpose rather than by drifting past it.
    assert.equal(useStore.getState().status!.filterwheel!.position, 3);
    // 3. THE BUG. Capture must not now disagree with Align about the wheel.
    const capture = mount(React.createElement(CaptureView));
    const captureFace = shownFilter(capture.dom);
    const alignFace = shownFilter(align.dom);
    capture.close();
    align.close();
    assert.ok(captureFace.includes("Oiii") && alignFace.includes("Oiii"),
      `Align says "${alignFace}", Capture says "${captureFace}" — one wheel, ` +
      "two answers.");
    // 4. And the pin is still visible on Align, or the operator has no way to
    //    know R will be asserted the moment the run starts.
    assert.match(alignFace, /R/,
      "the pin vanished — a silent wheel move is queued and nothing says so");
  });

// ================================================== the server reaches a cold screen

await test("no imaging setting starts from a hardcoded default", async () => {
  // The reload defect, generalised. Seed the SERVER with values no client
  // constant contains, mount COLD, and require every surface to show them.
  reset({
    capture: { exposure_s: 47, gain: 33, offset: 7, binning: 3, filter: null },
    focus: { exposure_s: 41, gain: 29, offset: 11, binning: 2, filter: null },
    solve: { exposure_s: 53, gain: 17, offset: 13, binning: 4, filter: null },
    guide: { exposure_s: 3.5, gain: 111, offset: 12, binning: 1, filter: null },
  });
  for (const [name, make, scope] of [
    ["Capture", () => React.createElement(CaptureView), "capture"],
    ["Focus", () => React.createElement(FocusView), "focus"],
    ["Align", () => React.createElement(PolarView), "solve"],
  ] as const) {
    const m = mount(make());
    const want = server[scope];
    const exp = shown(m.dom, "exposure_s");
    const gain = shown(m.dom, "gain");
    const bin = shown(m.dom, "binning");
    m.close();
    assert.match(exp, new RegExp(String(want.exposure_s)),
      `${name} ignored the server's exposure (${want.exposure_s}) and showed ` +
      `"${exp}" — it is seeded from a constant`);
    assert.match(gain, new RegExp(String(want.gain)),
      `${name} showed gain "${gain}", not the server's ${want.gain}`);
    assert.match(bin, new RegExp(String(want.binning)),
      `${name} showed binning "${bin}", not the server's ${want.binning}`);
  }
});

await test("the Align screen's offset is REACHABLE, not just stored", async () => {
  // It was reachable only through the radial dial on the reticle, which hides
  // itself entirely on a stage under 124 px — i.e. on a phone, which is what
  // polar alignment is done from.
  reset({ solve: { exposure_s: 53, gain: 17, offset: 13, binning: 4, filter: null } });
  const align = mount(React.createElement(PolarView));
  const offs = align.dom.querySelector('[data-picker="OFFS"] [data-picker-summary]');
  align.close();
  assert.ok(offs, "the Align screen offers no reachable offset control");
  assert.match((offs!.textContent ?? "").trim(), /13/);
});

// ================================================ the session does not own them

await test("an alignment starting does not revert the faces to defaults",
  async () => {
    // polar/session.py start() resets state to _idle(), which carries no
    // solve_settings, and the client applies polar events wholesale. While the
    // settings rode that event, beginning a run wiped them off every screen
    // and the engine went on using the operator's values regardless.
    reset({ solve: { exposure_s: 53, gain: 17, offset: 13, binning: 4, filter: null } });
    const align = mount(React.createElement(PolarView));
    assert.match(shown(align.dom, "exposure_s"), /53/, "cold seed failed");
    act(() => {
      useStore.getState().handleEvent({
        type: "polar",
        data: { state: "running", az_error: 0, alt_error: 0, total_error: 0,
                progress: 0, message: "", source: "native" },
      } as any);
    });
    const after = shown(align.dom, "exposure_s");
    align.close();
    assert.match(after, /53/,
      `the run reverted the face to "${after}" while the engine kept using 53s`);
  });

// ==================================================== the scopes stay separate

await test("a guide camera's exposure is not the imaging camera's", async () => {
  // The INVERSE guard, and it matters as much as the agreement ones: folding
  // the scopes together is as wrong as splitting them per screen. A 5-minute
  // light frame must never become a 5-minute guide frame.
  reset({
    capture: { exposure_s: 300, gain: 0, offset: 0, binning: 1, filter: null },
    guide: { exposure_s: 2, gain: 100, offset: 30, binning: 1, filter: null },
  });
  const s = useStore.getState().frameSettings;
  assert.equal(s.capture.exposure_s, 300);
  assert.equal(s.guide.exposure_s, 2,
    "the guide camera inherited the imaging camera's 5-minute exposure");
  // ...and a write to one may not move the other.
  act(() => { useStore.getState().setFrameSettings("capture", { gain: 400 }); });
  await act(async () => {});
  assert.equal(useStore.getState().frameSettings.guide.gain, 100,
    "setting the imaging gain moved the guide camera's");
});

await test("a solve frame's settings are not a light frame's", async () => {
  reset();
  act(() => { useStore.getState().setFrameSettings("solve", { exposure_s: 90 }); });
  await act(async () => {});
  assert.equal(useStore.getState().frameSettings.capture.exposure_s, 2,
    "a longer solve exposure rewrote the light frame — a 0.3s L solve under a "
    + "narrowband plan is correct behaviour and folding the two breaks it");
});

// ======================================================== the write is honest

await test("a write reaches the SERVER, not just the screen that made it",
  async () => {
    reset();
    puts.length = 0;
    act(() => { useStore.getState().setFrameSettings("focus", { gain: 275 }); });
    await act(async () => {});
    assert.equal(puts.length, 1, "the pick never left the browser");
    assert.match(puts[0].path, /scope=focus/);
    assert.equal(puts[0].body.gain, 275);
  });

await test("a refused write rolls the face back instead of lying", async () => {
  // The guider refuses a binning change mid-session (409). A dial left showing
  // a value the rig rejected is this same defect in a new place.
  reset();
  const before = useStore.getState().frameSettings.guide.binning;
  const realPut = (api as any).put;
  (api as any).put = async () => { throw new Error("stop guiding first"); };
  act(() => { useStore.getState().setFrameSettings("guide", { binning: 4 }); });
  await act(async () => {});
  (api as any).put = realPut;
  assert.equal(useStore.getState().frameSettings.guide.binning, before,
    "the store kept a binning the rig refused");
});

// ============================================== the guards the migration risked

await test("a blank exposure still blocks the shutter and never reaches the store",
  async () => {
    // THE MIGRATION'S SINGLE HIGHEST-RISK ITEM. Capture's boxes are strings on
    // purpose: `isExposureInvalid` and the gain guard read the RAW string so
    // that "", "1e9" and "-3" mark the field and block the shot. Bind the input
    // to a number and all three stop guarding — a blank box becomes a 0s
    // exposure, i.e. the blank frame plus the misleading "few stars" that
    // CAP-02 was filed for.
    reset({ capture: { exposure_s: 12, gain: 120, offset: 30, binning: 1,
                       filter: null } });
    const cap = mount(React.createElement(CaptureView));
    const box: any = cap.dom.querySelector('[data-frame-field="exposure_s"]');
    assert.ok(box, "no exposure field");
    for (const bad of ["", "1e9", "-3"]) {
      await act(async () => { type(box, bad); });
      assert.equal(box.getAttribute("aria-invalid"), "true",
        `"${bad}" is not marked invalid — the guard reads the raw string and ` +
        "the box no longer holds one");
      // ...and leaving the field must not launder it into the shared scope
      // either. React maps onBlur onto the BUBBLING `focusout`.
      await act(async () => {
        box.dispatchEvent(new win.Event("focusout", { bubbles: true }));
      });
      await act(async () => {});
      assert.equal(useStore.getState().frameSettings.capture.exposure_s, 12,
        `"${bad}" reached the shared scope; every other surface now believes it`);
    }
    cap.close();
  });

await test("a typed exposure DOES reach the store once committed", async () => {
  // The other half: a draft that never commits is a per-screen copy wearing a
  // different hat, and the operator who typed 300 would be shooting 300 while
  // every other surface said 12.
  reset({ capture: { exposure_s: 12, gain: 120, offset: 30, binning: 1,
                     filter: null } });
  const cap = mount(React.createElement(CaptureView));
  const box: any = cap.dom.querySelector('[data-frame-field="exposure_s"]');
  await act(async () => { type(box, "300"); });
  // React maps onBlur onto the BUBBLING `focusout`, not `blur`.
  await act(async () => {
    box.dispatchEvent(new win.Event("focusout", { bubbles: true }));
  });
  cap.close();
  assert.equal(useStore.getState().frameSettings.capture.exposure_s, 300,
    "the typed value never left the box it was typed in");
});

await test("the dial builder drops opaque slots for EVERY caller", async () => {
  // A blackout slot is a carrier with no glass. A focus sweep through one
  // measures nothing at every position, and the engine only advances when a
  // measurement lands — so it is a HANG, not a bad result (fourteen
  // re-exposures at one focuser position on the rig, 2026-08-08).
  //
  // FilterPicker had always excluded them; PolarView excluded them at the call
  // site; FocusView did not. One of three callers getting it right is what a
  // caller-side rule looks like from the inside, so the rule moved into the
  // builder.
  const { cameraDialCategories } = await import("../components/ui/CameraPickers");
  const cats = cameraDialCategories({
    values: { exposure_s: 1, gain: 100, binning: 1, offset: 30, filter: null },
    filters: WHEEL.names,
    opaqueSlots: WHEEL.opaque,
    currentFilter: "Oiii",
    onExposure: () => {}, onGain: () => {}, onFilter: () => {},
  });
  const filt = cats.find((c: any) => c.id === "filter") as any;
  assert.ok(filt, "no FILT ring");
  const ids = filt.options.map((o: any) => o.id);
  assert.ok(!ids.includes("Dark"),
    `the ring offers a blackout slot as an imaging filter: ${ids.join(", ")}`);
  assert.ok(ids.includes("Oiii"), String(ids));
  // Every hub carries a glyph — the ring falls back to label text without one.
  for (const id of ["exposure", "gain", "binning", "filter"]) {
    const c = cats.find((x: any) => x.id === id);
    if (c) assert.ok((c as any).icon, `the ${id} hub has no icon`);
  }
});

// ------------------------------------------------------------------- report
const total = passed + failed;
console.log(`cameraSettingsAgreement.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
