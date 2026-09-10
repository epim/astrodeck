// archiveDom.test.tsx - SESSION > ARCHIVE (the frame library), MOUNTED.
//
//   Run directly:  npx tsx src/next/hubs/session/sheets/__tests__/archiveDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc`.
//
// WHAT THIS FILE EXISTS FOR. The sheet had no DOM test at all, and the two
// defects below are exactly the kind a source read misses:
//
//  1. REBUILD PREVIEWS DECLARED NO CAPABILITY (review #72). Every other write
//     on this screen is gated - `downloadReason` on `view.media`,
//     `deleteReason` on `control.capture` - and this one was not, while
//     `POST /api/gallery/thumbs/backfill` is `CAP_CONTROL_CAPTURE` on the
//     server (`app.py:7295`). A viewer holds `view.preview`, so the Frames tab
//     renders for them in full: the button was live, undimmed, and answered a
//     press with a 403 toast. That is the 403-on-tap that non-negotiable 6
//     exists to prevent.
//  2. THE JPEG VIEWER WAS GATED ON THE WRONG CAPABILITY (review #74).
//     `FrameViewer` fetches `/api/gallery/view` and `/api/gallery/thumb`, both
//     `CAP_VIEW_PREVIEW`. Gating the tile's OPEN on `view.media` withheld a
//     picture the server would have served - which is the half of the FITS
//     deviation that says JPEG previews stay available to everyone.
//
// WAVE R7 ADDS six more, all of them about the rebuilt area under
// `session/gallery/frames/` (`FrameTile`, `FrameViewer`, `TrashPanel` and the
// live stack's chrome) rather than about the sheet's own layout:
//
//  3. THE LAZY LOAD IS THE FEATURE. A page is up to 500 rows; rendering their
//     <img> tags eagerly fires 500 thumbnail requests at a Pi-class box for the
//     dozen tiles a viewport shows. The observer gate is asserted from BOTH
//     sides - nothing requested while off screen, exactly one request per tile
//     once revealed - because a rebuild that quietly drops it looks identical
//     on a two-row fixture and falls over on a real library.
//  4. BACK CLOSES THE VIEWER. The viewer stopped being a click-anywhere scrim
//     and became a `Sheet`, so the way out is the design's BACK pill. A viewer
//     with no way out is a trapped user.
//  5. RESTORE POSTS ONCE. The bin's whole promise.
//  6. DELETE IS HONEST-DISABLED, NOT HIDDEN, and fires nothing.
//  7. THE FITS RULE NAMES THE ROLES THAT HOLD IT. `view.media` is held by
//     SYNCER and admin (`lib/caps.ts` ROLE_CAPS). A note saying "admin access"
//     would be wrong for the syncer it locks out and is the exact defect
//     `accessPhrase()` exists to prevent (ARCHITECTURE section 8, amended).
//  8. NO EM-DASHES REACH THE SCREEN. `lib/gallery.ts` is shared with
//     `#/classic` and still writes them; the new UI's rule is hyphens, so the
//     whole rendered subtree is swept.
//
// The sheet is tablet-and-desktop only, so `matchMedia` answers the 768px
// query true. A phone fixture would render the honest-absence card and every
// assertion below would be vacuous - the first test says so out loud.
//
// Convention: shell-and-tests.md section 4 - jsdom by hand, createRoot + act,
// native events, printed tally plus the `{ passed, failed, total }` export.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ------------------------------------------------------------------ css stub
// The rebuilt area's root module imports `frames.css` (wave R7's rule: one
// `next.css` owner, every other area carries its own file). Node has no idea
// what a `.css` file is, so a synchronous load hook answers with an empty
// module - the same hook `shellDom.test.tsx` installs for `next.css`.
{
  const { registerHooks } = await import("node:module");
  registerHooks({
    load(url: string, context: any, nextLoad: any) {
      if (url.endsWith(".css")) {
        return { format: "module", shortCircuit: true, source: "export default {};" };
      }
      return nextLoad(url, context);
    },
  } as any);
}

