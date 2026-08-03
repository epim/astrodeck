// galleryTile.test.tsx — the gallery grid tile (components/gallery/FrameTile).
//
//   Run directly:  npx tsx src/components/__tests__/galleryTile.test.tsx
//   Also type-checked by `tsc -b` in the build.
//
// Same dependency-free inline-assert harness as focusPod.test.tsx — no vitest,
// no jsdom. THERE IS NO LAYOUT HERE, so nothing below asserts a rendered pixel:
// TileSurface is rendered to static markup and checked for WHAT IT SAYS and
// WHICH CONTROLS EXIST, which is where this component can be wrong in a way that
// costs someone a frame.
//
// The defect this file exists to stop: a gallery that draws a browser's broken-
// image icon for a frame whose file is gone. That icon is indistinguishable from
// a thumbnail that merely failed to render, and the two have OPPOSITE
// consequences — one frame is downloadable and the other does not exist. So the
// three failure states are asserted as three different tiles, and the presence
// or absence of the download affordance is asserted with them.

// ------------------------------------------------------------ browser stubs
// FrameTile imports lib/base, which reads window.location.pathname AT MODULE
// LOAD. Stub before importing, then import DYNAMICALLY so the stub is in place
// first (same idiom as lib/__tests__/caps.test.ts).
const g = globalThis as unknown as { window?: unknown; document?: unknown };
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", protocol: "http:", host: "test" } };
}
if (typeof g.document === "undefined") {
  g.document = { documentElement: { classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } }, style: { setProperty() {}, getPropertyValue() { return ""; } } } };
}

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { GalleryFrame } from "../../types";

const tileMod = await import("../gallery/FrameTile");
const { TileSurface } = tileMod;
type TileState = import("../gallery/FrameTile").TileState;

interface NodeFsLike { readFileSync(path: string, encoding: string): string }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const pathOf = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");
const tileSrc = fs.readFileSync(pathOf("../gallery/FrameTile.tsx"), "utf8");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function has(hay: string, needle: string, msg = ""): void {
  if (!hay.includes(needle)) throw new Error(`${msg} — markup does not contain ${JSON.stringify(needle)}`);
}
function lacks(hay: string, needle: string, msg = ""): void {
  if (hay.includes(needle)) throw new Error(`${msg} — markup unexpectedly contains ${JSON.stringify(needle)}`);
}

const FRAME: GalleryFrame = {
  path: "M42/Light_M42_L_2026-06-15_235000_0001.fits",
  name: "Light_M42_L_2026-06-15_235000_0001.fits",
  folder: "M42",
  night: "2026-06-15",
  ts: 1781234000,
  // Rig-local, sent by the server. The tile must never re-derive these from
  // `ts`: `night` above was computed in the observatory's timezone and a browser
  // on the relay is not in it.
  local_date: "2026-06-15",
  local_clock: "23:50",
  target: "M42",
  filter: "L",
  frame_type: "Light",
  exposure_s: 300,
  bytes: 524880,
  mtime: 1781234567.5,
};

function render(over: {
  state?: TileState; status?: number; selected?: boolean; selectable?: boolean;
  canDownload?: boolean; frame?: GalleryFrame; thumbSrc?: string;
} = {}): string {
  return renderToStaticMarkup(
    createElement(TileSurface, {
      frame: over.frame ?? FRAME,
      state: over.state ?? "ok",
      status: over.status,
      selected: over.selected ?? false,
      selectable: over.selectable ?? true,
      canDownload: over.canDownload ?? true,
      onToggle: () => {},
      thumbSrc: over.thumbSrc ?? "/api/gallery/thumb?path=x&w=256",
    }),
  );
}

// =========================================================== the three failures

test("a frame whose FILE IS GONE renders as missing — words, not a broken image", () => {
  const html = render({ state: "missing", status: 404 });
  has(html, "FILE GONE", "the tile must say what happened");
  has(html, "Not on disk any more");
  // No <img> at all: a broken-image glyph is exactly the outcome being avoided.
  lacks(html, "<img", "a missing frame must not attempt to render an image");
});

test("a missing frame offers NO download — there is nothing there to download", () => {
  const html = render({ state: "missing", status: 404 });
  lacks(html, "/api/gallery/file", "offering a download for a file the server says is gone is the same lie");
});

test("an UNRENDERABLE frame says so and KEEPS its download", () => {
  // 422, not 404: the FITS is intact, this server just cannot draw it (.xisf,
  // an exotic BITPIX). Taking the download away here would lose a real frame.
  const html = render({ state: "unrenderable", status: 422 });
  has(html, "NO PREVIEW");
  has(html, "intact");
  has(html, "/api/gallery/file", "an unrenderable frame is still a downloadable frame");
});

test("an unexplained failure reports its status rather than guessing", () => {
  const html = render({ state: "error", status: 503 });
  has(html, "PREVIEW FAILED");
  has(html, "503");
  has(html, "/api/gallery/file");
});

