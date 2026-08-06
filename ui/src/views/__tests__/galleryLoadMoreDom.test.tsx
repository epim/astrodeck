// galleryLoadMoreDom.test.tsx — "Load more" versus a filter change.
//
//   Run directly:  npx tsx src/views/__tests__/galleryLoadMoreDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT THIS IS ABOUT (UX-2026-08-05 #23). Every OTHER read in GalleryView is an
// effect with an alive/generation guard. "Load more" is fired from a click, so
// it had none — and it is the one read that APPENDS. A page fetched under the
// old filter therefore did not merely paint stale tiles: it concatenated 200 of
// them onto the new grid and replaced `page`, the object `total`/`bytes` come
// from. The select-all count, the delete confirmation's count, and the price on
// the download button are all computed from that object — while the .zip href
// is built from the filter itself, so the button and the wire disagreed with no
// bug in either of them.
//
// The whole race is expressible here because both reads go through `fetch`:
// hold the "load more" response open, change the filter, let the new listing
// land, and only then answer the old request.

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
win.WebSocket = class { close() {} addEventListener() {} send() {} };

// ---- the wire -------------------------------------------------------------
const NIGHT_A = "2026-07-30";   // the wide, 400-frame set the grid opens on
const TONIGHT = "2026-08-05";   // 5 frames

function frame(i: number, night: string): any {
  return {
    path: `${night}/L_${String(i).padStart(4, "0")}.fits`,
    name: `L_${String(i).padStart(4, "0")}.fits`,
    folder: night, night, ts: 1_700_000_000 + i,
    local_date: night, local_clock: "23:00",
    target: "NGC 7000", filter: "L", frame_type: "Light",
    exposure_s: 300, bytes: 50_000_000, mtime: 1_700_000_000 + i,
  };
}
function page(night: string, offset: number, count: number, total: number): any {
  return {
    frames: Array.from({ length: count }, (_, i) => frame(offset + i, night)),
    total, bytes: total * 50_000_000, offset, limit: 200,
    truncated: false, scan_ms: 4,
  };
}

/** A response we can hold open. `settle` resolves it when the test says so. */
type Pending = { url: string; settle: (body: any) => void };
const pending: Pending[] = [];
function respond(match: string, body: any): void {
  const i = pending.findIndex((p) => p.url.includes(match));
  if (i < 0) throw new Error(`no request in flight matching "${match}" (have: ${pending.map((p) => p.url).join(", ")})`);
  const [p] = pending.splice(i, 1);
  p.settle(body);
}
function inFlight(match: string): boolean {
  return pending.some((p) => p.url.includes(match));
}

win.fetch = (url: string) =>
  new Promise((resolve) => {
    pending.push({
      url: String(url),
      settle: (body: any) => resolve({
        ok: true, status: 200, statusText: "OK",
        json: () => Promise.resolve(body),
      }),
    });
  });

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "Element", "Node", "Event",
  "CustomEvent", "MouseEvent", "KeyboardEvent", "localStorage", "getComputedStyle",
  "matchMedia", "WebSocket", "requestAnimationFrame", "cancelAnimationFrame", "fetch",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const GalleryView = (await import("../GalleryView")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

/** Let every already-resolved promise flush into React. */
async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
}

act(() => {
  useStore.setState({
    principal: {
      role: "admin", email: null,
      caps: ["view.status", "view.preview", "view.media", "control.capture"],
    },
  } as never);
});

const container = win.document.getElementById("root") as any;
const root = createRoot(container);
act(() => { root.render(createElement(GalleryView)); });

const tiles = () => container.querySelectorAll("[data-tile-state]").length;
const text = () => container.textContent || "";
const byText = (re: RegExp): any =>
  [...container.querySelectorAll("button, a")].find((b: any) => re.test(b.textContent || ""));
const click = (el: any) => act(() => {
  el.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
});