// ---------------------------------------------------------------- jsdom first
const { JSDOM } = await import("jsdom");
const dom = new JSDOM(
  `<!doctype html><html><body><div id="root"></div></body></html>`,
  { url: "http://local/#/session/gallery/archive", pretendToBeVisual: true },
);
const win = dom.window as any;

win.matchMedia = (q: string) => ({
  matches: /min-width:\s*768px/.test(String(q)),
  addEventListener() {}, removeEventListener() {},
  addListener() {}, removeListener() {},
});
win.WebSocket = class { close() {} addEventListener() {} send() {} };
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };

// The observer, on a switch. `ioAuto` true is every other test in this file:
// tiles are in view the instant they mount. The lazy-load test flips it off,
// mounts, asserts that nothing asked for a picture, and then reveals them by
// hand - which is the only way to tell a working gate from a missing one.
let ioAuto = true;
let ioPending: Array<() => void> = [];
let ioDisconnects = 0;
win.IntersectionObserver = class {
  constructor(private cb: any) {}
  observe(el: any) {
    const fire = () => this.cb([{ target: el, isIntersecting: true }]);
    if (ioAuto) fire(); else ioPending.push(fire);
  }
  unobserve() {}
  disconnect() { ioDisconnects++; }
};
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "HTMLAnchorElement", "Element", "Node", "Event", "CustomEvent", "MouseEvent",
  "KeyboardEvent", "localStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "ResizeObserver", "IntersectionObserver", "requestAnimationFrame",
  "cancelAnimationFrame", "Image",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
interface Asked { method: string; url: string }
const asked: Asked[] = [];
const NIGHT = "2026-09-08";

const FRAMES = [
  {
    path: "M31/L_0001.fits", name: "L_0001.fits", folder: "M31", night: NIGHT,
    ts: 1_757_000_001, local_date: NIGHT, local_clock: "22:10", target: "M31",
    filter: "L", frame_type: "Light", exposure_s: 60, bytes: 10 * 1024 * 1024,
    mtime: 1_757_000_001,
  },
  {
    path: "M31/L_0002.fits", name: "L_0002.fits", folder: "M31", night: NIGHT,
    ts: 1_757_000_002, local_date: NIGHT, local_clock: "22:12", target: "M31",
    filter: "L", frame_type: "Light", exposure_s: 60, bytes: 10 * 1024 * 1024,
    mtime: 1_757_000_002,
  },
];

/** One item in the bin, with everything the row prints: where it goes back to,
 *  when it dies, whether it still can. */
const TRASHED = {
  path: "trash/M31/L_0009.fits", original: "M31/L_0009.fits", name: "L_0009.fits",
  deleted_at: Math.floor(Date.now() / 1000) - 3600,
  expires_at: Math.floor(Date.now() / 1000) + 29 * 86400,
  bytes: 10 * 1024 * 1024, restorable: true,
};

const ok = (data: unknown) => ({
  ok: true, status: 200, statusText: "OK", json: async () => data,
});

