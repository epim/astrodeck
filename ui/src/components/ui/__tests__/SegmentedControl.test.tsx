// SegmentedControl.test.tsx — a11y + behaviour regression for the tri-state
// segmented pill (mount tracking-rate spec, Task 5). Same dependency-free
// inline-assert harness as the other UI suites (no vitest/jsdom wired in — these
// run under `tsx` and compile under `tsc -b`):
//
//   Run directly:  npx tsx src/components/ui/__tests__/SegmentedControl.test.tsx
//
// The component is a PURE (hook-free) function of its props, which lets us test it
// two complementary ways with zero DOM deps:
//   1. renderToStaticMarkup — asserts the REAL serialized HTML carries the
//      radiogroup/radio roles, aria-checked, aria-label and the disabled attr.
//   2. direct element-tree walk — invokes each radio's onClick / onKeyDown with a
//      synthetic event to assert onChange fires (or is blocked when disabled), so
//      the click + keyboard paths are exercised without a browser.

import { createElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { SegmentedControl, segmentedNextIndex, type SegmentedControlProps } from "../SegmentedControl";

// ------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`);
}

// Recursively collect elements whose props.role === role from a rendered tree.
type AnyEl = ReactElement<Record<string, unknown>>;
function isElement(n: unknown): n is AnyEl {
  return typeof n === "object" && n !== null && "props" in (n as object);
}
function collectByRole(node: ReactNode, role: string, out: AnyEl[] = []): AnyEl[] {
  if (Array.isArray(node)) { node.forEach((c) => collectByRole(c, role, out)); return out; }
  if (!isElement(node)) return out;
  if ((node.props as { role?: string }).role === role) out.push(node);
  const kids = (node.props as { children?: ReactNode }).children;
  if (kids != null) collectByRole(kids, role, out);
  return out;
}

const OPTIONS = [
  { value: "sidereal", label: "Sidereal" },
  { value: "lunar", label: "Lunar" },
  { value: "solar", label: "Solar" },
] as const;
type Rate = (typeof OPTIONS)[number]["value"];

// A synthetic keyboard/mouse event good enough for the handlers (they only touch
// key / preventDefault / currentTarget.closest — the last is guarded so a null is
// fine in a no-DOM run).
function ev(key = ""): { key: string; preventDefault(): void; currentTarget: { closest(): null } } {
  return { key, preventDefault() {}, currentTarget: { closest: () => null } };
}

// ------------------------------------------------------------ render / roles
test("renders one radio per option inside a labelled radiogroup", () => {
  const html = renderToStaticMarkup(
    createElement(SegmentedControl<Rate>, {
      options: OPTIONS as unknown as { value: Rate; label: string }[],
      value: "sidereal", onChange: () => {}, ariaLabel: "Tracking rate",
    }),
  );
  assert(/role="radiogroup"/.test(html), "no radiogroup role in markup");
  assert(/aria-label="Tracking rate"/.test(html), "group aria-label missing");
  eq((html.match(/role="radio"/g) ?? []).length, 3, "expected 3 radios");
  ["Sidereal", "Lunar", "Solar"].forEach((l) => assert(html.includes(l), `label ${l} missing`));
});

test("active option reflects `value` via aria-checked (XOR — exactly one)", () => {
  const el = SegmentedControl<Rate>({
    options: OPTIONS as unknown as { value: Rate; label: string }[],
    value: "lunar", onChange: () => {}, ariaLabel: "Tracking rate",
  });
  const radios = collectByRole(el, "radio");
  eq(radios.length, 3, "expected 3 radios in tree");
  const checked = radios.filter((r) => r.props["aria-checked"] === true);
  eq(checked.length, 1, "exactly one radio should be checked");
  eq(checked[0].props.children as unknown, "Lunar", "the checked radio is the value");
});

test("no selection when value is undefined (nothing checked)", () => {
  const el = SegmentedControl<Rate>({
    options: OPTIONS as unknown as { value: Rate; label: string }[],
    value: undefined, onChange: () => {}, ariaLabel: "Tracking rate",
  });
  const checked = collectByRole(el, "radio").filter((r) => r.props["aria-checked"] === true);
  eq(checked.length, 0, "no radio should be checked when value is undefined");
});

// ------------------------------------------------------------ click → onChange
test("clicking a segment calls onChange with its value", () => {
  const calls: Rate[] = [];
  const el = SegmentedControl<Rate>({
    options: OPTIONS as unknown as { value: Rate; label: string }[],
    value: "sidereal", onChange: (v) => calls.push(v), ariaLabel: "Tracking rate",
  });
  const radios = collectByRole(el, "radio");
  (radios[1].props.onClick as () => void)();
  eq(calls.length, 1, "onChange fired once");
  eq(calls[0], "lunar", "onChange got the clicked value");
});

