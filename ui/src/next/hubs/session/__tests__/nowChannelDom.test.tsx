// nowChannelDom.test.tsx - the channel chip fetches the channel (D-SES-1).
//
//   Run directly:  npx tsx src/next/hubs/session/__tests__/nowChannelDom.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc --noEmit`.
//
// WHAT THIS FILE IS FOR. The strip used to greyscale the colour composite and
// lay a colour over it, so Ha, Oiii and Sii were three labels on ONE picture.
// The whole of D-SES-1 is that the picture CHANGES, and the only way to assert
// that is on the `<img>`'s src - a class, a style or a rendered colour would
// have passed for the tint too. So:
//
//   1. The chips are `status.channels[].channel` and nothing else. A chip built
//      from the wheel's name ("H-alpha", "Red") 404s on the route or, worse,
//      folds onto L and shows L's picture under another label - so the seeded
//      ledger below carries a filter the stack has no channel for and the test
//      asserts no chip appeared for it.
//   2. Tapping Ha puts `channel=Ha` in the image URL.
//   3. The 404 is CAUGHT. An <img> cannot read a status code; the fallback to
//      the composite is only honest if the sentence says so, so both are
//      asserted together.
//   4. A source scan for `grayscale` over the three files, because the tint can
//      come back as one line in a CSS filter string and no behaviour test would
//      see it.
//
// Convention: jsdom by hand, createRoot + act, native events, printed tally plus
// the `{ passed, failed, total }` export (shell-and-tests.md section 4).

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
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {} send() {} addEventListener() {} removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.scrollTo = () => {};

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
]) {
  const v = k === "window" ? win : win[k];
  if (v === undefined) continue;
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- fetch recorder
const asks: string[] = [];

// The stack holds TWO channels. The session plan below adds a third filter the
// stacker has no accumulator for, which is the case the chip list must refuse.
let STACK: any = {
  enabled: true, target: "M31", seq: 7,
  channels: [
    { channel: "Ha", frames: 42, integrated_s: 12_600, rejected: 1 },
    { channel: "Oiii", frames: 18, integrated_s: 5_400, rejected: 0 },
  ],
  frames: 60, integrated_s: 18_000, rejected: 1, mode: "narrowband", downsample: 2,
  has_image: true, render_age_s: 3,
  backfill: {
    running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0,
    channel: "", error: "", started_ts: null, finished_ts: null, available: 0,
  },
};

const SESSION = {
  id: "sess-1", schema_version: 3, name: "M31 SHO",
  created_ts: 1, updated_ts: 2, status: "active",
  nights: ["M31-20260909-210000"], auto_resume: true, origin: "plan", origin_id: "",
  plan: {
    name: "M31 SHO", guide: true, dither_every: 1, dither_pixels: null,
    autofocus_every: 0, cool_to: -10, cool_timeout_s: 600, apply_filter_offsets: null,
    refocus_on_temp_delta_c: null, meridian_flip: true, recover_guiding: null,
    hfr_reject_factor: 1.15, park_when_done: true, warm_cooler_when_done: true,
    count_mode: "attempts",
    targets: [{
      id: "t1", name: "M31", ra_hours: 0.71, dec_deg: 41.2,
      center: true, autofocus_first: true, calibration: false,
      steps: [
        { id: "s-ha", filter: "H-alpha", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 40, frame_type: "Light" },
        { id: "s-o3", filter: "Oiii", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 20, frame_type: "Light" },
        { id: "s-sii", filter: "S2", exposure_s: 300, gain: 100, offset: 30, binning: 1, count: 20, frame_type: "Light" },
      ],
    }],
  },
  frames: [
    { id: "f1", ts: 1, night: "M31-20260909-210000", target_id: "t1", step_id: "s-ha", thumb: null, metrics: {}, auto_accepted: true, override: null },
    { id: "f2", ts: 2, night: "M31-20260909-210000", target_id: "t1", step_id: "s-o3", thumb: null, metrics: {}, auto_accepted: true, override: null },
  ],
};

g.fetch = async (url: any, init: any) => {
  const u = String(url);
  asks.push(`${(init?.method ?? "GET").toUpperCase()} ${u}`);
  const body = u.includes("/api/sequence/stack") ? STACK
    : u.includes("/api/sessions/sess-1") ? SESSION
      : u.includes("/api/sessions") ? { sessions: [] }
        : { ok: true };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => "",
  };
};

// ------------------------------------------ imports, AFTER the globals are set
const { readFileSync } = await import("node:fs");
const { fileURLToPath } = await import("node:url");
const { dirname, join } = await import("node:path");