// ------------------------------------------------------------------ the run
// One ordered scenario; the assertions inside it are the tests. Wrapped so a
// step that cannot even be reached is REPORTED as a failure rather than killing
// the file and leaving it unscorable.
async function scenario(): Promise<void> {
await flush();
respond("/api/gallery/frames", page(NIGHT_A, 0, 200, 400));
respond("/api/gallery/nights", {
  current: TONIGHT,
  nights: [{ night: TONIGHT, frames: 5, bytes: 250_000_000 },
           { night: NIGHT_A, frames: 400, bytes: 20_000_000_000 }],
  truncated: false,
});
if (inFlight("/api/gallery/trash")) respond("/api/gallery/trash", { count: 0, ttl_days: 30, entries: [] });
await flush();

test("the grid opened on the wide set — 200 of 400 shown", () => {
  assert(tiles() === 200, `expected 200 tiles, got ${tiles()}`);
  assert(/400/.test(text()), `the totals line does not mention the 400-frame set: ${text().slice(0, 200)}`);
});

const loadMore = byText(/Load .* more/);
test("a Load more button exists — otherwise there is no race to test", () => {
  assert(loadMore != null, `no "Load more" control rendered: ${text().slice(0, 200)}`);
});

// 1. ask for the next page, and DO NOT answer it yet
click(loadMore);
await flush();
test("the Load more request is in flight", () => {
  assert(inFlight("offset=200"),
    `no offset=200 request in flight after the click: ${pending.map((p) => p.url).join(", ")}`);
});

// 2. change the filter while that request is still open
const tonightBtn = byText(/Tonight/);
click(tonightBtn);
await flush();
test("changing the filter starts a new listing", () => {
  assert(tonightBtn != null, "no Tonight filter button");
  assert(inFlight(`night_from=${TONIGHT}`) || inFlight(TONIGHT),
    `the filter change fired no new listing: ${pending.map((p) => p.url).join(", ")}`);
});

// 3. the NEW set lands
respond(TONIGHT, page(TONIGHT, 0, 5, 5));
await flush();
test("the new, narrow set is on screen", () => {
  assert(tiles() === 5, `expected the 5-frame night, got ${tiles()} tiles`);
});

// 4. and only now does the old page answer
respond("offset=200", page(NIGHT_A, 200, 200, 400));
await flush();

// --------------------------------------------------------------- THE HAZARD
test("the stale page is NOT appended to the new grid", () => {
  assert(tiles() === 5,
    `the grid holds ${tiles()} tiles — a page fetched for the previous filter was ` +
    "concatenated onto a night that has five frames in it");
});

test("and it does not clobber the totals the actions are priced from", () => {
  // Both of these read `page.total` — the field the stale response overwrote.
  // (The whole-screen text is not a fair witness: the night dropdown legitimately
  // lists "2026-07-30 · 400 frames" as an option you can still pick.)
  const selectAll = byText(/Select all/);
  assert(selectAll != null, "no select-all control to check");
  assert(/\b5\b/.test(selectAll.textContent || "") && !/400/.test(selectAll.textContent || ""),
    `"${selectAll.textContent}" — select-all still offers the PREVIOUS filter's count, ` +
    "and that count is what the delete confirmation quotes back to the user");
  const dl = byText(/Download/);
  assert(dl != null && !/400/.test(dl.textContent || ""),
    `the download button is priced from the stale page: "${dl?.textContent}"`);
});
}

await scenario().catch((e: unknown) => {
  failed++;
  failures.push(`x the scenario could not be driven to the end: ${(e as Error).message}`);
});

// ================================= the Frames/Trash tabs, under the red filter
// The selected tab used to be marked by `!border-accent !text-accent` alone —
// border colour and text colour, nothing else. In night mode that is worse than
// no cue: --accent (#ff3a3a) is DIMMER than --text (#ff7a7a), so the ACTIVE
// label read fainter than the inactive one — like the disabled one — while the
// 1px border got brighter. Two channels pointing opposite ways, and the label
// wins by area. index.css:154-157 states the rule ("Any status that must survive
// night mode needs a non-hue channel — ... fill for selection") and four of the
// five other hand-rolled uses of this exact idiom in the repo already obey it
// (NavMoreSheet:269, SlewPad:372, SlewPad:467, MountView:566). These two were
// the only occurrences that dropped the fill.
//
// A DOM test cannot see a colour, so the assertion is the mechanism: the
// selected face must differ from the unselected one by something that is not a
// colour word — a fill.
//
// AND THE FILL HAS TO BE `!important`, WHICH IS NOT A STYLE PREFERENCE. `.btn`
// is declared in index.css OUTSIDE any cascade layer and sets
// `background: var(--bg-raise)`; Tailwind's `bg-accent/10` is emitted inside
// `@layer utilities`. Unlayered CSS beats layered CSS regardless of specificity,
// so a plain `bg-accent/10` on a `.btn` paints nothing at all. jsdom computes no
// cascade and would happily report the class as present — which is exactly how
// a "fixed" tab could ship still looking identical under the red filter, and
// why this file asserts the `!`. (MountView.tsx:623-629 documents the same trap
// after deleting its own dead `bg-accent/10`.)
const tab = (re: RegExp): any =>
  [...container.querySelectorAll("button")]
    .find((b: any) => re.test(b.textContent || "") && b.getAttribute("aria-pressed") != null);
