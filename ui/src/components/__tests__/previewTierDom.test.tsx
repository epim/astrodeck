// previewTierDom.test.tsx — which asset the Monitor's live tile asks for (#221).
//
//   Run directly:  npx tsx src/components/__tests__/previewTierDom.test.tsx
//   Also type-checked by `tsc -b` in the build.
//
// Same dependency-free harness as galleryTile.test.tsx — no vitest, no jsdom.
//
// THE DEFECT. Reported 2026-08-10: "the live view option while the plan runs
// shows a really pixelated image — like a digital zoom, or an ultra-compressed
// thumbnail being shown as the full image", then "until I click on it anyway".
//
// It was exactly that. The server publishes two sizes per frame:
//     /api/preview/{id}            to_jpeg   max_width 1400  q85   keep 8
//     /api/preview/{id}/thumb.jpg  to_thumb  max_width  160  q70   keep 50
// and the tile asked for the 160 px one, stretched across a `w-full`
// `aspect-[16/10]` box (~700 px on a desktop). Clicking opened Capture, whose
// PreviewStage uses the display bytes — hence "until I click on it".
//
// These are SOURCE assertions rather than rendered ones, deliberately: the tier
// fallback is driven by an <img> onError, which needs a real network stack and a
// real decoder to exercise. What can be pinned without either is the thing that
// was actually wrong — which URL is asked for FIRST, and that the fallback is
// still reachable. The rendered behaviour is covered by the Playwright run
// against the relay, where there IS a decoder.

interface NodeFsLike { readFileSync(path: string, encoding: string): string }
const nodeImport = (s: string): Promise<unknown> =>
  (Function("m", "return import(m)") as (m: string) => Promise<unknown>)(s);
const fs = (await nodeImport("node:fs")) as NodeFsLike;
const pathOf = (rel: string): string =>
  decodeURIComponent(new URL(rel, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, "$1");

const src = fs.readFileSync(pathOf("../monitor.tsx"), "utf8");

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ tests

test("the live tile does not hardcode the thumb as its primary source", () => {
  // The exact shape of the bug: `src={u(\`/api/preview/${id}/thumb.jpg\`)}` on
  // the tile's PRIMARY img. The fallback img may still use it.
  const primary = /key=\{`?\$\{previewId\}/.test(src);
  assert(primary, "could not find the primary img — this test has drifted");
  assert(!/src=\{u\(`\/api\/preview\/\$\{previewId\}\/thumb\.jpg`\)\}/.test(src),
         "the primary img still asks for the 160px thumb");
});

test("the tier helper prefers the full display bytes", () => {
  const m = src.match(/function previewUrlFor[\s\S]{0,300}?\n\}/);
  assert(!!m, "previewUrlFor is gone — the tier mechanism was removed");
  const body = m![0];
  const displayIdx = body.indexOf("`/api/preview/${id}`");
  const thumbIdx = body.indexOf("/thumb.jpg");
  assert(displayIdx >= 0, "no display-bytes URL in previewUrlFor");
  assert(thumbIdx >= 0, "no thumb URL in previewUrlFor — the fallback is gone");
  assert(displayIdx < thumbIdx,
         "the thumb is returned before the display bytes — the tiers are inverted");
});

test("the tile starts at the display tier on every new frame", () => {
  assert(/setTier\("display"\)/.test(src),
         "nothing resets the tier, so one 404 would strand every later frame on the thumb");
});

test("the fallback still exists — a 404 steps DOWN rather than going stale", () => {
  // Only 8 display frames are retained against 50 thumbs, and a NINA-supplied
  // frame may have no display bytes at all. Removing the fallback would trade
  // this bug for 404-to-STALE, which is what the thumb was chosen to avoid.
  assert(/if \(tier === "display"\) setTier\("thumb"\)/.test(src),
         "an error no longer falls back to the thumb");
  assert(/else setLoadError\(true\)/.test(src),
         "a failed thumb no longer reports stale — the tile would spin forever");
});

test("the filmstrip is left alone", () => {
  // The filmstrip's tiles really are thumbnail-sized; changing them would cost
  // bandwidth for no visible gain. Its source lives in another file, so all
  // that is asserted here is that this change did not reach into it.
  const strip = fs.readFileSync(pathOf("../preview/FrameFilmstrip.tsx"), "utf8");
  assert(strip.includes("/thumb.jpg"),
         "the filmstrip stopped using thumbs — it should still use them");
});

test("the reasoning is recorded where the next person will look", () => {
  // This tile has now been wrong twice about which asset it shows. The numbers
  // that decide it (1400 vs 160, keep 8 vs keep 50) are not guessable from the
  // call site, so they have to be written down next to it.
  assert(/1400/.test(src) && /160/.test(src),
         "the two asset sizes are not stated in monitor.tsx");
  assert(/PREVIEW_DISPLAY_KEEP|keep 8|= 8/.test(src),
         "the retention asymmetry that justifies the fallback is not stated");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
if (failures.length) console.log(failures.join("\n"));
console.log(`previewTierDom.test.tsx: ${passed}/${total} passed`);
export default { passed, failed, total };