g.fetch = async (url: any, init?: { method?: string }) => {
  const u = String(url);
  asked.push({ method: (init?.method ?? "GET").toUpperCase(), url: u });
  if (u.includes("/api/gallery/nights")) {
    return ok({
      current: NIGHT,
      nights: [{ night: NIGHT, frames: FRAMES.length, bytes: 20 * 1024 * 1024 }],
      truncated: false,
    });
  }
  // Ordered before the bare trash listing: every one of these contains
  // "/api/gallery/trash" as a prefix.
  if (u.includes("/api/gallery/trash/restore")) {
    return ok({ restored: [{ path: TRASHED.path, restored_to: TRASHED.original }], failed: [] });
  }
  if (u.includes("/api/gallery/trash/purge")) {
    return ok({ purged: 1, bytes: TRASHED.bytes, failed: [] });
  }
  if (u.includes("/api/gallery/trash")) {
    // `count_only=1` is the tab badge; the full listing is the panel.
    const countOnly = u.includes("count_only");
    return ok({
      items: countOnly ? [] : [TRASHED],
      count: 1, bytes: TRASHED.bytes, ttl_days: 30,
    });
  }
  if (u.includes("/api/gallery/thumbs/backfill")) {
    return ok({ frames: 2, rendered: 2, unrenderable: 0, truncated: false });
  }
  if (u.includes("/api/gallery/frames")) {
    // `truncated` and a COLD `scan_ms` on purpose: they are the two listing
    // states whose sentences (`truncatedNote`, `scanNote`) still carry
    // em-dashes in the shared `lib/gallery.ts`, and without them on screen the
    // em-dash sweep below would pass over a page that never had one to find.
    return ok({
      frames: FRAMES, total: FRAMES.length,
      bytes: FRAMES.reduce((a, f) => a + f.bytes, 0),
      offset: 0, limit: 200, truncated: true, scan_ms: 4200,
    });
  }
  return ok({});
};

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../../store");
const { accessPhrase } = await import("../../../../../lib/caps");
const { _reset: resetThumbQueue } = await import("../../../../../lib/thumbQueue");
const { keptSummary, materializeDisabledReason } = await import("../../../../../lib/bundleView");
const { fmtBytes, scanNote, truncatedNote } = await import("../../../../../lib/gallery");
const { hy, tileCopy, MEDIA_PHRASE } = await import("../../gallery/frames");
const { hyphenateOrNull } = await import("../../report/reportModel");
const { readFile } = await import("node:fs/promises");
const { ArchiveSheet } = await import("../archive");

