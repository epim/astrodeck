// guideDrawerSave.test.tsx — the Guide Tuning drawer's Save, MOUNTED, against a
// rig whose guide block holds settings this screen never shows.
//
//   Run directly:  npx tsx src/views/__tests__/guideDrawerSave.test.tsx
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT WENT WRONG. `PUT /api/guide/settings` takes a whole pydantic
// `GuideConfig` (server/astrodeck/api/app.py:6348) and `ConfigStore.set_guide`
// assigns it wholesale (`cfg.guide = guide`, server/astrodeck/config.py:2069).
// It is a REPLACE, not a patch. The drawer's Save PUT six fields — the two
// algorithms, their params, the Dec direction and the backlash pulse — so every
// other field of the block went back to its pydantic default the moment anybody
// changed a guide algorithm:
//
//     dither_pixels 7 -> 3.0        recover_guiding -> true
//     exposure_s -> 2.0             gain -> 100        binning -> 1
//     offset -> 30                  recalibrate_after_pier_change -> true
//     flip_requires_dec_flip -> false   relock_limit -> 3
//
// None of those are rendered on this screen, so nothing on it changed and no
// toast said anything: the next dither was 3 px instead of 7, and the guide
// camera went back to a 2 s exposure at gain 100 in the middle of a night.
//
// WHY IT IS TESTED HERE AND NOT IN THE LIBRARY. There is no pure function to
// call: the body is assembled inside the component from state seeded by a GET,
// and the two paths that reach Save disagree about whether that GET has
// happened — "Open in tuning editor" hands the drawer a settings body straight
// from the Guiding Assistant and marks it loaded WITHOUT reading the rig. So
// both are driven through the real DOM, and the assertion is on the bytes the
// component handed to `fetch`.
//
// The loop over "every field that is not one of the six" is deliberate: naming
// only `dither_pixels` would pass again the day a new field is appended to
// `GuideConfig` and this drawer starts flattening that one instead.

/* eslint-disable @typescript-eslint/no-explicit-any */

// ---------------------------------------------------------------- jsdom first
// Installed BEFORE the store or the view are imported: api.ts reads
// window.location at module scope and the store reads localStorage/matchMedia.
// run-tests.mjs gives this file its own process, so planting globals here cannot
// leak into another suite.
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
// The hand-off path scrolls the drawer into view; jsdom implements no layout, so
// the method simply does not exist and the effect would throw before Save is
// reachable. Same for focus({preventScroll}), which jsdom accepts and ignores.
win.HTMLElement.prototype.scrollIntoView = function scrollIntoView() {};

// ------------------------------------------------------------- the fake rig
type Call = { method: string; url: string; body: any };
const calls: Call[] = [];
type Reply = { status?: number; body?: unknown };
let answer: (c: Call) => Reply = () => ({ body: {} });

const fakeFetch = async (input: any, init: any) => {
  const call: Call = {
    method: init?.method ?? "GET",
    url: String(input),
    body: init?.body ? JSON.parse(init.body) : undefined,
  };
  calls.push(call);
  const r = answer(call);
  const status = r.status ?? 200;
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "error",
    json: async () => r.body ?? {},
  } as any;
};

const hits = (method: string, path: string) =>
  calls.filter((c) => c.method === method && c.url.endsWith(path)).length;
const lastCall = (method: string, path: string) =>
  [...calls].reverse().find((c) => c.method === method && c.url.endsWith(path));

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

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// ------------------------------------------------------------- the fixtures
//
// The rig's persisted `guide` block, as `GET /api/guide/settings` sends it
// (`config_store.cfg().guide.model_dump()` — the whole model). EVERY value here
// is deliberately NOT the pydantic default, so a field that survives can only
// have survived by being carried; a reset to the default is visible as such.
const SAVED: Record<string, any> = {
  // the six the drawer edits
  ra_algorithm: "hysteresis",
  dec_algorithm: "resist_switch",
  dec_guide_mode: "north",
  blc_pulse_ms: 0,
  ra_params: { min_move: 0.35, aggression: 0.42, hysteresis: null },
  dec_params: { min_move: 0.28, aggression: 1.4 },
  // ...and everything else in the block, which it does not render at all
  dither_pixels: 7,                       // default 3.0
  recover_guiding: false,                 // default true
  exposure_s: 3.5,                        // default 2.0
  gain: 250,                              // default 100
  binning: 2,                             // default 1
  offset: 42,                             // default 30
  recalibrate_after_pier_change: false,   // default true
  flip_requires_dec_flip: true,           // default false
  relock_limit: 5,                        // default 3
  relock_window_min: 20,                  // default 10.0
};

/** The six fields Save is ENTITLED to change. Everything else in the block must
 *  come back byte-identical. */
const EDITED = new Set([
  "ra_algorithm", "dec_algorithm", "ra_params", "dec_params",
  "dec_guide_mode", "blc_pulse_ms",
]);
const CARRIED = Object.keys(SAVED).filter((k) => !EDITED.has(k));

