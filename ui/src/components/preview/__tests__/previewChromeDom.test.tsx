// previewChromeDom.test.tsx — the chrome drawn OVER the preview, mounted.
//
//   Run directly:  npx tsx src/components/preview/__tests__/previewChromeDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Two components, one jsdom, because the three defects here are all about state
// or attributes that only exist once something is mounted:
//
//  * PreviewStage's explanation chips were unreachable — the gesture layer
//    listens for pointerdown NATIVELY on the stage element, so a tap on a chip
//    bubbled into it and counted towards the double-tap accelerator. Asking a
//    chip what it meant zoomed the preview instead of answering. The mark that
//    exempts a press is [data-no-pan], which only StarOverlay carried.
//  * The selected-star readout outlived the frame it described: a star picked
//    on frame N was still quoted over frame N+1, with no ring under it.
//  * PreviewToolbar offered downloads for frames the server had already freed.
//    `has_lossless`/`data_is_linear` are stamped at CAPTURE time and never
//    revised, while hub._trim_previews frees the heavy arrays two frames back
//    and the whole ring entry eight frames back — and an <a download> that 404s
//    reports nothing at all.
//
// WHAT THIS CANNOT DO: jsdom lays nothing out and fetches nothing, so the stage
// measures 0x0, no <img> ever decodes, and no download is actually exercised.
// What is real is which node carries which attribute, and what the components
// do when the frame under them changes.

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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.Element.prototype.setPointerCapture = function () {};
win.Element.prototype.releasePointerCapture = function () {};
// The linear path draws through a 2D context jsdom does not implement; the
// component already bails on a null ctx, so return one rather than let jsdom
// print a "not implemented" wall over the test output.
win.HTMLCanvasElement.prototype.getContext = () => null;

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
const { useStore } = await import("../../../store");
const { PreviewStage } = await import("../PreviewStage");
const { PreviewToolbar } = await import("../PreviewToolbar");
type PreviewInfo = import("../../../types").PreviewInfo;
type OverlayToggles = import("../../../types").OverlayToggles;
type StretchParams = import("../../../types").StretchParams;
type Viewport = import("../../../types").Viewport;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------------ fixtures
/** A linear frame that clips and carries tilt + stars — i.e. one that puts
 *  every explanation chip on screen at once. */
function frame(over: Partial<PreviewInfo> = {}): PreviewInfo {
  return {
    id: 20,
    stats: { min: 100, max: 65535, mean: 900, median: 810, std: 220 },
    histogram: [1, 40, 900, 400, 90, 12, 3, 1],
    histogram_domain: "linear",
    exposure_s: 120,
    gain: 100,
    binning: 1,
    data_width: 4000,
    data_height: 3000,
    display_width: 1400,
    display_height: 1050,
    mime: "image/jpeg",
    source: "alpaca",
    is_stretched: false,
    data_is_linear: true,
    has_lossless: true,
    full_well: 51000,
    pixel_scale_arcsec: 1.2,
    auto_levels: { black: 0, mid: 0.4, white: 1 },
    star_list: [
      { x: 400, y: 300, hfr: 3.1 },
      { x: 1200, y: 900, hfr: 4.4 },
    ],
    tilt: {
      zones: [], worst_corner: "TL", spread_pct: 31, tilted: true,
    } as any,
    ts: 1_700_000_000,
    ...over,
  };
}

const overlays: OverlayToggles = {
  stars: true, clip: true, reticle: false, centerMark: true, tilt: true, bahtinov: true,
};
const stretch: StretchParams = {
  auto: true, black: 0, mid: 0.5, white: 1, brightness: 0, contrast: 0, advancedOpen: false,
};
const viewport: Viewport = { scale: 1, x: 0, y: 0, fit: true };

const container = win.document.getElementById("root") as any;
let rootRef: ReturnType<typeof createRoot> | null = null;
function mount(el: any): void {
  if (rootRef) act(() => { rootRef!.unmount(); });
  rootRef = createRoot(container);
  act(() => { rootRef!.render(el); });
}
function rerender(el: any): void {
  act(() => { rootRef!.render(el); });
}

function pointer(type: string, id: number): any {
  const ev = new win.Event(type, { bubbles: true, cancelable: true });
  ev.pointerId = id;
  ev.pointerType = "touch";
  ev.clientX = 40;
  ev.clientY = 40;
  ev.button = 0;
  ev.isPrimary = true;
  return ev;
}
function click(node: any): void {
  act(() => { node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true })); });
}