/** Every background-fill utility on the element that can actually WIN against
 *  `.btn` — i.e. the important ones. A non-important `bg-*` is a dead
 *  declaration here, so it deliberately does not count as a fill. */
const fills = (el: any): string[] =>
  String(el?.className ?? "").match(/(?:^|\s)!bg-[\w./[\]-]+/g)?.map((s) => s.trim()) ?? [];
/** ...and the trap itself: a fill that is present but cannot render. */
const deadFills = (el: any): string[] =>
  String(el?.className ?? "").match(/(?:^|\s)bg-[\w./[\]-]+/g)?.map((s) => s.trim()) ?? [];

function tabScenario(): void {
  const frames = tab(/Frames/);
  const trash = tab(/Trash/);
  test("GUARD: both gallery tabs rendered, with Frames the selected one", () => {
    assert(frames != null, `no Frames tab: ${text().slice(0, 200)}`);
    assert(trash != null, "no Trash tab — this account should hold control.capture");
    assert(frames.getAttribute("aria-pressed") === "true", "precondition: Frames is selected");
    assert(trash.getAttribute("aria-pressed") === "false", "precondition: Trash is not");
  });

  test("the selected tab is marked by a FILL, not by colour alone", () => {
    assert(fills(frames).length > 0,
      `the selected tab carries no fill that can render (class="${frames.className}") — ` +
      "under the red night palette its only cues are a dimmer label and a brighter " +
      "hairline, which point in opposite directions and read as the DISABLED tab. " +
      "A non-important bg-* here does not count: `.btn` is unlayered and its " +
      "background beats anything in @layer utilities.");
    assert(fills(trash).length === 0,
      `the UNSELECTED tab carries a fill too (class="${trash.className}"), so the fill ` +
      "distinguishes nothing");
    // The specific way this fix can be silently undone.
    assert(deadFills(frames).length === 0,
      `the selected tab carries a NON-important fill (${deadFills(frames).join(" ")}) — ` +
      "it is in the class list and paints no pixels, so the tab looks exactly as " +
      "broken as before while every class-string check says it was fixed");
  });

  test("and both tabs agree on what selection looks like", () => {
    // Byte-identical conditionals or the pair contradicts itself. Fixing one and
    // not the other is the failure mode this catches.
    // Snapshot FIRST: React reuses the same DOM node and rewrites className in
    // place, so reading `frames` after the click would read the DESELECTED face
    // and the comparison would pass by comparing nothing to nothing.
    const selectedFrames = fills(frames).join(" ");
    click(trash);
    const frames2 = tab(/Frames/);
    const trash2 = tab(/Trash/);
    assert(trash2.getAttribute("aria-pressed") === "true",
      "precondition: clicking Trash selected it");
    assert(selectedFrames.length > 0, "precondition: the snapshot caught the selected face");
    assert(fills(trash2).join(" ") === selectedFrames,
      `selected Trash paints "${fills(trash2).join(" ")}" where selected Frames painted ` +
      `"${selectedFrames}" — the two halves of one control disagree`);
    assert(fills(frames2).length === 0, "the deselected Frames tab kept its fill");
  });
}

try { tabScenario(); }
catch (e: unknown) {
  failed++;
  failures.push(`x the tab scenario could not be driven to the end: ${(e as Error).message}`);
}

// ------------------------------------------------------------------- report
act(() => { root.unmount(); });
const total = passed + failed;
console.log(`galleryLoadMoreDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
