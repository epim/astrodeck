// catalogSearchDismiss.test.ts — pins the Atlas catalog-search dropdown against
// the latch that has now shut on a phone user TWICE.
//
// Run directly:  npx tsx src/__tests__/catalogSearchDismiss.test.ts
//
// History. The dropdown may be dismissed without clearing the query, so it does
// not float over the page forever after the user looks away. UX-06 was the
// first time that dismissal fired when the user had NOT looked away: `onBlur`
// with a null relatedTarget (an Android soft-keyboard hide) latched it shut.
// The second time was the document-level `pointerdown` dismisser, and the cause
// is the same desktop assumption: a mouse only presses where it means to, but a
// touch screen dispatches `pointerdown` at the start of every finger gesture —
// including the swipe that scrolls the page. Reproduced on an s25ultra
// viewport: type "m31", M31 is listed; ONE outside scroll swipe and the
// dropdown is gone with the caret still in the field; re-tapping the input does
// not restore it; only editing the query does. Which is precisely the field
// report — "typed m31, nothing happened … deleted the 1 and it showed m31".
//
// So the dismissal predicate is the fix, and it is what is pinned here: a tap
// dismisses, a scroll does not. These are not style assertions — every case
// below is a gesture a phone user makes while reading their own search results.

// lib/base.ts (imported transitively via api.ts) reads window.location at
// module load, so stub the browser globals the module graph touches.
const g = globalThis as unknown as { window?: unknown };
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", host: "localhost", protocol: "http:" } };
}

const { outsideTapDismisses, TAP_SLOP_PX } =
  await import("../components/atlas/CatalogSearch");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

const at = (x: number, y: number) => ({ x, y });

// ====================================================================
// the regression: a scroll is not a dismissal
// ====================================================================

test("a scroll swipe that starts outside does NOT dismiss (the reported bug)", () => {
  // finger down on the page below the widget, dragged up to scroll
  assert(!outsideTapDismisses(at(206, 700), at(206, 480), true),
         "one page scroll latched the dropdown shut");
});

test("even a short flick past the slop is a scroll, not a tap", () => {
  assert(!outsideTapDismisses(at(206, 700), at(206, 700 - TAP_SLOP_PX - 1), true),
         "a gesture that travelled further than the tap slop is a drag");
});

test("a gesture the browser claimed for scrolling (pointercancel) does not dismiss", () => {
  // pointercancel clears the recorded down-point, so there is nothing to match
  assert(!outsideTapDismisses(null, at(206, 480), true),
         "a cancelled gesture must never dismiss");
});

// ====================================================================
// what dismissal still has to do (the reason the behaviour exists)
// ====================================================================

test("a genuine tap outside still dismisses", () => {
  assert(outsideTapDismisses(at(60, 760), at(61, 762), true),
         "tapping the page away from the widget must close the dropdown");
});

test("a perfectly still tap dismisses", () => {
  assert(outsideTapDismisses(at(60, 760), at(60, 760), true),
         "a mouse click travels zero pixels");
});

test("a tap exactly at the slop boundary is still a tap", () => {
  assert(outsideTapDismisses(at(60, 760), at(60, 760 + TAP_SLOP_PX), true),
         "a finger is never perfectly still; the boundary is inclusive");
});

// ====================================================================
// gestures that begin or end INSIDE the widget are the user working
// ====================================================================

test("a gesture that started inside the widget does not dismiss", () => {
  // e.g. scrolling the suggestion list itself, or dragging in the input
  assert(!outsideTapDismisses(null, at(200, 420), true),
         "the down-point was inside, so there is no outside tap to honour");
});

test("a drag that ends back inside the widget does not dismiss", () => {
  assert(!outsideTapDismisses(at(60, 760), at(200, 400), false),
         "releasing on the widget is reaching for it, not dismissing it");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ncatalogSearchDismiss.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export const result = { passed, failed, total };