// ============================================================== PreviewStage
function stage(p: PreviewInfo, ov: OverlayToggles = overlays): any {
  return createElement(PreviewStage, {
    preview: p, viewport, setViewport: () => {}, stretch, overlays: ov,
    hfrGood: 3.5, hfrWarn: 5, night: false, linkDown: false, pinned: false,
    newSincePinned: 0, onReturnToLive: () => {},
  });
}

mount(stage(frame()));

test("the stage mounted with its explanation chips on screen", () => {
  // The anti-blank-page guard: every assertion below is about a chip, and "no
  // chip has the wrong attribute" is trivially true of a page with no chips.
  const text = container.textContent || "";
  assert(/overexposed/.test(text), "the clip chip is not on screen — the fixture is wrong, not the component");
  assert(/Field:/.test(text), "the tilt chip is not on screen — the fixture is wrong, not the component");
});

test("every explanation chip is exempt from the pan/double-tap gesture", () => {
  // usePreviewGestures skips a press inside [data-no-pan]. Without the mark the
  // press counts towards the double-tap accelerator, so the second tap on a
  // chip zooms the preview to 100% instead of opening the tooltip.
  for (const [what, needle] of [["clip", "overexposed"], ["tilt", "Field:"]] as const) {
    const el = [...container.querySelectorAll("div")]
      .find((d: any) => (d.textContent || "").includes(needle) && d.className.includes("absolute"));
    assert(el != null, `could not find the ${what} chip's positioned wrapper`);
    assert(el.closest("[data-no-pan]") != null,
      `the ${what} chip is not inside [data-no-pan] — tapping it starts a gesture instead of explaining itself`);
  }
});

test("the per-sub SNR slot is exempt too (it carries a tooltip of its own)", () => {
  // Identified by the width cap that keeps it clear of the loupe — the only
  // inline max-width in the stage's chrome.
  const snr = container.querySelector('div[style*="max-width"]');
  assert(snr != null, "no SNR chip slot rendered — the fixture is wrong, not the component");
  assert(snr.hasAttribute("data-no-pan"),
    "the SNR chip slot is not marked data-no-pan, so tapping its tooltip starts a gesture");
});

test("a star can be selected, and its HFR is reported", () => {
  const ring = container.querySelector('g[data-no-pan]');
  assert(ring != null, "no star ring rendered — the star overlay is off, so the selection test is vacuous");
  act(() => { ring.dispatchEvent(pointer("pointerdown", 5)); });
  assert(/HFR 3\.10 px/.test(container.textContent || ""),
    "tapping a star ring did not produce the HFR readout, so nothing below is testing its lifetime");
});

test("the selected-star readout does not outlive its frame", () => {
  // Precondition re-asserted from the test above: the chip is up RIGHT NOW.
  assert(/HFR 3\.10 px/.test(container.textContent || ""), "no star is selected, so this proves nothing");
  rerender(stage(frame({ id: 21 })));
  assert(!/HFR /.test(container.textContent || ""),
    "the HFR chip survived the frame swap — it is now quoting a star from a frame that is no longer on screen, " +
    "with no ring under it");
});

test("the readout is gated on the overlay that produced it", () => {
  mount(stage(frame()));
  const ring = container.querySelector('g[data-no-pan]');
  act(() => { ring.dispatchEvent(pointer("pointerdown", 5)); });
  assert(/HFR 3\.10 px/.test(container.textContent || ""), "the star was not selected, so this proves nothing");
  rerender(stage(frame(), { ...overlays, stars: false }));
  assert(!/HFR /.test(container.textContent || ""),
    "turning the star overlay off left its readout behind — the chip outlives the rings it belongs to");
});

// ============================================================ PreviewToolbar
function toolbar(p: PreviewInfo | null): any {
  return createElement(PreviewToolbar, {
    preview: p, overlays, setOverlays: () => {}, scalePct: 100,
    onZoomIn: () => {}, onZoomOut: () => {}, onFit: () => {}, onHundred: () => {},
    starsAvailable: true, clipAvailable: true, linkDown: false, stretch,
  });
}
/** `liveId` is what the toolbar measures frame age against. */
function setLive(id: number | null): void {
  act(() => { useStore.setState({ livePreviewId: id, toasts: [] } as never); });
}
const dlButton = () => [...container.querySelectorAll("button")]
  .find((b: any) => /Download/.test(b.textContent || ""));
/** The row for `label` inside the open Download popover, plus whether it is a
 *  live link or the honest-disabled stand-in. */
function dlRow(label: string): { node: any; locked: boolean } {
  const menu = container.querySelector('[role="group"]');
  assert(menu != null, "the Download popover is not open");
  const node = [...menu.querySelectorAll("a, span[role='button']")]
    .find((n: any) => (n.textContent || "").includes(label));
  assert(node != null, `no "${label}" row in the Download popover`);
  return { node, locked: node.tagName.toLowerCase() !== "a" };
}