test("every failure state carries a GLYPH as well as a colour", () => {
  // The night palette collapses bad/warn toward the same coral, so a hue-only
  // distinction says nothing at 2am (design-system: non-hue channel required).
  for (const state of ["missing", "unrenderable", "error"] as TileState[]) {
    const html = render({ state, status: 500 });
    assert(/<svg/.test(html), `${state} tile has no glyph — colour alone is not a channel`);
  }
});

test("the tile publishes its state, so a probe/test can read it without OCR", () => {
  has(render({ state: "ok" }), 'data-tile-state="ok"');
  has(render({ state: "missing" }), 'data-tile-state="missing"');
});

// ================================================================= affordances

test("a healthy tile renders the thumbnail and the download", () => {
  const html = render({ state: "ok" });
  has(html, "<img");
  has(html, "Preview of Light_M42_L_2026-06-15_235000_0001.fits", "the image needs a real alt text");
  has(html, "/api/gallery/file");
});

test("without view.media there is no FITS download anywhere on the tile", () => {
  // The frames embed SITELAT/SITELONG; a viewer browses thumbnails and no more.
  const html = render({ state: "ok", canDownload: false });
  lacks(html, "/api/gallery/file");
  has(html, "<img", "thumbnails are NOT restricted — only the raw FITS is");
});

test("the tick box is a real checkbox with a name, and disappears when nothing can act on it", () => {
  const on = render({ selectable: true });
  has(on, 'type="checkbox"');
  has(on, 'aria-label="Select Light_M42_L_2026-06-15_235000_0001.fits"');
  const off = render({ selectable: false });
  lacks(off, 'type="checkbox"', "a tick box with no bulk action behind it is a control that does nothing");
});

test("selection is visible on the tile itself, not only in the toolbar count", () => {
  has(render({ selected: true }), 'data-selected="true"');
  lacks(render({ selected: false }), 'data-selected="true"');
});

// ========================================================== night vs filename

test("a frame that crossed midnight explains itself on the tile", () => {
  // Its filename carries one calendar date and it is filed under another. That
  // is the single most confusing thing about this screen, so the tile that
  // shows it is where the explanation goes.
  const html = render({
    frame: { ...FRAME, night: "2026-06-15", local_date: "2026-06-16", local_clock: "00:12" },
  });
  has(html, "2026-06-15", "the night it is filed under");
  has(html, "2026-06-16", "the calendar date its FILENAME carries");
  has(html, "00:12", "the clock it was actually shot at");
  has(html, "noon to noon", "why the two dates differ");
});

test("a frame whose dates agree does not nag about the rollover", () => {
  // Both dates are the SERVER's, so this is a comparison of two rig-timezone
  // strings — not of a rig night against whatever calendar the viewer's laptop
  // is on. Deriving the calendar date from `ts` here is how a user in London
  // would see this note on every frame a rig in Arizona ever took.
  const html = render({ frame: { ...FRAME, night: FRAME.local_date } });
  lacks(html, "noon to noon");
});

test("the tile's hover text quotes the RIG's clock, not the browser's", () => {
  const html = render({ frame: { ...FRAME, ts: 0, local_clock: "23:50" } });
  has(html, "captured 23:50 rig time", "the clock has to be the one the server sent");
  lacks(html, "1970", "the tile formatted `ts` in the viewer's timezone");
});

test("the caption states the size — the number that decides whether to download", () => {
  has(render(), "513 kB");
  has(render(), "L · 300s · Light");
});

// =============================================================== lazy loading

test("nothing is requested until the tile is near the viewport", () => {
  // A 50k-frame library must not render 50k images. The container gates the
  // <img> on the observer; TileSurface has no src of its own to leak.
  assert(/IntersectionObserver/.test(tileSrc), "the grid must lazy-load through an IntersectionObserver");
  assert(/inView \?/.test(tileSrc), "the tile must render its placeholder until it is in view");
  assert(/io\.disconnect\(\)/.test(tileSrc), "the observer must stop watching once the tile has loaded");
});

test("a browser without IntersectionObserver still sees pictures (fail open)", () => {
  assert(/typeof IntersectionObserver === "undefined"[\s\S]{0,120}setInView\(true\)/.test(tileSrc),
    "a missing IntersectionObserver must fall back to loading, not to a grid of blanks");
});

test("the failure path costs exactly ONE extra request, and only on failure", () => {
  // <img onerror> cannot report a status code, so the status is fetched once.
  // The happy path must never fetch: that would put every thumbnail through a
  // Blob and hand us hundreds of object URLs to revoke.
  const calls = tileSrc.match(/fetch\(/g) ?? [];
  assert(calls.length === 1, `expected exactly one fetch() (the failure probe), found ${calls.length}`);
  assert(/onImgError/.test(tileSrc), "the probe must hang off the image's error, not off render");
  assert(!/createObjectURL/.test(tileSrc), "thumbnails are plain <img> srcs, never object URLs");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\ngalleryTile.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
