// primitivesDom.test.tsx - the next-UI primitives, MOUNTED and pressed.
//
//   Run directly:  npx tsx src/next/ui/__tests__/primitivesDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT. These are the components every hub is about to be built
// out of, so a broken promise here is a broken promise on twenty screens at
// once. The four contracts worth spending a test on:
//
//   1. HONEST-DISABLED (ARCHITECTURE section 6/8). A locked control must dim,
//      carry aria-disabled, STAY in the tab order, and SAY the reason when
//      pressed. The failure this guards against is the silent one: a control
//      that looks live, swallows the tap and explains nothing. Every locked
//      test below asserts both halves - the action did NOT run AND the reason
//      did reach `onExplain`.
//   2. The two-tap ARM on STOP. One press must never stop a night's imaging.
//   3. SHAPE, NOT HUE (night mode). A selected readout tile carries a marker
//      element, not only an accent border; a switch carries an ON/OFF word.
//      Under `:root.night` every token is the same red, so a hue-only state is
//      no state at all.
//   4. The Dial reaches its value by BOTH routes - a tap on a stop and an
//      arrow key - because on a phone the first is the only one and on a
//      desktop the second is.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally
// plus the `{ passed, failed, total }` export (shell-and-tests.md section 4).

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
// jsdom implements neither; the Dial calls both behind a guard, and the guard
// is only meaningful if the happy path is exercised somewhere too.
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "getComputedStyle", "matchMedia",
  "requestAnimationFrame", "cancelAnimationFrame",
  // NOT `performance`: this jsdom's Performance delegates to the global one,
  // so copying it onto globalThis makes `performance.now()` call itself until
  // the stack blows. Node's own is fine here - nothing under test reads a
  // clock, unlike holdButtonDom, which installs a hand-cranked one instead.
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act, useRef, useState } = await import("react");
const { createRoot } = await import("react-dom/client");
const {
  ActionButton, Bar, Dial, IncidentCard, Popover, ReadoutGrid, ReadoutTile,
  Segmented, Sheet, Switch,
} = await import("../index");

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

const container = win.document.getElementById("root") as any;
const root = createRoot(container);