const { createElement, act, Fragment } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../../store");
const { ChannelStrip, ledgerDiffers, UNANSWERED_LEDGER } = await import("../now/ChannelStrip");
const { LiveStack, channelFallbackNote } = await import("../now/LiveStack");
const { resetStackViewForTests, resetSessionStackStateForTests } = await import("../now/stackView");
const { resetSessionDataForTests } = await import("../now/sessionData");

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function testAsync(name: string, fn: () => Promise<void> | void): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg = ""): void {
  if (got !== want) throw new Error(`${msg} expected ${String(want)}, got ${String(got)}`);
}
const settle = async () => {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
};

const container = win.document.getElementById("root") as any;
const byId = (id: string) => container.querySelector(`[data-testid="${id}"]`) as any;
const imgSrc = (): string => byId("live-stack-image")?.getAttribute("src") ?? "";

const OPERATOR = {
  role: "operator", email: "op@rig",
  caps: ["view.status", "view.preview", "view.site_derived", "control.capture"],
};

useStore.setState({
  principal: OPERATOR,
  equipConnected: true,
  wsPhase: "up",
  wsLastEvent: Date.now(),
  status: { connected: { camera: true }, looping: false },
  sequence: {
    state: "running", target: "M31", plan_name: "M31 SHO", target_index: 0,
    session: { id: "sess-1", name: "M31 SHO", count_mode: "attempts", accepted: 2, target: "M31" },
    progress: { frames_done: 2, frames_total: 80, percent: 3, elapsed_s: 600, rejected: 0 },
  },
} as never);

resetStackViewForTests();
resetSessionStackStateForTests();
resetSessionDataForTests();

const root = createRoot(container);
await act(async () => {
  root.render(createElement(Fragment, null,
    createElement(ChannelStrip),
    createElement(LiveStack)));
});
await settle();

// ------------------------------------------------- 0. the vacuity guard first
// Every assertion below is about something being present, absent or changed on
// a mounted tree. On a blank page most of them would pass by accident.
await testAsync("the strip and the picture mounted at all", async () => {
  assert(byId("now-live-stack") != null,
    "no live stack: the stack fetch or the store seed is broken, not the channel rule");
  assert(byId("now-channel-strip") != null, "no channel strip");
  assert(byId("live-stack-image") != null,
    "no <img>: has_image/frames in the fixture no longer render a picture");
  assert(imgSrc().includes("/api/sequence/stack/preview.jpg"),
    `the picture is not the stack preview: "${imgSrc()}"`);
  assert(asks.some((a) => a.startsWith("GET ") && a.includes("/api/sequence/stack")),
    "the stack status was never fetched, so the chips below are not the seeded ones");
});

// --------------------------------------- 1. the chips are the STACK's channels
await testAsync("the chips are status.channels, not the wheel's filter names", async () => {
  assert(byId("channel-Ha") != null, "no Ha chip for a channel the stack holds");
  assert(byId("channel-Oiii") != null, "no Oiii chip for a channel the stack holds");
  // The plan shoots S2 as well and the stack has no accumulator for it. A chip
  // for it would 404 on the route or fold onto L and show the wrong picture.
  eq(byId("channel-S2"), null, "a chip was built from a wheel name the stack has no channel for");
  eq(byId("channel-SII"), null, "a chip was built from a wheel name the stack has no channel for");
  eq(byId("channel-H-alpha"), null, "a chip was built from the wheel's spelling of Ha");
  const chip = byId("channel-Ha").textContent as string;
  assert(/42/.test(chip), `the Ha chip does not carry the stack's own frame count: "${chip}"`);
});

await testAsync("the ledger's own counts survive, on a line that says which is which", async () => {
  const note = byId("channel-ledger-note");
  assert(note != null,
    "the night's accepted counts vanished with the chips - the ledger line is how they survive");
  const text = note.textContent as string;
  assert(/Chips count what the stack holds/.test(text),
    `the line does not say which count is on the chips: "${text}"`);
  assert(/H-alpha 1/.test(text), `the ledger's own filter names and counts are missing: "${text}"`);
});

await testAsync("identical tallies print once, not twice", () => {
  eq(ledgerDiffers([{ name: "Ha", count: 42 }], [{ name: "H-alpha", count: 42 }]), false,
    "the same fact under two spellings was reported as a disagreement:");
  eq(ledgerDiffers([{ name: "Ha", count: 42 }], [{ name: "H-alpha", count: 40 }]), true,
    "two different counts were reported as agreeing:");
});

// ------------------------------------- 2. THE ONE THAT MATTERS: the src changes
const composite = imgSrc();
await testAsync("the composite URL carries no channel parameter", async () => {
  assert(!composite.includes("channel="),
    `the combined view asked for a channel: "${composite}"`);
});