test("on the live frame every export the frame supports is offered", () => {
  setLive(20);
  mount(toolbar(frame()));
  const btn = dlButton();
  assert(btn != null, "the Download control is locked on the live frame");
  click(btn);
  assert(!dlRow("Full-res PNG").locked, "Full-res PNG is locked on a linear live frame");
  assert(!dlRow("Lossless PNG").locked, "Lossless PNG is locked on a frame that has a lossless base");
  assert(!dlRow("Stretched PNG").locked, "Stretched PNG is locked on a frame that has a lossless base");
});

test("two frames back, the exports that read the raw copy say it is gone", () => {
  // hub keeps lossless/linear for PREVIEW_LINEAR_KEEP = 2 frames. Every flag on
  // this PreviewInfo still says the export exists, because they were stamped at
  // capture; only the frame's AGE knows better.
  setLive(22);
  mount(toolbar(frame({ id: 20 })));
  click(dlButton());
  const full = dlRow("Full-res PNG");
  assert(full.locked,
    "Full-res PNG is still a live download two frames after the rig freed the linear array — the tap 404s silently");
  assert(/full-quality copy/.test(full.node.getAttribute("aria-label") || ""),
    `the locked row must say WHY — got ${full.node.getAttribute("aria-label")}`);
  assert(dlRow("Lossless PNG").locked, "Lossless PNG is still offered after the lossless base was freed");
  assert(dlRow("Stretched PNG").locked, "Stretched PNG is still offered after the lossless base was freed");
});

test("eight frames back the whole control is locked, with the reason on it", () => {
  // PREVIEW_DISPLAY_KEEP = 8: the ring entry itself is gone, so "Save first
  // light" and FITS 404 too — and neither has a capability flag of its own.
  setLive(28);
  mount(toolbar(frame({ id: 20 })));
  assert(dlButton() == null, "the Download button is still live for a frame the rig has dropped");
  const locked = [...container.querySelectorAll('span[role="button"]')]
    .find((n: any) => /Download/.test(n.textContent || ""));
  assert(locked != null, "no locked Download stand-in rendered");
  assert(/no longer on the rig/.test(locked.getAttribute("aria-label") || ""),
    `the locked Download must name the cause — got ${locked.getAttribute("aria-label")}`);
});

test("the popover is a disclosure, not a menu it cannot implement", () => {
  setLive(20);
  mount(toolbar(frame()));
  click(dlButton());
  assert(container.querySelector('[role="menu"]') == null,
    'the popover still declares role="menu" while implementing none of the menu keyboard model');
  assert(container.querySelector('[role="menuitem"]') == null,
    'rows still declare role="menuitem" — two of them are LockedChips, which are not menuitems');
  assert(container.querySelector('[role="group"]') != null, "the popover has no accessible grouping at all");
});

test("Escape closes the Download popover", () => {
  setLive(20);
  mount(toolbar(frame()));
  click(dlButton());
  assert(container.querySelector('[role="group"]') != null, "the popover did not open, so closing it proves nothing");
  act(() => {
    win.document.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  assert(container.querySelector('[role="group"]') == null,
    "Escape did not close the Download popover — a keyboard user has no way out of it");
});

test("a blocked annotation row states its reason where a finger can see it", () => {
  setLive(20);
  // No tilt data on this frame, so the Tilt row is disabled with a reason.
  mount(toolbar(frame({ tilt: undefined })));
  const picker = [...container.querySelectorAll("button")]
    .find((b: any) => /Annotations/.test(b.getAttribute("aria-label") || ""));
  assert(picker != null, "no Annotations picker rendered");
  click(picker);
  const row = [...container.querySelectorAll('[role="option"]')]
    .find((o: any) => (o.textContent || "").includes("Tilt"));
  assert(row != null, "no Tilt row in the annotations picker");
  assert(row.getAttribute("aria-disabled") === "true",
    "the Tilt row is not disabled on a frame with no tilt data, so this proves nothing");
  click(row);
  const toasts = useStore.getState().toasts;
  assert(toasts.length === 1,
    "tapping a blocked annotation row produced no toast — the reason reaches a mouse hover only");
  assert(/tilt data/i.test(toasts[0].title || ""),
    `the toast must carry the row's own reason — got ${JSON.stringify(toasts[0].title)}`);
});

// ------------------------------------------------------------------- report
act(() => { rootRef!.unmount(); });
const total = passed + failed;
console.log(`previewChromeDom: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total };
