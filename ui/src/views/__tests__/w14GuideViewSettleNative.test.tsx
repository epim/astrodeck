// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w14GuideViewSettleNative.test.tsx - the CLASSIC Guide view's settle boxes on
// the native guider (#679, backlog WP-94). The classic copy of the defect the
// Rig > Guider sheet had (w14GuiderSettleNative.test.tsx owns that one): three
// settle boxes (local state, read only by the DITHER press) whatever the guide
// provider, although the native engine reads the timeout alone.
//
//   Run directly:  npx tsx src/views/__tests__/w14GuideViewSettleNative.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// Same rule as the sheet, from the same module (next/hubs/rig/lib/guideSettle):
// lock "settle px" and "settle s" with the reason ONLY when the provider is
// KNOWN native, show the active provider's default as the placeholder, and send
// no settle distance or time from DITHER on native.
//
// Mutants (a byte backup of GuideView.tsx inside this worktree, restored
// byte-identically after each; failures quoted from the run):
//   M6  `const nativeSettle = settleIgnoredByGuider(providers?.guide?.kind)`
//       -> `const nativeSettle = false`:
//         RED  native: settle px and settle s are locked with the reason:
//         native: "settle px" is not locked (expected "true", got null)
//   M7  `...ditherSettleBody(nativeSettle,` -> `...ditherSettleBody(false,`:
//         RED  native: Dither sends no settle distance or time, even ones
//         typed before the status said native: Dither on native still sent
//         settle_pixels (expected undefined, got 2.5)
//   M2b guideSettle.ts `settleIgnoredByGuider` also true for an undefined kind:
//         RED  provider unknown: [data-testid="guide-settle-px"] is locked
//         while the provider is unknown (expected false, got true)

/* eslint-disable @typescript-eslint/no-explicit-any */

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

