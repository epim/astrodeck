// overlay.test.ts — regression guard for the ONE overlay primitive and the two
// CSS rules that made every overlay in the app land off the viewport.
//
// Run directly:  npx tsx src/__tests__/overlay.test.ts
//
// These are not presentational assertions. Each one pins a property that was
// MEASURED broken in the four-persona review and whose failure mode is silent —
// nothing throws, nothing logs, the dialog just renders somewhere the user
// cannot reach it:
//
//   * an entry animation left in `animation-fill-mode: both` keeps an identity
//     matrix() transform forever, which makes its element the containing block
//     for every `position: fixed` descendant. That is what put the preflight's
//     `fixed inset-0` at x=-80/right=620 on an 820px tablet.
//   * a translucent overlay surface lets the layer underneath read through it
//     (event-log lines through MORE rows; AUTOMATION toggles through frame
//     metadata).
//   * the overlay surface's size must come from CSS custom properties, because
//     index.css is unlayered and its authored `max-width`/`max-height` beat any
//     Tailwind utility on the same element. Asserting the vars are present in
//     BOTH the CSS and the geometry resolver keeps those two halves in sync.

interface NodeFsLike { readFileSync(path: string, encoding: string): string }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;

const resolve = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");
const css = fs.readFileSync(resolve("../index.css"), "utf8");

const { overlayGeometry } = await import("../components/Overlay");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`✗ ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** Every declaration block whose selector list mentions `selector`, concatenated.
 *  A class can legitimately appear in more than one rule (the base rule plus a
 *  reduced-motion or @supports override), so matching only the FIRST one reads
 *  the wrong rule — which is exactly how this helper failed on its first run. */
function block(selector: string): string {
  const re = new RegExp(`\\${selector}(?![\\w-])[^{}]*\\{([^}]*)\\}`, "g");
  const parts = [...css.matchAll(re)].map((m) => m[1]);
  assert(parts.length > 0, `no CSS rule for ${selector}`);
  return parts.join("\n");
}
/** One declaration's value out of a block, e.g. `background`. */
function decl(blockText: string, prop: string): string {
  const m = new RegExp(`(?:^|;|\\n)\\s*${prop}\\s*:([^;}]*)`).exec(blockText);
  assert(!!m, `no \`${prop}\` declaration`);
  return m![1].trim();
}

// ------------------------------------------------- the containing-block trap
test("entry animations never use fill-mode `both` (identity-matrix trap)", () => {
  for (const cls of ["view-enter", "sheet-enter", "more-sheet-in"]) {
    const decl = block(`.${cls}`);
    assert(/animation:/.test(decl), `.${cls} has no animation`);
    assert(!/\bboth\b/.test(decl),
      `.${cls} uses animation-fill-mode: both — a filled identity transform makes ` +
      `the element a containing block for every fixed descendant (review S1/#3)`);
    assert(/\bbackwards\b/.test(decl), `.${cls} should fill \`backwards\``);
  }
});

