// slewPad.test.tsx — the four buttons that can leave the mount slewing.
//
//   Run directly:  npx tsx src/components/__tests__/slewPad.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// lib/__tests__/slewController.test.ts already drives the controller class hard,
// and it cannot see the defect this file exists for, because that defect is not
// in the controller. It is in the REACT WIRING between a finger and it:
//
//   `Arrow` was declared INSIDE SlewPad's body, so every render handed React a
//   brand-new component TYPE for the same four slots. A new type is not a
//   re-render, it is an unmount plus a remount: React destroys the <button> the
//   thumb is currently on and builds a fresh one in its place. Destroying the
//   element implicitly releases the pointer capture taken in onPointerDown —
//   and capture is the ONLY thing binding the slew to the finger, because this
//   pad deliberately has no onPointerLeave (R3, see the file header). Pressing
//   an arrow re-renders immediately (`activeKey` derives from slewState.mode),
//   and telemetry re-renders land every 2s, so the capture was gone within a
//   frame of the press. Slide a thumb off the 56px button after that and
//   pointerup fires on a node nobody is listening to: ctrl.endPress never runs,
//   the 600ms keepalive keeps re-asserting the rate under the server's 1200ms
//   move-axis deadman so the deadman never trips, and THE MOUNT KEEPS SLEWING.
//   onLostPointerCapture, the net under all of that, was registered on a node
//   React had already removed. activePointerId also stayed set, so afterwards
//   every arrow refused every press until STOP was used.
//
// There is no jsdom in this UI, so nothing below mounts anything or dispatches
// a real pointer event, and nothing below asserts a pixel. It does not need to:
// the hazard is entirely a question of WHAT ELEMENT TYPE React is handed at
// those four slots, and that is visible in the element tree the component
// returns. A host tag ("button") cannot be remounted by a re-render — that is
// the whole fix, and it is what the first two tests pin. The tree is taken from
// the REAL component running under react-dom/server's dispatcher (real
// useState/useRef/useMemo), never from a re-description of it here.
//
// The globals below are stubs installed before the store is imported, because
// the API client reads `window.location` at module scope. run-tests.mjs gives
// this file its own process, which is why that is safe (same idiom as
// overridePanels.test.tsx).

/* eslint-disable @typescript-eslint/no-explicit-any */
import type { ReactElement, ReactNode } from "react";

const g = globalThis as any;
g.window = {
  location: { pathname: "/", href: "http://local/", protocol: "http:", host: "local" },
  addEventListener() {},
  removeEventListener() {},
  matchMedia: () => ({
    matches: false, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {},
  }),
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  setTimeout, clearTimeout,
};
g.localStorage = g.window.localStorage;
g.document = {
  documentElement: {
    dataset: {}, style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {} },
  },
  addEventListener() {}, removeEventListener() {}, body: {},
};
g.WebSocket = class { close() {} addEventListener() {} send() {} };

const { createElement } = await import("react");
const { renderToStaticMarkup } = await import("react-dom/server");
const { useStore } = await import("../../store");
const SlewPad = (await import("../SlewPad")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

type AnyEl = ReactElement<Record<string, any>>;
function isElement(n: unknown): n is AnyEl {
  return typeof n === "object" && n !== null && "type" in (n as object) && "props" in (n as object);
}
/** Depth-first over the element tree the component RETURNED — component
 *  children are not expanded (they were never rendered), which is exactly the
 *  boundary that matters here: a slot filled by a component is the bug. */
function walk(node: ReactNode, visit: (el: AnyEl) => void): void {
  if (Array.isArray(node)) { node.forEach((n) => walk(n, visit)); return; }
  if (!isElement(node)) return;
  visit(node);
  const kids = (node.props as { children?: ReactNode }).children;
  if (kids != null) walk(kids, visit);
}
function firstWhere(tree: AnyEl, pred: (el: AnyEl) => boolean): AnyEl | null {
  let hit: AnyEl | null = null;
  walk(tree, (el) => { if (!hit && pred(el)) hit = el; });
  return hit;
}
/** How a type reads in a failure message: "<button>" or the component's name. */
function typeName(t: unknown): string {
  if (typeof t === "string") return `<${t}>`;
  if (typeof t === "function") return `component ${(t as { name?: string }).name || "(anonymous)"}`;
  return String(t);
}

// ------------------------------------------------------------------ fixture
// A connected, unparked, well-above-the-horizon mount — the ONLY state in which
// the pad renders arrows at all. zustand feeds React's SERVER snapshot from
// getInitialState(), which returns the object captured when the store was
// created; setState replaces the state object and leaves that one behind, so a
// plain setState is invisible to renderToStaticMarkup. Mutate it in place.
Object.assign(useStore.getInitialState(), {
  status: {
    connected: {}, looping: false, mode: "sim",
    mount: {
      ra_hours: 5.6, dec_deg: -5.4, ra_str: "05h35m", dec_str: "-05°23'",
      alt: 45, az: 120, tracking: true, parked: false, slewing: false,
    },
  } as never,
  // control.mount, or the whole pad renders inert and there is nothing to test.
  principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] } as never,
});