type Call = { method: string; url: string; body: any };
const calls: Call[] = [];
const fakeFetch = async (input: any, init: any) => {
  calls.push({
    method: init?.method ?? "GET",
    url: String(input),
    body: init?.body ? JSON.parse(init.body) : undefined,
  });
  const url = String(input);
  const body = url.endsWith("/api/guide/calibration") ? { report: null } : {};
  return { ok: true, status: 200, statusText: "OK", json: async () => body } as any;
};
const dithers = () => calls.filter((c) => c.method === "POST" && c.url.endsWith("/api/guide/dither"));

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement", "Element",
  "Node", "Event", "CustomEvent", "MouseEvent", "localStorage",
  "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
  "matchMedia", "WebSocket",
]) {
  const v = k === "window" ? win : win[k];
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
Object.defineProperty(g, "fetch", { value: fakeFetch, writable: true, configurable: true });
g.IS_REACT_ACT_ENVIRONMENT = true;

const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const GuideView = (await import("../GuideView")).default;
const { NATIVE_SETTLE_REASON } = await import("../../next/hubs/rig/lib/guideSettle");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${JSON.stringify(want)}, got ${JSON.stringify(got)})`);
}

/** `providers: undefined` is a status frame that carries no `providers` yet. */
function seed(providers: { kind: string; label: string } | undefined): void {
  useStore.setState({
    status: {
      connected: {}, looping: false, mode: "sim", busy_lanes: [],
      ...(providers ? { providers: { guide: providers } } : {}),
      guider: {
        name: "guider", guiding: true, phase: "guiding",
        rms_ra: 0.4, rms_dec: 0.5, rms_total: 0.64, snr: 30,
        recent: [{ t: 0, ra: 0.1, dec: 0.1 }], is_arcsec: true, image_scale: 1.5,
      },
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.guide"] },
    showToast: () => {},
  } as never);
}

async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

let current: { container: any; root: any } | null = null;
async function open(providers: { kind: string; label: string } | undefined): Promise<any> {
  if (current) { act(() => { current!.root.unmount(); }); current.container.remove(); }
  seed(providers);
  const div = win.document.createElement("div");
  win.document.body.appendChild(div);
  const root = createRoot(div);
  act(() => { root.render(createElement(GuideView)); });
  current = { container: div, root };
  await flush();
  calls.length = 0;
  const c = div as any;
  // The anti-blank-page guard: every assertion is about one box.
  assert(c.querySelector('[data-testid="guide-settle-px"]') != null,
    "the classic Guide view did not render its settle boxes at all");
  return c;
}
const PX = '[data-testid="guide-settle-px"]';
const S = '[data-testid="guide-settle-s"]';
const TO = '[data-testid="guide-settle-timeout"]';
const setValue = (input: any, v: string) => {
  Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!.set!.call(input, v);
  act(() => { input.dispatchEvent(new win.Event("input", { bubbles: true })); });
};
const dither = async (c: any) => {
  const b = [...c.querySelectorAll("button")].find((x: any) => (x.textContent || "").trim() === "Dither") as any;
  assert(b != null, "no Dither button rendered");
  await act(async () => {
    b.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await Promise.resolve();
  });
  await flush();
};
const locked = (input: any) => input.getAttribute("aria-disabled") === "true" && input.readOnly === true;

const NATIVE = { kind: "astrodeck", label: "AstroDeck native" };
const PHD2 = { kind: "backend", label: "PHD2" };

// ======================================================================= cases
await test("native: settle px and settle s are locked with the reason, timeout s is not", async () => {
  const c = await open(NATIVE);
  for (const [name, sel] of [["settle px", PX], ["settle s", S]] as const) {
    const box = c.querySelector(sel);
    eq(box.getAttribute("aria-disabled"), "true", `native: "${name}" is not locked`);
    eq(box.readOnly, true, `native: "${name}" accepts typing`);
    eq(box.title, NATIVE_SETTLE_REASON, `native: "${name}" does not carry the reason`);
    setValue(box, "9");
    eq(box.value, "", `typing into the locked "${name}" box changed what it shows`);
  }
  assert(!locked(c.querySelector(TO)), "native: timeout s is locked, but it is the one box the native dither honours");
  assert((c.textContent || "").includes(NATIVE_SETTLE_REASON),
    "the reason is only in a title, which never fires on touch: it must be on screen");
  eq(c.querySelector(PX).placeholder, "1.5", "native settle px placeholder");
  eq(c.querySelector(S).placeholder, "10", "native settle s placeholder");
  eq(c.querySelector(TO).placeholder, "60", "native timeout s placeholder");
});

await test("native: Dither sends no settle distance or time, even ones typed before the status said native", async () => {
  // The boxes are LOCAL state, so a locked box cannot be typed into: the only
  // way a settle distance or time is in state on a native rig is that it was
  // typed while the provider was still unknown (the first status frame with
  // `providers` had not landed). It stays in the box, and must not ride the
  // request. Timeout is the one that does.
  const c = await open(undefined);
  setValue(c.querySelector(PX), "2.5");
  setValue(c.querySelector(S), "7");
  setValue(c.querySelector(TO), "30");
  await act(async () => { seed(NATIVE); });
  await flush();
  assert(locked(c.querySelector(PX)), "precondition: the status turning native did not lock settle px");
  eq(c.querySelector(PX).value, "2.5", "locking cleared what had been typed");
  await dither(c);
  eq(dithers().length, 1, "Dither did not POST /api/guide/dither");
  const body = dithers()[0].body;
  eq(body.settle_pixels, undefined, "Dither on native still sent settle_pixels");
  eq(body.settle_time_s, undefined, "Dither on native still sent settle_time_s");
  eq(body.settle_timeout_s, 30, "Dither on native dropped the timeout");
});

await test("PHD2: nothing is locked, placeholders 1.5 / 8 / 60, and Dither sends all three", async () => {
  const c = await open(PHD2);
  for (const sel of [PX, S, TO]) assert(!locked(c.querySelector(sel)), `PHD2: ${sel} is locked`);
  eq(c.querySelector(PX).placeholder, "1.5", "PHD2 settle px placeholder");
  eq(c.querySelector(S).placeholder, "8", "PHD2 settle s placeholder");
  eq(c.querySelector(TO).placeholder, "60", "PHD2 timeout s placeholder");
  assert(!(c.textContent || "").includes(NATIVE_SETTLE_REASON), "PHD2 shows the native lock reason");
  setValue(c.querySelector(PX), "2.5");
  setValue(c.querySelector(S), "7");
  setValue(c.querySelector(TO), "40");
  await dither(c);
  const body = dithers()[0].body;
  eq(body.settle_pixels, 2.5, "PHD2's Dither lost settle_pixels");
  eq(body.settle_time_s, 7, "PHD2's Dither lost settle_time_s");
  eq(body.settle_timeout_s, 40, "PHD2's Dither lost settle_timeout_s");
});

await test("provider unknown (no providers on the frame yet): the boxes stay editable", async () => {
  const c = await open(undefined);
  for (const sel of [PX, S, TO]) {
    eq(locked(c.querySelector(sel)), false, `${sel} is locked while the provider is unknown`);
  }
  const px = c.querySelector(PX);
  setValue(px, "2");
  eq(px.value, "2", "an editable box refused typing");
});

if (current) act(() => { current!.root.unmount(); });

const total = passed + failed;
console.log(`w14GuideViewSettleNative.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
