// fieldAnnotationDom.test.tsx — the target name and the frame markers, mounted.
//
//   Run directly:  npx tsx src/components/capture/__tests__/fieldAnnotationDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// `lib/fieldIdentity.test.ts` proves the STATES. This proves the two things a
// pure state machine cannot: that the proposal actually becomes a control the
// operator can press, and that the marker layer actually draws something.
//
// The second one is the lesson from #111, which was marked complete and rendered
// nothing for weeks because every test asserted about the data and none asserted
// about the picture. So the marker assertion here is deliberately blunt: empty
// the layer and the test must fail saying NOTHING WAS DRAWN.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "Image", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket", "ResizeObserver",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { TargetField } = await import("../TargetField");
const { FieldOverlay, frameMarks, frameLabelBudget } =
  await import("../../preview/FieldOverlay");
type PreviewField = import("../../../types").PreviewField;
type FieldObject = import("../../../types").FieldObject;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(el: any): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(el); });
}
function click(node: any): void {
  act(() => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
}

function obj(over: Partial<FieldObject> = {}): FieldObject {
  return {
    id: "M 27", label: "Dumbbell Nebula", kind: "dso",
    type: "Planetary Nebula", describe: "planetary nebula in Vulpecula",
    sep_arcmin: 2.1, confident: true, runner_up: null,
    x: 500, y: 400, size_px: 300, inside: true,
    mag: 7.4, size_arcmin: 8, constellation: "Vulpecula", alias: "NGC 6853",
    ...over,
  };
}

function solved(over: Partial<PreviewField> = {}): PreviewField {
  return {
    source: "solve",
    solved_at: Date.now() / 1000 - 20,
    id: {
      id: "M 27", label: "Dumbbell Nebula", kind: "dso",
      type: "Planetary Nebula",
      describe: "Dumbbell Nebula — planetary nebula in Vulpecula · mag 7.4",
      sep_arcmin: 2.1, confident: true, runner_up: null,
    },
    objects: [obj()],
    ...over,
  };
}

// ============================================================== the target field

test("the proposal is a real control, and pressing it adopts the name", () => {
  const seen: string[] = [];
  mount(createElement(TargetField, {
    value: "", onChange: (v: string) => seen.push(v),
    field: solved(), perFrameSolving: false, frameType: "Light",
  }));
  const btn = container.querySelector("[data-capture-adopt]");
  assert(!!btn, "the identified field offered nothing to press — the whole "
    + "feature is one tap and there was no tap target");
  assert(btn.getAttribute("data-capture-adopt") === "M 27", btn.outerHTML);
  click(btn);
  assert(seen.length === 1 && seen[0] === "M 27", JSON.stringify(seen));
});

test("the input's VALUE is still empty while a proposal is showing", () => {
  mount(createElement(TargetField, {
    value: "", onChange: () => {},
    field: solved(), perFrameSolving: true, frameType: "Light",
  }));
  const input = container.querySelector("[data-capture-field='target']");
  assert(input.value === "",
    `a derived name was pre-filled into the field (${input.value}). That value `
    + "names the folder and keys the persisted frame counter.");
  assert(input.getAttribute("placeholder") === "M 27",
    "the proposal should read as a placeholder, not as content");
});

test("a typed name is never replaced, and the disagreement stays on screen", () => {
  mount(createElement(TargetField, {
    value: "Veil east", onChange: () => {},
    field: solved(), perFrameSolving: true, frameType: "Light",
  }));
  const input = container.querySelector("[data-capture-field='target']");
  assert(input.value === "Veil east", input.value);
  assert(container.textContent.includes("M 27"),
    "the machine's answer disappeared instead of staying visible beside the "
    + "operator's — the disagreement must be legible, not silently resolved");
});

test("with nothing solved, the note names the missing thing and offers the fix", () => {
  let opened = 0;
  mount(createElement(TargetField, {
    value: "", onChange: () => {}, field: null,
    perFrameSolving: false, frameType: "Light",
    onOpenSolveSettings: () => { opened += 1; },
  }));
  assert(!container.querySelector("[data-capture-adopt]"),
    "there is nothing to adopt");
  const text = container.textContent as string;
  assert(/plate solv/i.test(text), text);
  const fix = [...container.querySelectorAll("button")]
    .find((b: any) => /turn on plate solving/i.test(b.textContent));
  assert(!!fix, "the actionable case offered no action");
  click(fix);
  assert(opened === 1, "the fix button did nothing");
});

test("a mount/plate disagreement is rendered, not just modelled", () => {
  mount(createElement(TargetField, {
    value: "", onChange: () => {},
    field: solved({ pointing_disagrees_deg: 4.2 }),
    perFrameSolving: true, frameType: "Light",
  }));
  assert((container.textContent as string).includes("4.2°"),
    `the condition that cost this rig a night was not on screen: ${container.textContent}`);
});

test("the OBJECT card the app will write is disclosed when it is not the box", () => {
  mount(createElement(TargetField, {
    value: "", onChange: () => {},
    field: solved(), perFrameSolving: true, frameType: "Light",
  }));
  const text = container.textContent as string;
  assert(/OBJECT = M 27/.test(text),
    `a header card nobody typed was going to be written silently: ${text}`);
  assert(/untargeted/.test(text),
    "and the folder rule has to be stated in the same breath, or the two look "
    + "like they must agree");
});

// ================================================================ the markers

test("an object in the frame is DRAWN", () => {
  mount(createElement("svg", {},
    createElement(FieldOverlay, {
      objects: [obj()], dispW: 1400, dispH: 1050,
      dataW: 4000, dataH: 3000, scale: 1,
    })));
  const marks = container.querySelectorAll("[data-field-object]");
  assert(marks.length === 1,
    `NOTHING WAS DRAWN — the marker layer rendered ${marks.length} objects for a `
    + "frame the server placed one in. That is the exact state #111 shipped in.");
  assert(marks[0].getAttribute("data-field-object") === "M 27",
    marks[0].outerHTML);
  assert(!!marks[0].querySelector("ellipse"),
    "an 8-arcminute nebula is an extent, not a dot");
});

test("nothing is drawn when the server placed nothing", () => {
  mount(createElement("svg", {},
    createElement(FieldOverlay, {
      objects: [], dispW: 1400, dispH: 1050, dataW: 4000, dataH: 3000, scale: 1,
    })));
  assert(container.querySelectorAll("[data-field-object]").length === 0,
    "an empty object list must draw an empty overlay, not a placeholder");
});

// ------------------------------------------------------------ the pure geometry

test("markers use SEPARATE x and y scales", () => {
  // The JPEG's height is int(h*scale), FLOORED, so dispH/dataH and dispW/dataW
  // are not the same number. StarOverlay uses one x-derived scale — fine for a
  // 4px ring, wrong for a galaxy outline, which is why this layer carries two.
  const [m] = frameMarks([obj({ x: 1000, y: 1000 })], {
    sx: 1400 / 4000, sy: 1049 / 3000, dispW: 1400, dispH: 1049, budget: 8,
  });
  assert(Math.abs(m.cx - 350) < 1e-9, `cx ${m.cx}`);
  assert(Math.abs(m.cy - 1049 / 3) < 1e-9, `cy ${m.cy}`);
  assert(m.ry !== m.r, "the two semi-axes collapsed to one — sx was used for both");
});

test("a point source gets the minimum glyph rather than a zero-radius nothing", () => {
  const [m] = frameMarks([obj({ size_px: 0 })], {
    sx: 0.35, sy: 0.35, dispW: 1400, dispH: 1050, budget: 8,
  });
  assert(!m.extended, "a point source is not an extent");
  assert(m.r >= 7, `radius ${m.r} is invisible and untappable`);
});

test("labels are budgeted but MARKERS ARE NOT", () => {
  // Losing a label is a readability decision. Losing a marker is losing the
  // information, and there is no way for the user to ask for it back.
  const rows = Array.from({ length: 40 }, (_, i) =>
    obj({ id: `NGC ${i}`, label: `NGC ${i}`, x: 500, y: 400 + i * 2, size_px: 0 }));
  const marks = frameMarks(rows, {
    sx: 0.35, sy: 0.35, dispW: 800, dispH: 600, budget: frameLabelBudget(800),
  });
  assert(marks.length === 40, `${marks.length} markers for 40 objects`);
  const labelled = marks.filter((m) => m.labelled).length;
  assert(labelled <= frameLabelBudget(800),
    `${labelled} labels over a budget of ${frameLabelBudget(800)}`);
  assert(labelled >= 1, "every label was suppressed");
});

test("two objects at the same point get one label and two markers", () => {
  const marks = frameMarks(
    [obj({ id: "A", label: "A", size_px: 0 }), obj({ id: "B", label: "B", size_px: 0 })],
    { sx: 0.35, sy: 0.35, dispW: 1400, dispH: 1050, budget: 8 },
  );
  assert(marks.length === 2, "a collision must not delete an object");
  assert(marks.filter((m) => m.labelled).length === 1,
    "two labels were stacked on one point");
});

test("the label budget scales with the stage and stays inside its bounds", () => {
  assert(frameLabelBudget(0) === 4, String(frameLabelBudget(0)));
  assert(frameLabelBudget(320) === 4, String(frameLabelBudget(320)));
  assert(frameLabelBudget(800) === 10, String(frameLabelBudget(800)));
  assert(frameLabelBudget(4000) === 12, String(frameLabelBudget(4000)));
});

const total = passed + failed;
console.log(`fieldAnnotationDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
export default result;