/** SlewPad's own element tree, from a real render pass.
 *
 *  Probe calls the component the way React does — under a live hook dispatcher —
 *  and keeps the TREE instead of the markup. Each call is an independent render
 *  pass with its own hook state, which is precisely the comparison the first
 *  test needs: what React would be handed on one render versus the next. */
function renderTree(): AnyEl {
  const seen: AnyEl[] = [];
  function Probe() {
    seen.push(SlewPad() as AnyEl);
    return null;
  }
  renderToStaticMarkup(createElement(Probe));
  if (seen.length !== 1) throw new Error("the probe captured no tree — SlewPad rendered nothing");
  return seen[0];
}

/** The pad grid's four directional slots, in DOM order (N, W, E, S).
 *
 *  Found by structure rather than by aria-label on purpose: when the slot holds
 *  a COMPONENT the label lives inside a subtree that was never rendered, so a
 *  label-based finder would report "no arrows" for the very defect being
 *  hunted. The grid's children are the four arrows, four spacer <span>s and the
 *  rate radiogroup; strip those two and what remains is the arrows. */
function arrowSlots(tree: AnyEl): AnyEl[] {
  const grid = firstWhere(tree, (el) =>
    typeof el.type === "string" && String(el.props.className ?? "").includes("grid-cols-3"));
  if (!grid) throw new Error("no pad grid in the tree — the fixture is not rendering the pad");
  const kids: AnyEl[] = [];
  const children = (grid.props as { children?: ReactNode }).children;
  (Array.isArray(children) ? children : [children]).forEach((c) => {
    if (isElement(c)) kids.push(c);
  });
  return kids.filter((el) => el.type !== "span" && el.props.role !== "radiogroup");
}

// ============================================== the hazard: a stable element type
test("every slot in the pad keeps its element TYPE across renders", () => {
  // The general form of the defect, so an inline component added anywhere in
  // this file in future trips the same wire: two render passes, and every
  // element in DOM order must be the SAME type object. Host tags compare as
  // strings; a module-level component compares by identity; a component
  // declared in the render body is a fresh function every pass and cannot.
  const typesOf = (t: AnyEl): unknown[] => {
    const out: unknown[] = [];
    walk(t, (el) => out.push(el.type));
    return out;
  };
  const first = renderTree();
  eq(arrowSlots(first).length, 4, "the fixture must be rendering four arrows to prove anything");
  const a = typesOf(first);
  const b = typesOf(renderTree());
  eq(a.length, b.length, "the two render passes produced different tree shapes");
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) {
      throw new Error(
        `element ${i} is ${typeName(a[i])} on one render and ${typeName(b[i])} on the next — ` +
        "React unmounts and remounts a node whose type changed, which drops the " +
        "pointer capture holding the slew to the finger",
      );
    }
  }
});