/** A Guiding Assistant report in the server's `report_dict` shape, cut to one
 *  non-advanced recommendation — enough for the panel to render its card and
 *  its "Open in tuning editor" hand-off. */
const REPORT = {
  measurements: {
    n: 120, rms_ra_px: 0.4, rms_dec_px: 0.5, rms_total_px: 0.64,
    rms_ra_arcsec: 0.6, rms_dec_arcsec: 0.75, rms_total_arcsec: 0.96,
    drift_per_min_px: 0.8, drift_per_min_arcsec: 1.2,
    pe_amplitude_px: 0.3, pe_period_s: 480, jitter_px: 0.2,
    image_scale_arcsec: 1.5, image_scale_known: true,
    backlash: {
      bl_px: 0.9, bl_ms: 420, sigma_ms: 40, north_rate: 0.002,
      result_code: "VALID", y_rate_source: "calibration", halted: false,
      measured: true,
    },
  },
  recommendations: [{
    key: "ra_min_move", field: "ra_min_move", current: 0.2, recommended: 0.31,
    unit: " px", rationale: "seeing jitter is 0.2 px", confidence: "high",
    advanced: false,
  }],
  current: {
    ra_algorithm: "hysteresis", dec_algorithm: "resist_switch",
    dec_guide_mode: "auto", blc_pulse_ms: 0,
    ra_params: { min_move: 0.2 }, dec_params: { min_move: 0.2 },
  },
  polar: { verdict: "polar alignment looks fine", tone: "good", drift_per_min_arcsec: 1.2 },
  samples: [{ t: 0, ra: 0.1, dec: -0.1 }, { t: 2, ra: -0.2, dec: 0.15 }],
};

answer = (c) => {
  if (c.url.endsWith("/api/guide/settings") && c.method === "GET") return { body: SAVED };
  if (c.url.endsWith("/api/guide/calibration") && c.method === "GET") {
    return { body: { report: null } };
  }
  if (c.url.endsWith("/api/guide/assistant/report")) return { body: { report: REPORT } };
  // The PUT answers with the block it persisted, the way the route does.
  if (c.url.endsWith("/api/guide/settings") && c.method === "PUT") return { body: c.body };
  return { body: {} };
};

const toasts: { level: string; msg: string }[] = [];

/** A 2s status frame: an idle, connected native guider and an operator who may
 *  control guiding (without `control.guide` the drawer renders read-only chips
 *  and Save is never reachable). */
