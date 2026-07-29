// catalogSearchDropdown.test.ts — pins the suggestion list inside the screen.
//
// Run directly:  npx tsx src/__tests__/catalogSearchDropdown.test.ts
//
// UX-2026-07-28 S5. The suggestion list is anchored to the LEFT edge of the
// search field, and is wider than that field (the field is 224px in a toolbar;
// every suggestion row carries a name AND the altitude badge you actually
// choose by). Measured in the Plan on an 820px tablet: field at x=563, list
// 288px wide, so the list ran to 851 and the badge column to 838 — 31px and
// 18px past the screen. Nothing scrolls sideways there and the page clips, so
// those pixels were UNREACHABLE, which is what makes a clipped badge a defect
// rather than an untidiness.
//
// The cases that matter most below are the ones that must NOT move: a list that
// already fits, shifted "just in case", would be dragged off the opposite edge —
// and a widget close to the left edge can only give back what it has.

const g = globalThis as unknown as { window?: unknown };
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", host: "localhost", protocol: "http:" } };
}

const { dropdownShiftPx, DROPDOWN_EDGE_MARGIN_PX } =
  await import("../components/atlas/CatalogSearch");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq(got: number, want: number, msg: string): void {
  if (got !== want) throw new Error(`${msg} — expected ${want}, got ${got}`);
}

// ====================================================================
// the regression, with the measured numbers
// ====================================================================

test("Plan search on an 820px tablet: the list moves back on screen", () => {
  // field x=563, list 288 wide -> right edge 851 on an 820 screen
  const shift = dropdownShiftPx(563, 288, 820);
  eq(shift, 851 + DROPDOWN_EDGE_MARGIN_PX - 820, "shift clears the overhang plus the edge margin");
  assert(563 - shift + 288 <= 820 - DROPDOWN_EDGE_MARGIN_PX,
         "after the shift the whole list, badges included, is on screen");
});

// ====================================================================
// layouts that already fit must not be touched
// ====================================================================

test("Atlas header on a 390px phone does not move (it already fits)", () => {
  eq(dropdownShiftPx(29, 288, 390), 0, "a list that fits must stay where it is");
});

test("Plan search on a 390px phone does not move", () => {
  eq(dropdownShiftPx(33, 288, 390), 0, "a list that fits must stay where it is");
});

test("the full-width empty-state card does not move", () => {
  // anchor and list are the same box there
  eq(dropdownShiftPx(32, 326, 390), 0, "a full-width list is already inside the padding");
});

test("landing exactly on the margin is not an overhang", () => {
  eq(dropdownShiftPx(100, 288, 100 + 288 + DROPDOWN_EDGE_MARGIN_PX), 0,
     "the margin is the allowance, not a trigger");
});

// ====================================================================
// it can only give back what it has
// ====================================================================

test("a list wider than the screen shifts to the left margin and no further", () => {
  // Defensive: the CSS caps the list at 100vw-2rem, so this should not arise —
  // but if it ever does, the shift stops at the left margin instead of trading
  // the right edge for the left. 320px screen, anchor at 20, 320px list.
  eq(dropdownShiftPx(20, 320, 320), 12, "cannot shift past the left edge margin");
});

test("an anchor already at the left edge never shifts", () => {
  eq(dropdownShiftPx(0, 400, 320), 0, "shifting would only trade one clipped edge for the other");
});

test("negative and zero widths are inert", () => {
  eq(dropdownShiftPx(563, 0, 820), 0, "no box, no overhang");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncatalogSearchDropdown.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export const result = { passed, failed, total };