await testAsync("tapping Ha changes the image URL to that channel", async () => {
  const chip = byId("channel-Ha");
  assert(chip != null, "no Ha chip to tap - the chip list is not the stack's channels");
  await act(async () => {
    chip.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  const src = imgSrc();
  assert(src !== composite,
    "the <img> src did not change: this is exactly what the CSS tint did, and it is the defect D-SES-1 exists to end");
  assert(src.includes("channel=Ha"),
    `the image URL does not ask the server for the Ha accumulator: "${src}"`);
  assert(src.includes("seq=7"), `the cache key was dropped from the URL: "${src}"`);
  const badge = byId("live-stack-mode").textContent as string;
  assert(/Ha only/.test(badge), `the badge does not name the channel on screen: "${badge}"`);
  assert(/42 subs/.test(badge),
    `the badge counts the whole stack instead of this channel: "${badge}"`);
});

// ------------------------------------------------ 3. the 404 an <img> cannot see
await testAsync("onError falls back to the composite AND says so", async () => {
  await act(async () => {
    byId("live-stack-image").dispatchEvent(new win.Event("error"));
  });
  await settle();
  const src = imgSrc();
  eq(src, composite,
    "a refused channel left the picture pointing at a URL the server 404s:");
  const note = byId("channel-missing-note");
  assert(note != null,
    "the composite came back with no sentence: the badge and the picture would then disagree");
  const text = note.textContent as string;
  // AND IT SAYS THE RIGHT THING. `onError` carries no status code: it fires for
  // a channel the stacker has no accumulator for AND for a dropped link, a
  // truncated JPEG or a render that timed out. The stack says Ha holds 42 subs,
  // so "nothing stacked in Ha yet" would be a false statement about the night on
  // the one screen whose job is to say what the night has shot.
  assert(/Could not load the Ha frame/.test(text),
    `an <img> error over 42 banked Ha subs was reported as an empty channel: "${text}"`);
  assert(/42 subs are stacked/.test(text),
    `the sentence drops the count that contradicts "empty": "${text}"`);
  assert(/showing the combined picture\./.test(text),
    `the sentence does not say what is on screen instead: "${text}"`);
  const badge = byId("live-stack-mode").textContent as string;
  assert(!/Ha only/.test(badge),
    `the badge still claims a channel that 404d: "${badge}"`);
});

await testAsync("and an EMPTY channel still reads as empty", () => {
  // The pure half, both branches, because the DOM fixture can only be in one of
  // them at a time and the zero case is the one the sentence was written for.
  eq(channelFallbackNote("Sii", 0),
    "Nothing stacked in Sii yet - showing the combined picture.",
    "a channel with no subs must still say so:");
  assert(/Could not load the Sii frame \(7 subs are stacked\)/
    .test(channelFallbackNote("Sii", 7)),
  `a channel with subs must not claim to be empty: "${channelFallbackNote("Sii", 7)}"`);
});

await testAsync("going back to the composite clears the sentence", async () => {
  await act(async () => {
    byId("channel-combine").dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
  });
  await settle();
  eq(byId("channel-missing-note"), null,
    "the refusal sentence outlived the channel it was about:");
  eq(imgSrc(), composite, "the combined chip did not restore the composite URL:");
});

await act(async () => { root.unmount(); });

// ------------------- 4. the stack switched OFF still shows the night's counts
//
// The counts under the strip are the NIGHT'S accepted subs, read from the
// session's own frames. The stack is a picture somebody chose to build out of
// them. Switching the picture off used to remove the whole strip, so the only
// per-filter tally on the screen went with it - a number the operator reads to
// know whether Oiii is behind Ha tonight, deleted by a display toggle.
STACK = {
  enabled: false, target: "", seq: 0, channels: [],
  frames: 0, integrated_s: 0, rejected: 0, mode: "combined", downsample: 2,
  has_image: false, render_age_s: 0,
  backfill: {
    running: false, total: 0, done: 0, added: 0, skipped: 0, failed: 0,
    channel: "", error: "", started_ts: null, finished_ts: null, available: 0,
  },
};
resetStackViewForTests();
resetSessionStackStateForTests();
resetSessionDataForTests();
const offRoot = createRoot(container);
await act(async () => { offRoot.render(createElement(ChannelStrip)); });
await settle();

await testAsync("the stack is off and the fixture says so - the vacuity guard", async () => {
  eq(byId("channel-Ha"), null,
    "a channel chip is still on screen, so the OFF fixture never reached the strip and "
    + "the assertions below would be about the ON case");
  eq(byId("now-stretch"), null,
    "the stretch control is still rendered for a stack that is switched off");
});

await testAsync("the night's per-filter counts survive the stack being switched off", async () => {
  const note = byId("channel-ledger-note");
  assert(note != null,
    "the ledger line went away with the chips: turning the live stack off deleted the "
    + "only per-filter tally of what this night has actually accepted");
  const text = note.textContent as string;
  assert(/H-alpha 1/.test(text) && /Oiii 1/.test(text) && /S2 0/.test(text),
    `the line does not carry the night's own filter names and counts: "${text}"`);
  assert(/Live stack off/.test(text),
    `the counts are printed with no word about why the chips are gone: "${text}"`);
  assert(!/Chips count/.test(text),
    `the line still refers to chips that are not rendered: "${text}"`);
});

await act(async () => { offRoot.unmount(); });

// ------------------------- 5. before the first answer, nothing claims "off"
//
// `status == null` was read as "the live stack is switched off" - and it is also
// the state before the first `GET /api/sequence/stack` returns, and the state
// after that poll FAILS. So a slow or unreachable rig had the screen state, as a
// fact, that it was not building a picture.
//
// SABOTAGE: drop `answered` from `stackView.ts` (or read `status?.enabled`
// alone again) and both assertions below go red.
const HANGS = new Promise<never>(() => { /* the rig never answers */ });
g.fetch = async (url: any, init: any) => {
  const u = String(url);
  asks.push(`${(init?.method ?? "GET").toUpperCase()} ${u}`);
  if (u.includes("/api/sequence/stack")) return HANGS;
  const body = u.includes("/api/sessions/sess-1") ? SESSION
    : u.includes("/api/sessions") ? { sessions: [] }
      : { ok: true };
  return {
    ok: true, status: 200, statusText: "OK",
    headers: { get: () => "application/json" },
    json: async () => body,
    text: async () => "",
  };
};

resetStackViewForTests();
resetSessionStackStateForTests();
resetSessionDataForTests();
const quietRoot = createRoot(container);
await act(async () => {
  quietRoot.render(createElement(Fragment, null,
    createElement(ChannelStrip),
    createElement(LiveStack)));
});
await settle();

await testAsync("the unanswered fixture really is unanswered - the vacuity guard", async () => {
  assert(byId("now-live-stack") != null, "the picture panel never mounted");
  eq(byId("live-stack-image"), null,
    "there is a picture on screen, so the stack DID answer and the two assertions "
    + "below would be about the answered case");
  assert(byId("now-channel-strip") != null,
    "the strip is absent, so the ledger line below cannot be read");
});

await testAsync('an unanswered stack is not reported as "off"', async () => {
  const note = byId("channel-ledger-note");
  assert(note != null, "no ledger line at all");
  const text = note.textContent as string;
  assert(text.includes(UNANSWERED_LEDGER),
    `the strip does not say the stack has not answered: "${text}"`);
  assert(!/Live stack off/.test(text),
    `the strip told the operator the stack is OFF before the rig said anything: "${text}"`);
  assert(/H-alpha 1/.test(text),
    `the night's own counts went missing while the stack was unknown: "${text}"`);
});

await testAsync("and the picture panel says it is still reading, not that it is off",
  async () => {
    const empty = byId("live-stack-empty");
    assert(empty != null, "the empty face never rendered");
    const text = empty.textContent as string;
    assert(/READING THE STACK/.test(text),
      `the panel claims a state the rig has not reported: "${text}"`);
    assert(!/LIVE STACK IS OFF/.test(text),
      `the panel declared the stack off before the first status landed: "${text}"`);
    const start = byId("live-stack-start");
    assert(start != null, "START must still be rendered, never removed");
    eq(start.getAttribute("aria-disabled"), "true",
      "START is live while it is unknown whether a stack is already running");
    assert(/has not said yet/.test(start.getAttribute("title") ?? ""),
      `START's reason does not say what is unknown: "${start.getAttribute("title")}"`);
  });

await act(async () => { quietRoot.unmount(); });

// ------------------------------------------------------- 6. the tint cannot return
await testAsync("no greyscale and no blend layer anywhere in the three files", () => {
  const here = dirname(fileURLToPath(import.meta.url));
  for (const name of ["stackView.ts", "ChannelStrip.tsx", "LiveStack.tsx"]) {
    const src = readFileSync(join(here, "..", "now", name), "utf8");
    const code = src.split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
    assert(!/grayscale/i.test(code),
      `${name} greyscales the image again: the tint is back and every chip shows one picture`);
    assert(!/mixBlendMode/.test(code),
      `${name} lays a colour over the image again: that is the tint, under another name`);
  }
});

const total = passed + failed;
console.log(`nowChannelDom.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
