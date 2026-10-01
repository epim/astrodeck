// w5NamingPanelPreview.test.tsx - WP-42 (#278), MOUNTED: the classic Settings
// naming preview must show a GAIN/EXPOSURE/BINNING/SENSORTEMP template's real
// values, not collapse them like the mirror's former unknown-token handling
// did.
//
// w5NamingCaptureTokens.test.ts already pins the pure functions
// (renderTemplatePreview/PREVIEW_SAMPLE) against the server; this file is the
// other half - that NamingPanel.tsx actually WIRES PREVIEW_SAMPLE into the
// preview it renders, rather than keeping its own stale local sample (the
// exact "two un-synced copies" failure #278's "Class" section names). A
// mutant that reverts NamingPanel.tsx to a hand-copied sample missing the
// four tokens is invisible to a test that only imports lib/naming.ts, so this
// one mounts the real component.
//
//   Run directly:  npx tsx src/components/settings/__tests__/w5NamingPanelPreview.test.tsx

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Same minimal shim list as syncPanelDom.test.tsx - NamingPanel does no I/O on
// mount (its only effect re-seeds `draft` from the config template), so no
// fetch stub is needed; Save is never pressed by this file.
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
win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
win.WebSocket = class { close() {} addEventListener() {} send() {} };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "requestAnimationFrame", "cancelAnimationFrame",
  "getComputedStyle", "matchMedia", "ResizeObserver", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;
// NamingPanel never calls fetch, but importing ./../../store pulls in modules
// that reference it lazily; a stub that answers everything keeps a stray call
// from crashing the file instead of a single assertion.
g.fetch = async () => new win.Response(JSON.stringify({}), {
  status: 200, headers: { "content-type": "application/json" },
});

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const NamingPanel = (await import("../NamingPanel")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

function mount(): { host: any; root: any } {
  const host = win.document.createElement("div");
  win.document.body.appendChild(host);
  const root = createRoot(host);
  act(() => { root.render(createElement(NamingPanel)); });
  return { host, root };
}
function unmount(m: { host: any; root: any }): void {
  act(() => { m.root.unmount(); });
  m.host.remove();
}
const typeInto = (el: any, value: string): void => {
  const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!;
  act(() => {
    setter.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
  });
};

// ---------------------------------------------------------------- the tests
test("default-template preview renders (precondition: the panel mounted)", () => {
  const m = mount();
  const preview = m.host.querySelector(".break-all");
  assert(preview != null, "no preview element found - the fixture is wrong, not the component");
  assert(/^captures\//.test(preview.textContent ?? ""), `preview did not start with captures/: ${preview.textContent}`);
  unmount(m);
});

test("a GAIN/EXPOSURE template previews the server's real values, not a collapse (#278)", () => {
  const m = mount();
  const input = m.host.querySelector('[aria-label="File-naming template"]');
  assert(input != null, "no File-naming template input rendered");
  typeInto(input, "$$TARGET$$/g$$GAIN$$_$$EXPOSURE$$s/$$FRAMENR$$");
  const preview = m.host.querySelector(".break-all");
  // #278's measured bug: this used to read "captures/M42/g_s/0001.fits" - both
  // settings silently dropped. The fix must show the actual sample values.
  assert(preview.textContent === "captures/M42/g100_300s/0001.fits",
    `naming preview did not carry GAIN/EXPOSURE through - got "${preview.textContent}"`);
  unmount(m);
});

test("a BINNING/SENSORTEMP template previews real values too", () => {
  const m = mount();
  const input = m.host.querySelector('[aria-label="File-naming template"]');
  typeInto(input, "$$TARGET$$_bin$$BINNING$$_$$SENSORTEMP$$/$$FRAMENR$$");
  const preview = m.host.querySelector(".break-all");
  assert(preview.textContent === "captures/M42_bin1_-10C/0001.fits",
    `naming preview did not carry BINNING/SENSORTEMP through - got "${preview.textContent}"`);
  unmount(m);
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nw5NamingPanelPreview.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
}

export const result = { passed, failed, total };