// -------------------------------------------------------- the host contract
test("the overlay host is viewport-sized, dimmed, and click-through", () => {
  const b = block(".overlay-host");
  assert(/position:\s*fixed/.test(b), "host must be position: fixed");
  assert(/inset:\s*0/.test(b), "host must be inset: 0 — its padding box IS the viewport");
  assert(/pointer-events:\s*none/.test(b), "host must not eat taps outside its children");
  assert(/filter:\s*brightness\(var\(--screen-brightness/.test(b),
    "host must carry the dimmer — portalling out of .dim-content must not un-dim a dialog at 2am");
});

test("the overlay surface is OPAQUE and clamped to the viewport", () => {
  const b = block(".overlay-surface");
  const bg = decl(b, "background");
  assert(bg === "var(--bg-raise)",
    `surface must use the OPAQUE --bg-raise, never the translucent --bg-panel (#38/#44) — got ${bg}`);
  assert(!/rgba|\/\s*0?\.\d/.test(bg), "surface background must have no alpha");
  assert(/max-height:\s*min\(100dvh,\s*var\(--ov-max-h/.test(b),
    "surface height must be clamped to the viewport AND driven by --ov-max-h");
  assert(/max-width:\s*min\(100vw,\s*var\(--ov-max-w/.test(b),
    "surface width must be clamped to the viewport AND driven by --ov-max-w");
  assert(/flex-direction:\s*column/.test(b), "surface is head / body / foot");
});

test("the footer sits outside the scroll region and clears the home indicator", () => {
  const body = block(".overlay-body");
  assert(/overflow-y:\s*auto/.test(body), "the BODY is the only scroll region");
  const foot = block(".overlay-foot");
  assert(/flex:\s*none/.test(foot),
    "the footer must not scroll — that is what keeps CANCEL / RUN SEQUENCE reachable");
  assert(/env\(safe-area-inset-bottom/.test(foot), "footer must clear the home indicator (#46)");
});

test("safe-area insets exist for the bottom nav and page padding (#46)", () => {
  assert(/nav\.fixed\[aria-label="Primary"\][^{]*\{[^}]*env\(safe-area-inset-bottom/.test(css),
    "the fixed bottom nav has no safe-area padding");
  assert(/env\(safe-area-inset-bottom/.test(block(".main-safe-pad")),
    "<main>'s bottom padding must clear the nav AND the inset");
  assert(/env\(safe-area-inset-bottom/.test(block(".float-safe-b")),
    "bottom-floating controls must clear the inset");
});

test("the header icon cluster is one component (#47)", () => {
  const m = /\.app-header\s+\.btn,\s*\n?\s*\.app-header\s+\.step-btn\s*\{([^}]*)\}/.exec(css);
  assert(!!m, "no .app-header icon-set rule");
  assert(/min-width:\s*44px/.test(m![1]) && /min-height:\s*44px/.test(m![1]),
    "header icon buttons must meet the 44px touch minimum");
  assert(/border-radius:\s*10px/.test(m![1]), "header icon buttons must share one radius");
});

// ------------------------------------------------------- the size resolver
test("every variant positions itself inside the host, never in page flow", () => {
  for (const v of ["center", "sheet", "dock", "corner"] as const) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const g = overlayGeometry(v, lg, sm);
        assert(/\babsolute\b/.test(g.wrap),
          `${v} (lg=${lg} sm=${sm}) wrapper is not absolutely positioned inside the host`);
        assert(!/\bfixed\b/.test(g.wrap),
          `${v} wrapper must not re-introduce position: fixed — the host already is`);
      }
    }
  }
});

test("no variant asks for a surface bigger than the viewport", () => {
  const pct = (s: string | undefined): number | null => {
    if (!s) return null;
    const m = /^(\d+(?:\.\d+)?)(dvh|vh|vw)$/.exec(s);
    return m ? parseFloat(m[1]) : null;
  };
  for (const v of ["center", "sheet", "dock", "corner"] as const) {
    for (const lg of [false, true]) {
      for (const sm of [false, true]) {
        const { vars } = overlayGeometry(v, lg, sm);
        for (const key of ["--ov-max-h", "--ov-max-w", "--ov-w", "--ov-h"]) {
          const p = pct(vars[key]);
          assert(p === null || p <= 100,
            `${v} (lg=${lg} sm=${sm}) ${key}=${vars[key]} exceeds the viewport`);
        }
      }
    }
  }
});

test("the non-modal corner card clears the phone bottom nav", () => {
  const phone = overlayGeometry("corner", false, false);
  assert(/overlay-above-nav/.test(phone.wrap),
    "the first-run wizard must not cover the tabs it is telling the user to press");
  assert(/bottom:\s*calc\(3\.5rem \+ env\(safe-area-inset-bottom/.test(block(".overlay-above-nav")),
    ".overlay-above-nav must offset by the nav height plus the inset");
  const desktop = overlayGeometry("corner", true, true);
  assert(!/overlay-above-nav/.test(desktop.wrap),
    "there is no bottom nav above sm — the offset would just be a gap");
});

test("dock is a right column on lg and a bottom sheet below it", () => {
  const desk = overlayGeometry("dock", true, true);
  assert(/right-0/.test(desk.wrap) && desk.vars["--ov-w"] === "420px", "lg dock is a right column");
  // On lg the wrapper is only as wide as the drawer, so the dismiss catcher it
  // contains cannot blanket the viewport — the app stays interactive behind a
  // docked log, which is how it behaved before the drawer was portalled.
  assert(!/inset-0/.test(desk.wrap), "lg dock wrapper must not cover the whole viewport");
  const sheet = overlayGeometry("dock", false, false);
  assert(/inset-0/.test(sheet.wrap) && /justify-end/.test(sheet.wrap), "below lg the dock is a bottom sheet");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\noverlay.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