// ------------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
const settle = async () => {
  for (let i = 0; i < 5; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};
const q = (sel: string) => container.querySelector(sel) as any;
const qa = (sel: string) => [...container.querySelectorAll(sel)] as any[];
const byLabel = (label: string) =>
  qa("button, a, input").find((n: any) => (n.getAttribute("aria-label") ?? "") === label);
/** Accessible NAME, whether it comes from `aria-label` or the element's own
 *  text. The tile's tick box is `Checkbox22`, which names itself with a
 *  visually-hidden span rather than an attribute. */
const byName = (name: string) =>
  qa("button, a, input").find(
    (n: any) => (n.getAttribute("aria-label") ?? n.textContent ?? "").trim() === name,
  );
const byText = (t: string) =>
  qa("button").find((n: any) => (n.textContent ?? "").trim() === t);
const tid = (t: string) => q(`[data-testid="${t}"]`);
const click = async (el: any): Promise<void> => {
  await act(async () => {
    el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
  });
  await settle();
};

const ADMIN = {
  role: "admin", email: "admin@rig",
  caps: ["view.status", "view.preview", "view.media", "control.capture", "control.mount"],
};
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

function seed(principal: unknown): void {
  act(() => {
    useStore.setState({
      principal, wsPhase: "up", equipConnected: true,
      status: { disk: { free_gb: 412, low: false, critical: false } },
      toasts: [],
    } as never);
  });
}

async function mount(principal: unknown): Promise<void> {
  if (rootRef) act(() => { rootRef!.unmount(); });
  // The queue caps in-flight thumbnails at six across the whole page; a test
  // that mounts the grid a dozen times would otherwise starve the last mount of
  // slots and assert a blank grid for the wrong reason.
  resetThumbQueue();
  ioPending = [];
  seed(principal);
  rootRef = createRoot(container);
  await act(async () => {
    rootRef!.render(createElement(ArchiveSheet as any, { params: {}, depth: 1 }));
  });
  await settle();
}

// ==================================================== 1. it rendered, as a grid
await mount(ADMIN);

await testAsync("precondition: the sheet rendered the frames tab with real tiles", async () => {
  assert(tid("session-archive") != null,
    "no session-archive marker - the fixture is wrong, not the sheet");
  assert(byText("REFRESH") != null,
    "the actions row is missing, so the REBUILD PREVIEWS assertions below would be vacuous");
  assert(byName(`Select ${FRAMES[0].name}`) != null,
    "no frame tiles - a phone-width fixture would render the honest-absence card instead");
  eq(qa('[data-testid="gallery-tile"]').length, FRAMES.length,
    "the rebuilt tile marker is missing, so every gallery-tile assertion below is vacuous");
  assert(tid("gallery-grid") != null, "the rebuilt grid did not render");
});

// ============== 2. REBUILD PREVIEWS declares control.capture (review #72)

await testAsync("an admin can rebuild previews, and it posts the backfill route", async () => {
  const btn = byText("REBUILD PREVIEWS");
  assert(btn != null, "no REBUILD PREVIEWS control");
  eq(btn!.getAttribute("aria-disabled"), null,
    "precondition: an admin found REBUILD PREVIEWS locked");
  asked.length = 0;
  await click(btn!);
  eq(asked.filter((a) => a.url.includes("/api/gallery/thumbs/backfill")).length, 1,
    "REBUILD PREVIEWS did not post exactly once");
});

await testAsync("a viewer finds it honest-disabled with the route's own capability, and fires nothing", async () => {
  await mount(VIEWER);
  const btn = byText("REBUILD PREVIEWS");
  assert(btn != null,
    "the control was HIDDEN from a viewer - nothing is hidden (ARCHITECTURE section 8), "
    + "and the Frames tab renders for a viewer, who holds view.preview");
  eq(btn!.getAttribute("aria-disabled"), "true",
    "REBUILD PREVIEWS is live for a viewer - the press returns a 403, which is the "
    + "403-on-tap non-negotiable 6 exists to prevent");
  const why = btn!.getAttribute("title") ?? "";
  assert(why.includes(accessPhrase("control.capture")),
    `the reason must be derived from the ROUTE's capability (control.capture), got "${why}"`);

  asked.length = 0;
  await click(btn!);
  eq(asked.filter((a) => a.url.includes("/api/gallery/thumbs/backfill")).length, 0,
    "a viewer's press reached the rig");
  assert((useStore.getState() as any).toasts.length > 0,
    "the refusal was silent - the reason never reached the user");
});

// ============ 3. the JPEG viewer opens at view.preview, not view.media (#74)

await testAsync("a viewer can open the full-size preview - the server serves it at view.preview", async () => {
  const open = byLabel(`Open ${FRAMES[0].name}`);
  assert(open != null,
    "the tile's OPEN is gated on view.media, so a viewer is refused a picture the "
    + "server would have served (`/api/gallery/view` is CAP_VIEW_PREVIEW)");
});

await testAsync("but the FITS download stays behind view.media", async () => {
  const dl = byLabel(`Download ${FRAMES[0].name}, 10.0 MB`);
  assert(dl == null,
    "a viewer was offered the raw FITS link - opening the preview must not have "
    + "widened the file download with it");
  assert(qa("a").every((a: any) => !String(a.getAttribute("href") ?? "").includes("/api/gallery/file")),
    "a /api/gallery/file link exists in a viewer's DOM");
});

// ================================ 4. the FITS rule names SYNCER, not just admin

await testAsync("the FITS note names every role that holds view.media, not 'admin'", async () => {
  const note = tid("archive-fits-note");
  assert(note != null,
    "a viewer got no explanation for the missing FITS download at all");
  const text = String(note.textContent ?? "");
  eq(MEDIA_PHRASE, "syncer or admin access",
    "accessPhrase(view.media) no longer names the syncer - ROLE_CAPS changed and this "
    + "copy has to follow it, not the other way round");
  assert(text.includes(MEDIA_PHRASE),
    `the note must quote accessPhrase("view.media"), got "${text}"`);
  assert(!/needs admin access/.test(text),
    "the note says 'admin access' - a SYNCER holds view.media and would be told, "
    + "wrongly, that it cannot have the files (ARCHITECTURE section 8, amended)");
});

// ============================================ 5. the viewer opens and BACK closes

await testAsync("tapping a tile opens the viewer sheet, and BACK closes it", async () => {
  await mount(ADMIN);
  assert(tid("gallery-viewer") == null, "the viewer was open before anything was tapped");
  await click(byLabel(`Open ${FRAMES[0].name}`)!);
  const sheet = tid("gallery-viewer");
  assert(sheet != null, "tapping the centre of a tile did not open the viewer");
  assert(String(sheet.textContent ?? "").includes(FRAMES[0].name),
    "the viewer does not name the frame it is showing");
  const back = byLabel("Back to library");
  assert(back != null,
    "the viewer has no BACK - it stopped being a click-anywhere scrim and became a "
    + "Sheet, so BACK is the only way out and a missing one traps the user");
  await click(back!);
  assert(tid("gallery-viewer") == null, "BACK did not close the viewer");
});

// ================================ 6. a viewer's DELETE is locked and fires nothing

await testAsync("a viewer sees the viewer's delete honest-disabled and it reaches no route", async () => {
  await mount(VIEWER);
  await click(byLabel(`Open ${FRAMES[0].name}`)!);
  const del = tid("gallery-viewer-delete");
  assert(del != null,
    "the delete was HIDDEN from a viewer - nothing is hidden (ARCHITECTURE section 8)");
  eq(del.getAttribute("aria-disabled"), "true",
    "a viewer's delete is live - the press would 403 on POST /api/gallery/trash");
  const why = del.getAttribute("title") ?? "";
  assert(why.includes(accessPhrase("control.capture")),
    `the delete's reason must name control.capture, got "${why}"`);
  asked.length = 0;
  await click(del);
  eq(asked.filter((a) => a.method === "POST" && a.url.includes("/api/gallery/trash")).length, 0,
    "a viewer's delete reached the rig");
  assert((useStore.getState() as any).confirm == null,
    "a locked delete still opened the confirmation - the refusal must come first");
  assert((useStore.getState() as any).toasts.length > 0,
    "the refusal was silent - the reason never reached the user");
});

await testAsync("a viewer keeps the TRASH tab, gets the reason, and reads no bin", async () => {
  // The tab used to vanish for a role without control.capture. Nothing is
  // hidden (ARCHITECTURE section 8) - but the whole trash surface is
  // control.capture on the server, GET included, so the locked tab must state
  // that and issue no request rather than render an error card.
  await mount(VIEWER);
  const chip = tid("subnav-trash");
  assert(chip != null,
    "the TRASH tab was hidden from a viewer - a feature that vanishes is one the "
    + "user cannot even ask about");
  asked.length = 0;
  await click(chip);
  const card = tid("gallery-trash-locked");
  assert(card != null, "the locked bin gave no reason at all");
  assert(String(card.textContent ?? "").includes(accessPhrase("control.capture")),
    "the locked bin does not name control.capture, the capability GET /api/gallery/trash enforces");
  eq(asked.filter((a) => a.url.includes("/api/gallery/trash")).length, 0,
    "a viewer's TRASH tab read the bin anyway - that is a 403 on tap");
});

// ============================================== 7. the bin restores, exactly once

await testAsync("restoring from the trash posts the restore route exactly once", async () => {
  await mount(ADMIN);
  const trashChip = tid("subnav-trash");
  assert(trashChip != null, "the TRASH tab is missing, so the bin cannot be reached at all");
  await click(trashChip);
  assert(tid("gallery-trash") != null, "the rebuilt bin did not render");
  const row = tid("gallery-trash-row");
  assert(row != null,
    "no bin rows - the fixture's listing did not reach the panel, so the restore "
    + "assertion below would be vacuous");
  await click(row);
  const restore = tid("gallery-trash-restore");
  assert(restore != null, "the bin has no RESTORE - a bin without restore is a delayed delete");
  eq(restore.getAttribute("aria-disabled"), null,
    "RESTORE is locked with a row ticked");
  asked.length = 0;
  await click(restore);
  eq(asked.filter((a) => a.method === "POST" && a.url.includes("/api/gallery/trash/restore")).length, 1,
    "RESTORE did not post exactly once");
});

await testAsync("with nothing ticked, RESTORE states why and reaches no route", async () => {
  // The fixture's listing is static, so the row it just restored is still in
  // the bin and still ticked; untick it to reach the empty-selection state.
  await click(tid("gallery-trash-row"));
  const restore = tid("gallery-trash-restore");
  assert(restore != null, "the bin lost its RESTORE after a restore");
  eq(restore.getAttribute("aria-disabled"), "true",
    "RESTORE is live with an empty selection - the press would be a 422");
  asked.length = 0;
  await click(restore);
  eq(asked.filter((a) => a.url.includes("/api/gallery/trash/restore")).length, 0,
    "an empty RESTORE reached the rig");
});

// ============================== 8. the lazy load: nothing before, once after

await testAsync("a tile requests no thumbnail until it is in view, and exactly one after", async () => {
  ioAuto = false;
  try {
    await mount(ADMIN);
    eq(qa("img.nx-frames-img").length, 0,
      "a tile set its thumbnail src before the observer revealed it - on a 500-row "
      + "page that is 500 cold FITS decodes asked for at once, which is the measured "
      + "failure (2026-08-10: 41 requests, 19x 429, zero tiles rendered)");
    eq(qa(".nx-frames-skel").length, FRAMES.length,
      "an off-screen tile reserved no box, so the scrollbar jumps as the grid fills");
    assert(ioPending.length >= FRAMES.length,
      "the tiles never registered with the observer at all, so the reveal below "
      + "would prove nothing");

    const before = ioDisconnects;
    const fire = ioPending.slice();
    await act(async () => { for (const f of fire) f(); });
    await settle();

    eq(qa("img.nx-frames-img").length, FRAMES.length,
      "a revealed tile did not request its thumbnail, or requested more than one");
    const srcs = qa("img.nx-frames-img").map((n: any) => String(n.getAttribute("src") ?? ""));
    assert(srcs.every((s) => s.includes("/api/gallery/thumb")),
      `a tile's picture is not the thumbnail route: ${srcs.join(", ")}`);
    eq(new Set(srcs).size, FRAMES.length,
      "two tiles asked for the same picture - the frame path is not in the URL");
    assert(ioDisconnects > before,
      "the observer kept watching after the tile loaded - it must disconnect, or a "
      + "scroll re-fires the reveal for every tile on the page");
  } finally {
    ioAuto = true;
  }
});

// ==================================== 9. no em-dash reaches the rendered screen

await testAsync("nothing the library renders carries an em-dash", async () => {
  await mount(ADMIN);
  const text = String(container.textContent ?? "");
  const titles = qa("[title]").map((n: any) => String(n.getAttribute("title") ?? "")).join("\n");
  // Vacuity guard: both of these sentences carry an em-dash at the source, so
  // the sweep is looking at a page that HAS the shapes it guards against.
  assert(/[—–]/.test(truncatedNote(2)) && /[—–]/.test(scanNote(4200, 2) ?? ""),
    "lib/gallery.ts no longer writes em-dashes in the two sentences this fixture "
    + "renders - pick different ones or delete the boundary, but do not leave a "
    + "sweep that cannot fail");
  assert(!/[—–]/.test(text),
    `an em-dash reached the screen: "${(/[^.]*[—–][^.]*/.exec(text) ?? [""])[0]}" - `
    + "lib/gallery.ts is shared with #/classic and still writes them, so every one "
    + "of its strings has to cross frameCopy.ts's hyphen boundary");
  assert(!/[—–]/.test(titles),
    `an em-dash reached a title attribute: "${(/[^.]*[—–][^.]*/.exec(titles) ?? [""])[0]}"`);
  assert(text.includes("were walked - this is a prefix of the library"),
    "the truncated note did not reach the screen, so the sweep has nothing to sweep");
  assert(text.includes("frame headers in 4.2s - that is the cold cost"),
    "the cold-scan note did not reach the screen, so the sweep has nothing to sweep");
});

await testAsync("the hyphen boundary rewrites the legacy tile copy, dash for dash", async () => {
  const missing = tileCopy("missing");
  assert(!/[—–]/.test(missing.hint), `tileCopy("missing") still carries an em-dash: ${missing.hint}`);
  assert(missing.hint.includes(" - "),
    `the clause break was deleted rather than replaced: ${missing.hint}`);
  // The bare "no value" glyph is a HYPHEN, not the spaced " - " a sentence
  // needs: `fmtBytes(null)` is a placeholder, not a clause.
  eq(fmtBytes(null), "—", "fmtBytes no longer returns the em-dash placeholder this guards");
  eq(hy(fmtBytes(null)), "-", "the bare placeholder became a spaced clause break");
});

if (rootRef) act(() => { rootRef!.unmount(); });

// ======================= 10. the Files sheet's stacking bundle (T-R7-7 follow-up)

await testAsync("the Files sheet's bundle block is the Disclosure primitive, not a hand-rolled one", async () => {
  const src = await readFile(
    new URL("../files.tsx", import.meta.url), "utf8",
  ) as unknown as string;
  const bundle = src.slice(src.indexOf('data-testid="files-bundle"'));
  assert(/<Disclosure\b/.test(bundle),
    "the stacking bundle is still a hand-rolled chevron button - the house Disclosure "
    + "carries the 44 px row, the aria-controls region and the honest-disabled contract");
  assert(/data-testid="files-bundle-disclosure"/.test(bundle),
    "the bundle disclosure has no marker for the probe");
  assert(!/aria-expanded=\{open\}/.test(bundle),
    "the hand-rolled aria-expanded button survived beside the Disclosure");
  assert(/hyphenateOrNull\(keptSummary\(/.test(src),
    "keptSummary reaches the Files sheet unhyphenated");
  assert(/hyphenateOrNull\(materializeDisabledReason\(/.test(src),
    "materializeDisabledReason reaches the Files sheet unhyphenated");
});

await testAsync("the two bundle sentences arrive at the Files sheet with no em-dash", async () => {
  const preview = {
    groups: [{ dir: "M31/L", target: "M31", filter: "L", exposure_s: 60, gain: null,
      binning: null, light_count: 42, accepted_count: 42, kept_count: 38, masters: {} }],
    warnings: [], keep_threshold: 0.5,
  };
  const kept = hyphenateOrNull(keptSummary(preview as never));
  assert(kept != null, "the fixture no longer produces a kept summary, so this proves nothing");
  assert(/[—–]/.test(keptSummary(preview as never) ?? ""),
    "lib/bundleView.keptSummary no longer carries an em-dash - delete this boundary "
    + "rather than leaving a guard that cannot fail");
  assert(!/[—–]/.test(kept!), `keptSummary reached the sheet with an em-dash: ${kept}`);

  const empty = { groups: [], warnings: [], keep_threshold: null };
  const why = hyphenateOrNull(materializeDisabledReason(3, empty as never));
  assert(why != null && !/[—–]/.test(why),
    `materializeDisabledReason reached the sheet with an em-dash: ${why}`);
  assert(why!.includes(" - "), `the clause break was deleted rather than replaced: ${why}`);
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`archiveDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) {
  (globalThis as unknown as { process?: { exitCode?: number } }).process!.exitCode = 1;
}
export default { passed, failed, total };
export { passed, failed, total };