test("disabled blocks onClick → onChange and marks the buttons disabled", () => {
  const calls: Rate[] = [];
  const props: SegmentedControlProps<Rate> = {
    options: OPTIONS as unknown as { value: Rate; label: string }[],
    value: "sidereal", onChange: (v: Rate) => { calls.push(v); }, ariaLabel: "Tracking rate", disabled: true,
  };
  const el = SegmentedControl<Rate>(props);
  const radios = collectByRole(el, "radio");
  (radios[2].props.onClick as () => void)();
  eq(calls.length, 0, "onChange must not fire when disabled");
  assert(radios.every((r) => r.props.disabled === true), "every radio should carry disabled");
  const html = renderToStaticMarkup(createElement(SegmentedControl<Rate>, props));
  assert(html.includes("disabled"), "markup should carry the disabled attribute");
});

// ------------------------------------------------------------ keyboard
test("ArrowRight moves+selects next, wrapping past the end", () => {
  const calls: Rate[] = [];
  const el = SegmentedControl<Rate>({
    options: OPTIONS as unknown as { value: Rate; label: string }[],
    value: "solar", onChange: (v) => calls.push(v), ariaLabel: "Tracking rate",
  });
  const radios = collectByRole(el, "radio");
  // solar is index 2 (last); ArrowRight wraps to index 0 = sidereal.
  (radios[2].props.onKeyDown as (e: unknown) => void)(ev("ArrowRight"));
  eq(calls[0], "sidereal", "ArrowRight from last wraps to first");
});

test("ArrowLeft moves+selects previous, wrapping before the start", () => {
  const calls: Rate[] = [];
  const el = SegmentedControl<Rate>({
    options: OPTIONS as unknown as { value: Rate; label: string }[],
    value: "sidereal", onChange: (v) => calls.push(v), ariaLabel: "Tracking rate",
  });
  const radios = collectByRole(el, "radio");
  (radios[0].props.onKeyDown as (e: unknown) => void)(ev("ArrowLeft"));
  eq(calls[0], "solar", "ArrowLeft from first wraps to last");
});

test("Enter/Space selects the focused option", () => {
  const calls: Rate[] = [];
  const el = SegmentedControl<Rate>({
    options: OPTIONS as unknown as { value: Rate; label: string }[],
    value: undefined, onChange: (v) => calls.push(v), ariaLabel: "Tracking rate",
  });
  const radios = collectByRole(el, "radio");
  (radios[1].props.onKeyDown as (e: unknown) => void)(ev("Enter"));
  (radios[2].props.onKeyDown as (e: unknown) => void)(ev(" "));
  eq(calls[0], "lunar", "Enter selects the focused option");
  eq(calls[1], "solar", "Space selects the focused option");
});

test("keyboard is inert when disabled", () => {
  const calls: Rate[] = [];
  const el = SegmentedControl<Rate>({
    options: OPTIONS as unknown as { value: Rate; label: string }[],
    value: "sidereal", onChange: (v) => calls.push(v), ariaLabel: "Tracking rate", disabled: true,
  });
  const radios = collectByRole(el, "radio");
  (radios[0].props.onKeyDown as (e: unknown) => void)(ev("ArrowRight"));
  (radios[0].props.onKeyDown as (e: unknown) => void)(ev("Enter"));
  eq(calls.length, 0, "no selection changes while disabled");
});

// ------------------------------------------------------------ pure nav helper
test("segmentedNextIndex maps navigation keys (wrap + Home/End)", () => {
  eq(segmentedNextIndex("ArrowRight", 0, 3), 1, "right");
  eq(segmentedNextIndex("ArrowDown", 2, 3), 0, "down wraps");
  eq(segmentedNextIndex("ArrowLeft", 0, 3), 2, "left wraps");
  eq(segmentedNextIndex("ArrowUp", 1, 3), 0, "up");
  eq(segmentedNextIndex("Home", 2, 3), 0, "home");
  eq(segmentedNextIndex("End", 0, 3), 2, "end");
  eq(segmentedNextIndex("Enter", 1, 3), null, "enter is not navigation");
  eq(segmentedNextIndex("a", 1, 3), null, "other keys are inert");
});

// ------------------------------------------------------------ report
const total = passed + failed;
console.log(`\nSegmentedControl.test: ${passed}/${total} passed`);
if (failures.length) { console.error(failures.join("\n")); (globalThis as unknown as { process?: { exit(c: number): void } }).process?.exit(1); }
export const result = { passed, failed, total };