test("each arrow IS a <button>, not a component that can be swapped under the thumb", () => {
  // The specific fix: the four slots hold host elements. A host tag cannot
  // change identity between renders, so React reuses the same DOM node, so the
  // capture taken in onPointerDown survives every telemetry tick for as long as
  // the finger is down.
  const slots = arrowSlots(renderTree());
  eq(slots.length, 4, "expected N, W, E and S in the grid");
  slots.forEach((el, i) => {
    assert(el.type === "button",
      `slew arrow ${i} is ${typeName(el.type)}, not a <button> — React can remount it mid-slew`);
  });
  eq(slots.map((el) => String(el.props["aria-label"])).join(" | "),
    "slew north | slew west | slew east | slew south",
    "the arrows moved or lost their names");
});

// ================================================== the contract on those nodes
test("every arrow carries the full capture contract and NO onPointerLeave", () => {
  // Straight from the file header: capture binds the slew to the finger, and
  // stop fires ONLY on pointerup / pointercancel / lostpointercapture. A slot
  // that dropped one of these — or that grew an onPointerLeave — is a different
  // safety model from the one the controller was written against.
  for (const el of arrowSlots(renderTree())) {
    const name = String(el.props["aria-label"]);
    for (const h of ["onPointerDown", "onPointerUp", "onPointerCancel", "onLostPointerCapture"]) {
      assert(typeof el.props[h] === "function", `${name} has no ${h}`);
    }
    assert(el.props.onPointerLeave === undefined,
      `${name} grew an onPointerLeave — R3: sliding off a held button must NOT stop the slew, ` +
      "capture is what ends it");
  }
});

test("pressing an arrow takes pointer capture on that same node", () => {
  // The press path end to end, on the element the tree actually contains: the
  // handler must reach the DOM node (e.currentTarget) and claim the pointer.
  const [north] = arrowSlots(renderTree());
  const captured: number[] = [];
  const released: number[] = [];
  const node = {
    setPointerCapture(id: number) { captured.push(id); },
    releasePointerCapture(id: number) { released.push(id); },
  };
  (north.props.onPointerDown as (e: unknown) => void)({ pointerId: 7, currentTarget: node });
  eq(captured.join(","), "7", "onPointerDown must capture the pointer that pressed it");
});

test("a second finger cannot steal the pad, and a cancel hands it back", () => {
  // F-A4: the controller is single-axis, so a second concurrent press would
  // overwrite the live axis and strand the first one until the deadman. This
  // also proves the four buttons share ONE activePointerId ref — i.e. they came
  // out of one render of one component, not four independent widgets.
  const slots = arrowSlots(renderTree());
  const north = slots[0];
  const south = slots[3];
  const captured: number[] = [];
  const node = {
    setPointerCapture(id: number) { captured.push(id); },
    releasePointerCapture() {},
  };
  const down = (el: AnyEl, id: number) =>
    (el.props.onPointerDown as (e: unknown) => void)({ pointerId: id, currentTarget: node });

  down(north, 1);
  down(south, 2);
  eq(captured.join(","), "1", "a second pointer must be ignored while one owns the pad");
  // pointercancel (the OS taking the gesture) releases ownership; forceStop is a
  // no-op here because the default GUIDE rate never started a hold.
  (north.props.onPointerCancel as (e: unknown) => void)({ pointerId: 1 });
  down(south, 2);
  eq(captured.join(","), "1,2", "after a cancel the pad must accept the next press");
});

// ============================================== the fixture is a real rendered pad
test("the fixture really does render the pad, not an empty page", () => {
  // Guard against the trap that has burnt this app before: a probe that renders
  // nothing, so every assertion above holds vacuously. This renders the whole
  // component the ordinary way and reads the markup a user would get.
  const html = renderToStaticMarkup(createElement(SlewPad));
  for (const name of ["slew north", "slew south", "slew east", "slew west"]) {
    assert(html.includes(`aria-label="${name}"`), `${name} is missing from the rendered pad`);
  }
  assert(html.includes("Stop all mount motion"), "the STOP bar is missing");
  assert(!html.includes("connect a mount to slew") && !html.includes("unpark to slew"),
    "the fixture fell through to a no-mount / parked state and tested nothing");
});

// ------------------------------------------------------------------ report
const total = passed + failed;
console.log(`\nslewPad.test: ${passed}/${total} passed`);
if (failures.length) {
  console.error(failures.join("\n"));
}
export const result = { passed, failed, total };