function seed() {
  useStore.setState({
    status: {
      connected: {}, looping: false, mode: "sim", busy_lanes: [],
      providers: { guide: { kind: "astrodeck" } },
      guider: {
        name: "native", guiding: false, phase: "idle",
        rms_ra: 0.4, rms_dec: 0.5, rms_total: 0.64, snr: 30,
        recent: [{ t: 0, ra: 0.1, dec: 0.1 }], is_arcsec: true, image_scale: 1.5,
      },
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.guide"] },
    guideAssistant: null,
    showToast: (level: string, msg: string) => { toasts.push({ level, msg }); },
  } as never);
}

async function flush(): Promise<void> {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

function mount() {
  const div = win.document.createElement("div");
  win.document.body.appendChild(div);
  const root = createRoot(div);
  act(() => { root.render(createElement(GuideView)); });
  return { container: div as any, root };
}

const btns = (c: any) => [...c.querySelectorAll("button")] as any[];
const button = (c: any, re: RegExp) =>
  btns(c).find((b) => re.test((b.textContent || "").trim()));
const click = async (node: any) => {
  await act(async () => {
    node.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    await Promise.resolve();
  });
  await flush();
};
/** The <input> under the label whose first <span> reads exactly `name`. */
const fieldByLabel = (c: any, name: string) =>
  ([...c.querySelectorAll("label")] as any[])
    .find((l) => (l.querySelector("span")?.textContent || "").trim() === name)
    ?.querySelector("input") ?? null;
/** Type into a React-controlled input: React tracks its own value, so the
 *  native descriptor setter has to be used before the input event. */
const type = async (el: any, value: string) => {
  await act(async () => {
    Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, "value")!
      .set!.call(el, value);
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
    await Promise.resolve();
  });
  await flush();
};

/** The assertion this file exists for: everything outside the six edited fields
 *  came back exactly as the rig had it. */
function assertBlockSurvived(body: any, where: string): void {
  assert(body != null, `${where}: no PUT body at all`);
  for (const k of CARRIED) {
    assert(JSON.stringify(body[k]) === JSON.stringify(SAVED[k]),
      `${where}: ${k} was PUT as ${JSON.stringify(body[k])}, not the rig's ` +
      `${JSON.stringify(SAVED[k])} — the whole-block PUT reset it to the ` +
      "GuideConfig default");
  }
}

// ================================================== 1. the drawer's own Save
seed();
const A = mount();
await flush();

await test("precondition: the fixture block has fields this drawer never renders", () => {
  // Without this, the loop above could be empty and every assertion below would
  // pass against a body carrying nothing at all.
  assert(CARRIED.length >= 8,
    `only ${CARRIED.length} carried fields in the fixture — the check is toothless`);
  assert(SAVED.dither_pixels === 7, "the fixture no longer seeds dither_pixels: 7");
});

await test("the tuning drawer opens and reads the rig's block", async () => {
  const edit = button(A.container, /^Edit$/);
  assert(edit != null, "no Edit button — the Guide Tuning panel did not render");
  await click(edit);
  assert(hits("GET", "/api/guide/settings") === 1,
    "opening the drawer did not read the saved settings");
  assert(fieldByLabel(A.container, "Dec backlash pulse (ms)") != null,
    "the tuning editor did not open — the Save below would be pressing nothing");
});

await test("Save carries the whole block, not the six fields it edits", async () => {
  const blc = fieldByLabel(A.container, "Dec backlash pulse (ms)");
  await type(blc, "250");
  assert(blc.value === "250", `the edit did not stick: input reads ${blc.value}`);

  const before = hits("PUT", "/api/guide/settings");
  await click(button(A.container, /^Save$/));
  assert(hits("PUT", "/api/guide/settings") === before + 1,
    "Save did not PUT — every assertion below would be reading a stale body");

  const put = lastCall("PUT", "/api/guide/settings")!;
  // The positive control FIRST: if the edit never reached the wire, "everything
  // else survived" would be true of a body this drawer never really built.
  assert(put.body?.blc_pulse_ms === 250,
    `the edited field did not reach the rig: blc_pulse_ms is ` +
    `${JSON.stringify(put.body?.blc_pulse_ms)}, not 250`);
  assert(put.body?.dither_pixels === 7,
    `dither_pixels was PUT as ${JSON.stringify(put.body?.dither_pixels)} — a rig ` +
    "dithering 7 px goes back to the 3.0 default because a guide algorithm was saved");
  assertBlockSurvived(put.body, "drawer Save");
});

await test("a second Save in the same session still carries it", async () => {
  // The drawer keeps the block the PUT answered with. If it kept the pre-save
  // copy — or nothing — the second write is where the loss reappears.
  const blc = fieldByLabel(A.container, "Dec backlash pulse (ms)");
  await type(blc, "300");
  await click(button(A.container, /^Save$/));
  const put = lastCall("PUT", "/api/guide/settings")!;
  assert(put.body?.blc_pulse_ms === 300, "the second edit never reached the rig");
  assertBlockSurvived(put.body, "second Save");
});

act(() => { A.root.unmount(); });

// ================================ 2. the hand-off that never read the rig
// "Open in tuning editor" fills the drawer from the Guiding Assistant's
// recommendation and marks it loaded, so Save is live on a component that has
// issued no GET at all. That is the path where a body assembled from local
// state only would still be partial.
seed();
const B = mount();
await flush();
await act(async () => {
  useStore.setState({
    guideAssistant: { phase: "done", pct: 100, message: "Recommended settings are ready." },
  } as never);
});
await flush();

await test("the assistant report rendered — the hand-off is reachable", async () => {
  assert(hits("GET", "/api/guide/assistant/report") >= 1, "no report was fetched");
  const adv = button(B.container, /^(Show|Hide) the advanced/) ??
    btns(B.container).find((b) => /Advanced/.test(b.textContent || ""));
  assert(adv != null, "no Advanced disclosure in the Guiding Assistant panel");
  await click(adv);
  assert(button(B.container, /^Open in tuning editor$/) != null,
    "no hand-off button — the case below would test nothing");
});

await test("Save after 'Open in tuning editor' still carries the unread block", async () => {
  const getsBefore = hits("GET", "/api/guide/settings");
  await click(button(B.container, /^Open in tuning editor$/));
  const save = button(B.container, /^Save$/);
  assert(save != null, "the drawer did not open on the hand-off");
  assert(save.getAttribute("aria-disabled") !== "true",
    "Save is held back on the hand-off path — this case is about a Save that IS " +
    "reachable without a GET");

  const putsBefore = hits("PUT", "/api/guide/settings");
  await click(save);
  assert(hits("PUT", "/api/guide/settings") === putsBefore + 1, "the hand-off Save did not PUT");
  assert(hits("GET", "/api/guide/settings") > getsBefore,
    "Save wrote the block without ever reading it — whatever it sent was assembled " +
    "from local state alone");
  assertBlockSurvived(lastCall("PUT", "/api/guide/settings")!.body, "hand-off Save");
});

await test("Applying a recommendation carries it too", async () => {
  // Same route, same whole-block replace: `buildApplyBody` is six fields, and
  // this is the button a tired operator actually presses.
  const apply = button(B.container, /^Apply recommended settings$/) ??
    button(B.container, /^Apply selected$/);
  assert(apply != null, "no Apply button in the Guiding Assistant panel");
  const putsBefore = hits("PUT", "/api/guide/settings");
  await click(apply);
  assert(hits("PUT", "/api/guide/settings") === putsBefore + 1, "Apply did not PUT");
  assertBlockSurvived(lastCall("PUT", "/api/guide/settings")!.body, "assistant Apply");
});

act(() => { B.root.unmount(); });

const total = passed + failed;
console.log(`guideDrawerSave.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