const render = (el: any) => { act(() => { root.render(el); }); };
const q = (sel: string): any => container.querySelector(sel);
const qa = (sel: string): any[] => Array.from(container.querySelectorAll(sel));
const click = (el: any) => {
  act(() => { el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
};
const keydown = (el: any, key: string) => {
  act(() => { el.dispatchEvent(new win.KeyboardEvent("keydown", { key, bubbles: true, cancelable: true })); });
};
const wait = async (ms: number) => {
  await act(async () => { await new Promise((r) => setTimeout(r, ms)); });
};

// ============================================================ ActionButton

{
  let fired = 0;
  render(createElement(ActionButton, {
    kind: "primary", size: "xl", onPress: () => { fired++; }, "data-testid": "go",
  } as any, "IMAGE M31"));
  const go = () => q('[data-testid="go"]');

  test("precondition: the primary CTA rendered with its label", () => {
    assert(go() != null, "no button - the fixture is wrong, not the component");
    assert(/IMAGE M31/.test(go().textContent), "the label never reached the DOM");
    eq(go().getAttribute("data-size"), "xl", "size did not reach the element");
  });

  test("ActionButton fires onPress", () => {
    eq(fired, 0, "precondition: nothing fired before the press");
    click(go());
    eq(fired, 1, "the press did not reach onPress");
  });
}

// -------------------------------------------------------- the two-tap arm

{
  let stops = 0;
  render(createElement(ActionButton, {
    kind: "danger", size: "lg", onPress: () => { stops++; },
    arm: { label: "PRESS AGAIN TO STOP" }, "data-testid": "stop",
  } as any, "STOP"));
  const stop = () => q('[data-testid="stop"]');

  test("precondition: STOP renders unarmed and says STOP", () => {
    eq(stop().getAttribute("data-armed"), "false", "it started armed");
    assert(/STOP/.test(stop().textContent), "the label never rendered");
  });

  test("arm: ONE press does not stop the run", () => {
    click(stop());
    eq(stops, 0, "a single press stopped the session - the arm is not arming");
    eq(stop().getAttribute("data-armed"), "true", "the first press did not arm");
    assert(/PRESS AGAIN TO STOP/.test(stop().textContent),
      "the armed label is not shown, so nothing tells the user a second tap is live");
  });

  test("arm: the second press inside the window fires", () => {
    click(stop());
    eq(stops, 1, "the second press did not fire");
    eq(stop().getAttribute("data-armed"), "false", "it stayed armed after firing");
    assert(/STOP/.test(stop().textContent), "the label did not return");
  });
}

await testAsync("arm: the window LAPSES - a late second press re-arms, it does not fire", async () => {
  let late = 0;
  render(createElement(ActionButton, {
    kind: "danger", onPress: () => { late++; },
    arm: { label: "PRESS AGAIN TO STOP", ms: 20 }, "data-testid": "lapse",
  } as any, "STOP"));
  const el = () => q('[data-testid="lapse"]');
  click(el());
  eq(el().getAttribute("data-armed"), "true", "precondition: the first press armed");
  await wait(60);
  eq(el().getAttribute("data-armed"), "false", "the window never lapsed - the arm is permanent");
  click(el());
  eq(late, 0, "a press after the window lapsed FIRED - the arm silently became one tap");
});

// ------------------------------------------------------ honest-disabled

{
  let fired = 0;
  const explained: string[] = [];
  const REASON = "needs operator or admin access";
  render(createElement(ActionButton, {
    kind: "primary", onPress: () => { fired++; },
    lockedReason: REASON, onExplain: (r: string) => { explained.push(r); },
    "data-testid": "locked",
  } as any, "RUN NOW"));
  const el = () => q('[data-testid="locked"]');

  test("locked: aria-disabled is set and the native attribute is NOT", () => {
    eq(el().getAttribute("aria-disabled"), "true", "no aria-disabled - the reason is invisible to a reader");
    assert(!el().hasAttribute("disabled"),
      "the native disabled attribute is on it: that strips the control from the a11y tree " +
      "and takes the reason with it");
    eq(el().getAttribute("data-locked"), "true", "no data-locked hook for the dimmed style");
    eq(el().getAttribute("title"), REASON, "the reason is not on hover either");
  });

  test("locked: the press explains instead of acting", () => {
    click(el());
    eq(fired, 0, "a locked control ran its action anyway");
    eq(explained.length, 1, "the blocked press said NOTHING - the silent-no-op defect");
    eq(explained[0], REASON, "the wrong reason reached the user");
  });
}

// =================================================================== Switch

{
  const toggles: boolean[] = [];
  function SwitchHarness() {
    const [on, setOn] = useState(false);
    return createElement(Switch, {
      checked: on,
      onChange: (v: boolean) => { toggles.push(v); setOn(v); },
      label: "Cooler",
      note: "at -10C - stable 2 min",
      "data-testid": "cooler",
    } as any);
  }
  render(createElement(SwitchHarness));
  const sw = () => q('[data-testid="cooler"]');

  test("Switch has an accessible name and a role", () => {
    eq(sw().getAttribute("role"), "switch", "it is not a switch to a screen reader");
    eq(sw().getAttribute("aria-label"), "Cooler", "the switch has no accessible name");
    eq(sw().getAttribute("aria-checked"), "false", "precondition: it starts off");
  });

  test("Switch carries the state as a WORD as well as a knob position", () => {
    assert(/OFF/.test(sw().textContent),
      "no ON/OFF micro label: under :root.night the whole palette is one red and " +
      "a colour-only toggle has no readable state");
  });

  test("Switch toggles", () => {
    click(sw());
    eq(toggles.length, 1, "the press never reached onChange");
    eq(toggles[0], true, "it did not report the NEW value");
    eq(sw().getAttribute("aria-checked"), "true", "aria-checked did not follow the state");
    assert(/ON/.test(sw().textContent), "the ON/OFF word did not follow the state");
  });
}

// ===================================================================== Dial

const DIAL_OPTS = [
  { value: "-20", label: "-20C" },
  { value: "-15", label: "-15C" },
  { value: "-10", label: "-10C" },
  { value: "-5", label: "-5C" },
];

{
  const picked: string[] = [];
  render(createElement(Dial, {
    label: "SETPOINT", options: DIAL_OPTS, value: "-15",
    onChange: (v: string) => { picked.push(v); },
    "data-testid": "setpoint",
  } as any));
  const track = () => q(".nx-dial-track");

  test("precondition: the Dial announces the SETTING, not the index", () => {
    assert(track() != null, "no dial track - the fixture is wrong");
    eq(track().getAttribute("role"), "slider", "it is not a slider");
    eq(track().getAttribute("aria-valuenow"), "1", "the selected stop is not the value");
    eq(track().getAttribute("aria-valuetext"), "-15C",
      "aria-valuetext is missing: a reader would announce \"1 of 4\", which tells " +
      "the user nothing about the setpoint");
    eq(qa(".nx-dial-stop").length, 4, "not every option rendered a stop");
  });

  test("Dial: the selected stop is centred under the marker", () => {
    // 64 px per stop, so stop 1 sits at -(1*64 + 32) = -96px.
    const strip = q(".nx-dial-strip");
    assert(/translateX\(-96px\)/.test(strip.getAttribute("style") || ""),
      `the strip is not translated to centre the selected stop (${strip.getAttribute("style")})`);
    eq(q('.nx-dial-stop[data-selected="true"]').getAttribute("data-value"), "-15",
      "the wrong stop is marked selected");
  });

  test("Dial selects by TAP on a stop", () => {
    click(q('.nx-dial-stop[data-value="-10"]'));
    eq(picked.length, 1, "tapping a stop did nothing - on a phone that is the only route in");
    eq(picked[0], "-10", "the tap picked the wrong stop");
  });

  test("Dial selects by ArrowRight", () => {
    keydown(track(), "ArrowRight");
    eq(picked.length, 2, "ArrowRight did nothing - the dial is unreachable by keyboard");
    eq(picked[1], "-10", "ArrowRight moved to the wrong stop");
  });

  test("Dial: ArrowLeft moves the other way and the ends do not wrap", () => {
    keydown(track(), "ArrowLeft");
    eq(picked[2], "-20", "ArrowLeft moved the wrong way");
    render(createElement(Dial, {
      label: "SETPOINT", options: DIAL_OPTS, value: "-20",
      onChange: (v: string) => { picked.push(v); },
    } as any));
    keydown(q(".nx-dial-track"), "ArrowLeft");
    eq(picked.length, 3, "the dial wrapped past the end of its range");
  });
}

{
  let changed = 0;
  const explained: string[] = [];
  const REASON = "a flow owns the mount";
  render(createElement(Dial, {
    label: "SLEW RATE", options: DIAL_OPTS, value: "-15",
    onChange: () => { changed++; },
    lockedReason: REASON, onExplain: (r: string) => { explained.push(r); },
    "data-testid": "locked-dial",
  } as any));
  const track = () => q(".nx-dial-track");

  test("Dial locked: aria-disabled, and neither route changes the value", () => {
    eq(track().getAttribute("aria-disabled"), "true", "the locked dial is not marked disabled");
    keydown(track(), "ArrowRight");
    click(q('.nx-dial-stop[data-value="-10"]'));
    eq(changed, 0, "a locked dial moved the setting anyway");
    eq(explained.length, 2, "the blocked key and the blocked tap said nothing");
    eq(explained[0], REASON, "the wrong reason reached the user");
  });
}

// ================================================================ Segmented

{
  const picks: number[] = [];
  render(createElement(Segmented, {
    label: "Binning", value: 1,
    onChange: (v: number) => { picks.push(v); },
    options: [
      { value: 1, label: "1x1" },
      { value: 2, label: "2x2" },
      { value: 3, label: "3x3" },
    ],
    "data-testid": "bin",
  } as any));
  const grp = () => q('[data-testid="bin"]');

  test("precondition: Segmented is one radiogroup, not three toggles", () => {
    eq(grp().getAttribute("role"), "radiogroup", "it is not a radiogroup");
    eq(grp().getAttribute("aria-label"), "Binning", "the group has no accessible name");
    eq(qa('[role="radio"]').length, 3, "not every option rendered");
    eq(qa('[role="radio"]')[0].getAttribute("aria-checked"), "true", "precondition: 1x1 is selected");
    eq(qa('[role="radio"]')[0].getAttribute("tabindex"), "0", "the selected option is not the tab stop");
    eq(qa('[role="radio"]')[1].getAttribute("tabindex"), "-1",
      "every option is a tab stop - one setting should cost one tab, not three");
  });

  test("Segmented picks by click", () => {
    click(qa('[role="radio"]')[1]);
    eq(picks.length, 1, "the click never reached onChange");
    eq(picks[0], 2, "the click picked the wrong option");
  });

  test("Segmented picks by ArrowRight", () => {
    keydown(grp(), "ArrowRight");
    eq(picks.length, 2, "ArrowRight did nothing - the control is keyboard-dead");
    eq(picks[1], 2, "ArrowRight moved to the wrong option");
  });
}

// =============================================================== ReadoutTile

{
  let selects = 0;
  render(createElement(ReadoutGrid, { cols: 4, "data-testid": "grid" } as any,
    createElement(ReadoutTile, {
      key: "sp", label: "SETPOINT", value: "-10C", sub: "cooler target",
      selected: true, onSelect: () => { selects++; }, "data-testid": "t-sp",
    } as any),
    createElement(ReadoutTile, {
      key: "gain", label: "GAIN", value: "100", sub: "unity",
      selected: false, onSelect: () => { selects++; }, "data-testid": "t-gain",
    } as any),
  ));

  test("precondition: the grid rendered both tiles at 4 columns", () => {
    eq(q('[data-testid="grid"]').getAttribute("data-cols"), "4", "the column count did not reach the DOM");
    eq(qa(".nx-readout").length, 2, "not every tile rendered");
  });

  test("ReadoutTile: the SELECTED tile carries a marker element, not only a border colour", () => {
    const sel = q('[data-testid="t-sp"]');
    const other = q('[data-testid="t-gain"]');
    eq(sel.getAttribute("data-selected"), "true", "the selected tile is not marked selected");
    eq(sel.getAttribute("aria-pressed"), "true", "selection never reaches a screen reader");
    assert(sel.querySelector(".nx-readout-mark") != null,
      "no marker on the selected tile: an accent BORDER is a hue-only signal and " +
      "night mode paints every token the same red");
    assert(other.querySelector(".nx-readout-mark") == null,
      "the unselected tile carries the marker too, so the marker says nothing");
  });

  test("ReadoutTile: selecting one calls onSelect", () => {
    click(q('[data-testid="t-gain"]'));
    eq(selects, 1, "tapping a readout did not point the dial at it");
  });
}

// ==================================================================== Sheet

{
  let backs = 0;
  render(createElement(Sheet, {
    title: "CAMERA", sub: "cooled - -10.0C - 62% power",
    onBack: () => { backs++; }, backLabel: "RIG",
    "data-testid": "sheet",
  } as any, createElement("p", { "data-testid": "body" }, "sheet body")));

  test("Sheet renders its title and body", () => {
    eq(q(".nx-sheet-title").textContent, "CAMERA", "the sheet title is wrong or missing");
    assert(/cooled/.test(q(".nx-sheet-sub").textContent), "the live/sub line never rendered");
    assert(q('[data-testid="body"]') != null, "the sheet swallowed its children");
  });

  test("Sheet: BACK calls onBack", () => {
    const back = q(".nx-sheet-back");
    assert(back != null, "no BACK control - the sheet is a one-way door");
    assert(/RIG/.test(back.textContent), "BACK does not name where it goes");
    click(back);
    eq(backs, 1, "BACK did nothing - the shell can never pop the sheet");
  });
}

// ================================================================== Popover

{
  let closes = 0;
  function PopHarness({ open }: { open: boolean }) {
    const anchor = useRef<any>(null);
    return createElement("div", null,
      createElement("button", { ref: anchor, "data-testid": "anchor" }, "LAYERS"),
      createElement(Popover, {
        open, anchorRef: anchor, onClose: () => { closes++; },
        "data-testid": "pop",
      } as any, createElement("div", { "data-testid": "pop-body" }, "Cloud deck")),
    );
  }
  render(createElement(PopHarness, { open: true }));
  const panel = () => win.document.querySelector('[data-testid="pop"]');

  test("precondition: the Popover portals OUT of the hub and into its own root", () => {
    assert(panel() != null, "the popover did not render");
    assert(win.document.getElementById("nx-popover-root") != null,
      "no #nx-popover-root - it was supposed to make its own host lazily");
    assert(container.querySelector('[data-testid="pop"]') == null,
      "the popover rendered inside the hub, where an overflow:auto ancestor will clip it");
  });

  test("Popover: a tap INSIDE it does not close it", () => {
    act(() => {
      panel().dispatchEvent(new win.Event("pointerdown", { bubbles: true }));
    });
    eq(closes, 0, "tapping the popover's own body dismissed it");
  });

  test("Popover closes on an outside tap", () => {
    act(() => {
      win.document.body.dispatchEvent(new win.Event("pointerdown", { bubbles: true }));
    });
    eq(closes, 1, "an outside tap did not dismiss the popover");
  });

  test("Popover closes on Escape", () => {
    act(() => {
      win.document.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    });
    eq(closes, 2, "Escape did not dismiss the popover");
  });

  test("Popover: closed means gone, not hidden", () => {
    render(createElement(PopHarness, { open: false }));
    assert(win.document.querySelector('[data-testid="pop"]') == null,
      "a closed popover is still in the DOM, so it is still in the tab order");
  });
}

// ============================================================= IncidentCard

{
  const acted: string[] = [];
  const explained: string[] = [];
  const LOCK = "needs operator or admin access";
  const incident = {
    kind: "cloud",
    pill: "HOLDING",
    color: "#ffb454",
    title: "CLOUD HOLD",
    sinceMs: Date.now() - 8 * 60_000,
    resolvesItself: true,
    engine: "Capture paused at the frame boundary. Guiding parked, mount tracking.",
    next: "Resumes after 3 clear test frames.",
    actions: [
      { id: "wait", label: "WAIT", primary: true },
      { id: "ignore", label: "IGNORE WEATHER TONIGHT" },
      { id: "switch", label: "SWITCH TARGET" },
    ],
  };
  render(createElement(IncidentCard, {
    incident,
    onAction: (id: string) => { acted.push(id); },
    lockedFor: (id: string) => (id === "switch" ? LOCK : null),
    onExplain: (r: string) => { explained.push(r); },
    "data-testid": "inc",
  } as any));

  test("IncidentCard renders the ENGINE and NEXT rows", () => {
    const keys = qa(".nx-incident-key").map((k: any) => k.textContent);
    assert(keys.includes("ENGINE"), "no ENGINE row - the card never says what the rig is doing");
    assert(keys.includes("NEXT"), "no NEXT row - the card never says what happens on its own");
    assert(/frame boundary/.test(container.textContent), "the ENGINE sentence never rendered");
    assert(/3 clear test frames/.test(container.textContent), "the NEXT sentence never rendered");
  });

  test("IncidentCard: the since line says how long and WHO has to act", () => {
    const since = q(".nx-incident-since").textContent as string;
    assert(/8 min/.test(since), `the elapsed time is wrong or missing (${since})`);
    assert(/resolves itself if it can/.test(since),
      "the line never says whether the user has to do anything - the one thing it is for");
  });

  test("IncidentCard fires onAction with the action id", () => {
    click(q('[data-action="ignore"]'));
    eq(acted.length, 1, "the action button did nothing");
    eq(acted[0], "ignore", "the wrong action id reached the caller");
  });

  test("IncidentCard: an action the role cannot take is honest-disabled, not hidden", () => {
    const locked = q('[data-action="switch"]');
    assert(locked != null,
      "the blocked action VANISHED - a viewer must see the same card an operator does");
    eq(locked.getAttribute("aria-disabled"), "true", "the blocked action is not marked disabled");
    click(locked);
    eq(acted.length, 1, "a blocked action ran anyway");
    eq(explained[0], LOCK, "the blocked press did not state its reason");
  });
}

// ====================================================================== Bar

{
  render(createElement(Bar, {
    height: 14,
    label: "Integration",
    segments: [
      { frac: 12, fill: 0.25, color: "#e8ecf7", label: "L 3/12" },
      { frac: 2, fill: 0.5, color: "#ff5470", label: "R 1/2" },
      { frac: 2, fill: 0, color: "#3ddc97", label: "G 0/2" },
    ],
    "data-testid": "integ",
  } as any));

  test("Bar segmented: segment WIDTHS are the planned share, not equal thirds", () => {
    const segs = qa(".nx-bar-seg");
    eq(segs.length, 3, "not every segment rendered");
    eq(segs[0].style.flexGrow, "12", "the L segment is not 12 units wide");
    eq(segs[1].style.flexGrow, "2", "the R segment is not 2 units wide");
    eq(segs[2].style.flexGrow, "2", "the G segment is not 2 units wide");
  });

  test("Bar segmented: fill is per-segment progress", () => {
    const fills = qa(".nx-bar-seg-fill");
    eq(fills[0].style.width, "25%", "the L segment's fill is wrong");
    eq(fills[1].style.width, "50%", "the R segment's fill is wrong");
    eq(fills[2].style.width, "0%", "an unstarted filter is not empty");
  });

  test("Bar segmented: the legend names every filter", () => {
    const legend = qa(".nx-bar-legend-item").map((l: any) => l.textContent);
    assert(legend.some((t: string) => /L 3\/12/.test(t)), "the legend does not carry the counts");
    eq(legend.length, 3, "the legend lost a filter");
  });

  test("Bar plain: it is a progressbar with a real value", () => {
    render(createElement(Bar, { value: 0.4, height: 3, label: "Sub progress", "data-testid": "sub" } as any));
    const bar = q('[data-testid="sub"]');
    eq(bar.getAttribute("role"), "progressbar", "the plain bar is not a progressbar");
    eq(bar.getAttribute("aria-valuenow"), "40", "the value never reached aria");
    eq(q(".nx-bar-fill").style.width, "40%", "the fill width does not match the value");
  });
}

act(() => { root.unmount(); });

const total = passed + failed;
console.log(`primitivesDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
