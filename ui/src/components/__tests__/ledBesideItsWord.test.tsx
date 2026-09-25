// ledBesideItsWord.test.tsx - an LED printed beside a word that already says
// its state is heard as nothing, so the state is heard once (#231; the class
// of #206 and #217).
//
//   Run:  node --import ./test-css-stub.mjs --import tsx src/components/__tests__/ledBesideItsWord.test.tsx   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// WHAT IS WORTH GUARDING HERE
//
// `Led` (components/ui.tsx) draws a labelled LED as `<span role="img"
// aria-label=L>` and an unlabelled one `aria-hidden`. A screen reader reading
// in order announces the labelled image by L and then reads the text printed
// beside it. #231 found eleven places where that text already says L, or
// nearly: the polar verdict and its "measuring" row, the cooler word on the
// Monitor's thermometer and on Capture, the two App banners, the Monitor's
// RUN ARMED, both kinds of Link Status row, the active profile and the guide
// calibration. Each said its state twice to the ear while the page showed it
// once. The fix takes the label off; the word beside the LED is the name.
//
//   1. THE STATE IS HEARD ONCE AND STILL PRINTED, at every one of the eleven
//      sites, in every state each site can show. Both halves, as in #217:
//      "heard once" alone passes a fix that deletes the printed word and keeps
//      the label (a sighted reader gets an LED's colour and shape and no
//      word), and "printed once" alone passes the bug.
//   2. WHERE THE ROW HOLDS NOTHING BUT THE LED AND ITS TEXT, IT IS HEARD AS
//      EXACTLY WHAT IT PRINTS. Two sites paraphrase rather than repeat: the
//      guide calibration's warn state ("check calibration" beside
//      "Non-orthogonal - verify") and the polar busy LED, which said
//      "measuring" beside "Waiting for solve..." when nothing was being
//      measured yet. A word count cannot see a paraphrase; this can. Where
//      the row also holds controls (the App banners, a profile card), it
//      cannot be held to what it prints, so the LED itself must not be heard.
//   3. A SOURCE GUARD flags any `<Led label=...>` whose label repeats text
//      printed beside it, over every .tsx file in ui/src, so the twelfth site
//      is caught when it is written. It catches exact repeats only (the
//      issue's own limit): the guide calibration's "calibrated" beside "Good
//      calibration" is beyond it, and case 1 holds that site.
//   4. CONTROLS. The guard does not flag FlowNodeCard.tsx or
//      RestrictedAssetsPanel.tsx, where the label is the only place the word
//      appears and so is what the label is for. The Led primitive still names
//      a labelled LED and hides an unlabelled one, which is the mechanism the
//      fix relies on. An inactive profile, which never had a label, still
//      says nothing about being active.
//
// NOT HEARD ON A SCREEN READER. Everything here holds the markup to a model of
// the accname rules (the #217 helper's model, widened to whole regions). Nobody
// has listened to any of these screens on NVDA or on VoiceOver, before or
// after the change; that is the operator's step, as it was for #206 and #217.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced, run from a byte-for-byte backup of the file it edits.

/* eslint-disable @typescript-eslint/no-explicit-any */

import ts from "typescript";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

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
// One socket that never opens: App's connectWs() runs for real and hears
// nothing, so the store holds exactly what each case seeds.
win.WebSocket = class {
  static OPEN = 1;
  readyState = 0;
  close() {}
  send() {}
  addEventListener() {}
  removeEventListener() {}
};
win.Element.prototype.setPointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.releasePointerCapture = function () { /* jsdom has none */ };
win.Element.prototype.hasPointerCapture = function () { return false; };
win.HTMLElement.prototype.scrollIntoView = function () { /* jsdom has none */ };
// jsdom has no canvas; the screens mounted here draw charts and skip a null
// context. Answering null here keeps jsdom's "not implemented" off the output.
win.HTMLCanvasElement.prototype.getContext = function () { return null; };

const g = globalThis as any;
for (const k of [
  "window", "document", "navigator", "HTMLElement", "HTMLInputElement",
  "Element", "Node", "Event", "CustomEvent", "MouseEvent", "KeyboardEvent",
  "localStorage", "sessionStorage", "getComputedStyle", "matchMedia", "WebSocket",
  "requestAnimationFrame", "cancelAnimationFrame", "location", "history",
  "ResizeObserver", "IntersectionObserver",
]) {
  const v = k === "window" ? win
    : k === "ResizeObserver" || k === "IntersectionObserver"
      ? class { observe() {} unobserve() {} disconnect() {} }
      : win[k];
  // Node >=21 defines `navigator` as a getter-only global, so a plain
  // assignment throws; defineProperty works for every key.
  Object.defineProperty(g, k, { value: v, writable: true, configurable: true });
}
g.IS_REACT_ACT_ENVIRONMENT = true;

// ------------------------------------------------------------- stub server
// Every GET answers 404 unless a case routes it. Every loader these screens
// run swallows a failure and keeps the seeded store value, so what each case
// seeds is what the screen shows, not a race with the network. The routed
// paths are the two a screen cannot render the site without: the profile rows
// and the guider's calibration report.
const routes = new Map<string, unknown>();
g.fetch = async (url: any, _init?: any) => {
  const path = String(url).replace(/^[a-z]+:\/\/[^/]+/i, "").split("?")[0];
  const body = routes.get(path);
  if (body !== undefined) {
    return {
      ok: true, status: 200, statusText: "OK",
      headers: { get: () => "application/json" },
      json: async () => body, text: async () => JSON.stringify(body),
    };
  }
  return {
    ok: false, status: 404, statusText: "Not Found",
    headers: { get: () => "application/json" },
    json: async () => ({}), text: async () => "",
  };
};

// ------------------------------------------------------------------- imports
const { createElement, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../store");
const { Led } = await import("../ui");
const { ThermometerBar } = await import("../monitor");
const BackendLinkGrid = (await import("../settings/BackendLinkGrid")).default;
const ProfileList = (await import("../settings/ProfileList")).default;
const PolarView = (await import("../../views/PolarView")).default;
const CaptureView = (await import("../../views/CaptureView")).default;
const MonitorView = (await import("../../views/MonitorView")).default;
const GuideView = (await import("../../views/GuideView")).default;
const App = (await import("../../App")).default;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: unknown, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

/** Let React run effects and the promise chains they start (every loader on
 *  these screens goes through the async api client). */
async function flush(): Promise<void> {
  for (let i = 0; i < 4; i++) {
    await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  }
}

/** Mount `el` into a fresh box, and hand back the box and its unmount. Every
 *  case unmounts what it mounted, so no screen's polls outlive its case. */
async function mount(el: any): Promise<{ box: HTMLElement; done: () => void }> {
  const box = document.createElement("div");
  document.body.appendChild(box);
  const root = createRoot(box);
  await act(async () => { root.render(el); });
  await flush();
  return {
    box,
    done: () => { act(() => { root.unmount(); }); box.remove(); },
  };
}

async function seed(state: Record<string, unknown>): Promise<void> {
  await act(async () => { useStore.setState(state as never); });
  await flush();
}

// ------------------------------------------- heard and seen text, minimally
// WHAT IS HEARD. A screen reader reading a region in order says each text node,
// skips a subtree hidden from it (`aria-hidden`, and at phone width anything
// `display:none` or `visibility:hidden`: the `hidden` attribute and the
// `hidden` / `invisible` utilities), and says an element's aria-label in place
// of its content. That last rule is the whole defect: a labelled LED is a
// role="img" whose aria-label stands in for it, so its label is said, and then
// the word printed beside it. The same model as unreadableFlowCard.test.tsx's
// `contentText` (#217), applied to a region in reading order rather than to a
// control's name, and not a general accname implementation.
//
// WHAT IS SEEN. The same walk for the eye: the `hidden` attribute and the
// `sr-only`, `hidden` and `invisible` utilities are dropped, and so is an SVG
// <title>, which is never painted. `aria-hidden` is NOT dropped: it hides from
// the ear, not the eye. jsdom has no stylesheet here (the CSS is stubbed), so
// this reads the markup this codebase hides things with.
const HIDDEN_AT_PHONE = ["hidden", "invisible"];
function heardText(el: Element): string {
  let out = "";
  for (const n of Array.from(el.childNodes)) {
    if (n.nodeType === 3) { out += n.textContent ?? ""; continue; }
    if (n.nodeType !== 1) continue;
    const child = n as Element;
    if (child.getAttribute("aria-hidden") === "true" || child.hasAttribute("hidden")) continue;
    if (HIDDEN_AT_PHONE.some((c) => child.classList.contains(c))) continue;
    const label = child.getAttribute("aria-label");
    // Spaces around every element: separate spans are separate words when read.
    out += ` ${label && label.trim() ? label : heardText(child)} `;
  }
  return out;
}
function seenText(el: Element): string {
  let out = "";
  for (const n of Array.from(el.childNodes)) {
    if (n.nodeType === 3) { out += n.textContent ?? ""; continue; }
    if (n.nodeType !== 1) continue;
    const child = n as Element;
    if (child.hasAttribute("hidden") || child.tagName.toLowerCase() === "title") continue;
    if (["sr-only", ...HIDDEN_AT_PHONE].some((c) => child.classList.contains(c))) continue;
    out += ` ${seenText(child)} `;
  }
  return out;
}
/** The two walks above read an element's CONTENT, so they cannot see the
 *  element itself or its ancestors being hidden. A region is heard (or seen)
 *  only when nothing from it up to the document hides it. */
function hiddenFromEar(el: Element | null): boolean {
  for (let e = el; e; e = e.parentElement) {
    if (e.getAttribute("aria-hidden") === "true" || e.hasAttribute("hidden")) return true;
    if (HIDDEN_AT_PHONE.some((c) => e!.classList.contains(c))) return true;
  }
  return false;
}
function hiddenFromEye(el: Element | null): boolean {
  for (let e = el; e; e = e.parentElement) {
    if (e.hasAttribute("hidden")) return true;
    if (["sr-only", ...HIDDEN_AT_PHONE].some((c) => e!.classList.contains(c))) return true;
  }
  return false;
}
const squash = (s: string) => s.replace(/\s+/g, " ").trim();
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** How many times `what` is said in `text`: case-insensitive, whole words, any
 *  run of whitespace between the words of a phrase. Whole words because the
 *  cooler's state word is "on" and "connected" is inside "disconnected";
 *  whitespace-only between words because the banners' Dismiss buttons are
 *  named "Dismiss run-armed banner" and "Dismiss sequence-running banner",
 *  which name what they dismiss and are not a second copy of the state. */
function occurrences(text: string, what: string | RegExp): number {
  const re = typeof what === "string"
    ? new RegExp(
      `(?<![\\p{L}\\p{N}])${what.trim().split(/\s+/).map(escapeRe).join("\\s+")}(?![\\p{L}\\p{N}])`,
      "giu")
    : new RegExp(what.source, what.flags.includes("g") ? what.flags : `${what.flags}g`);
  return (text.match(re) ?? []).length;
}

/** THE LED BESIDE ITS WORD: the innermost element printing exactly `printed`,
 *  widened to the closest ancestor that also holds an LED. That is the region
 *  a screen reader moves through to get from the LED to the word, and no
 *  more, so a word printed elsewhere on the screen is not counted against a
 *  site. Throws, with the count, when the word is not printed exactly once. */
function besideRegion(scope: Element, printed: string): Element {
  const all = [scope, ...Array.from(scope.querySelectorAll("*"))];
  const hits = all.filter((el) => !hiddenFromEye(el) && squash(seenText(el)) === printed
    && !Array.from(el.children).some((c) => squash(seenText(c)) === printed));
  if (hits.length !== 1) {
    throw new Error(`${JSON.stringify(printed)} is printed by ${hits.length} element(s), not 1`
      + " - without the word the state is an LED's colour and shape alone");
  }
  let region: Element | null = hits[0];
  while (region && region !== scope.parentElement && !region.querySelector(".led")) {
    region = region.parentElement;
  }
  if (!region || region === scope.parentElement) {
    throw new Error(`no LED shares a region with ${JSON.stringify(printed)} - this is not the site`);
  }
  return region;
}

type Site = {
  who: string;
  /** The text the site prints beside its LED, exactly. */
  printed: string;
  /** The state word, or the stem for a site whose label only nearly repeats. */
  word: string | RegExp;
  /** The region holds only the LED and its text: it must be heard as printed. */
  exact?: boolean;
};

/** Grade one site; every way it is wrong goes into `wrong`, so a case reports
 *  all its sites at once rather than the first. */
function checkSite(scope: Element, s: Site, wrong: string[]): void {
  let region: Element;
  try { region = besideRegion(scope, s.printed); } catch (e) {
    wrong.push(`${s.who}: ${(e as Error).message}`);
    return;
  }
  const heard = hiddenFromEar(region) ? "" : squash(heardText(region));
  const seen = hiddenFromEye(region) ? "" : squash(seenText(region));
  const word = typeof s.word === "string" ? JSON.stringify(s.word) : String(s.word);
  const h = occurrences(heard, s.word);
  const p = occurrences(seen, s.word);
  if (h !== 1) wrong.push(`${s.who}: ${word} heard ${h} time(s), heard ${JSON.stringify(heard)}`);
  if (p !== 1) {
    wrong.push(`${s.who}: ${word} printed ${p} time(s) - without the word the state is an LED's`
      + " colour and shape alone");
  }
  if (s.exact && heard !== seen) {
    wrong.push(`${s.who}: heard ${JSON.stringify(heard)} beside printed ${JSON.stringify(seen)}`);
  }
  // A region that is not `exact` holds controls named otherwise than they
  // print, so it cannot be held to heard-as-printed, and a word count cannot
  // see a label that paraphrases the word ("Run is armed" beside RUN ARMED is
  // one "run armed"). Ask the LED instead: at every site it must not be heard
  // at all. An `exact` region needs no such check: any label is heard text
  // that is not printed, which heard-as-printed already fails.
  if (!s.exact) {
    for (const led of Array.from(region.querySelectorAll(".led"))) {
      if (!hiddenFromEar(led)) {
        wrong.push(`${s.who}: the LED beside it is heard, as`
          + ` ${JSON.stringify(led.getAttribute("aria-label") ?? squash(heardText(led)))}`);
      }
    }
  }
}
function none(wrong: string[]): void { assert(wrong.length === 0, wrong.join("\n  ")); }

const panel = (scope: Element, title: string): Element => {
  const p = Array.from(scope.querySelectorAll("section.panel"))
    .find((s) => (s.querySelector("h2")?.textContent ?? "").trim() === title);
  if (!p) throw new Error(`no "${title}" panel - the fixture never rendered the site`);
  return p;
};

// --------------------------------------------------------------------- tests

// The helpers' KNOWN POSITIVES, one per rule the site cases lean on. A helper
// that read plain textContent would pass every site case for the wrong reason.
//
// MUTANT "descendant label ignored" (heardText's `${label && label.trim() ?
// label : heardText(child)}` made `${heardText(child)}` - a mutant of THIS
// file). Observed, 12/14:
//   x precondition: the heard and seen helpers follow the rules they model: a labelled LED was not said before its word
//   expected "cooling on Cooling"
//   got      "Cooling"
//   x control: Led names a labelled LED and hides an unlabelled one from the ear: a labelled LED is not heard by its label
//   expected "in use"
//   got      ""
// Run TOGETHER with the thermometer's "restore the label", the thermometer
// case stayed green, blind, because a helper that cannot hear a label cannot
// hear it twice. Observed, 11/14: the two lines above, and the source guard's
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: components/monitor.tsx:499: label={on ? "cooler on" : "cooler off"} repeats the word beside it: "cooler on" beside printed "ON"
// So the precondition is what keeps every site case from passing blind.
//
// MUTANT "heard reads aria-hidden" (heardText's aria-hidden test deleted).
// Observed, 13/14:
//   x precondition: the heard and seen helpers follow the rules they model: an aria-hidden glyph reached the ear
//   expected "Cooling"
//   got      "+ Cooling"
//
// MUTANT "occurrences counts inside words" (the two lookarounds deleted from
// occurrences' string branch). Observed, 13/14:
//   x precondition: the heard and seen helpers follow the rules they model: occurrences counted inside a word
//   expected 1
//   got      2
//
// MUTANT "ancestors ignored" (hiddenFromEar reads the element only, not its
// ancestors: its loop body run once). Observed, 13/14:
//   x precondition: the heard and seen helpers follow the rules they model: a region under an aria-hidden ancestor was heard
//   expected true
//   got      false
await test("precondition: the heard and seen helpers follow the rules they model", () => {
  const kp = document.createElement("div");
  kp.innerHTML = `
    <div id="kp-led"><span class="led" role="img" aria-label="cooling on"></span><span>Cooling</span></div>
    <div id="kp-quiet"><span class="led" aria-hidden="true"></span><span aria-hidden="true">+</span><span>Cooling</span></div>
    <div id="kp-phone"><span class="hidden lg:inline">wide</span><span class="sr-only">heard</span><span>both</span></div>
    <div aria-hidden="true"><p><span id="kp-muted">x</span></p></div>
    <div class="sr-only"><p><span id="kp-unseen">y</span></p></div>`;
  document.body.appendChild(kp);
  try {
    const $ = (id: string) => document.getElementById(id)!;
    eq(squash(heardText($("kp-led"))), "cooling on Cooling", "a labelled LED was not said before its word");
    eq(squash(heardText($("kp-quiet"))), "Cooling", "an aria-hidden glyph reached the ear");
    eq(squash(seenText($("kp-led"))), "Cooling", "an LED's label reached the eye");
    eq(squash(heardText($("kp-phone"))), "heard both", "a display:none span was heard, or an sr-only one was not");
    eq(squash(seenText($("kp-phone"))), "both", "a display:none or sr-only span was seen");
    eq(hiddenFromEar($("kp-muted")), true, "a region under an aria-hidden ancestor was heard");
    eq(hiddenFromEye($("kp-muted")), false, "aria-hidden hid a region from the eye");
    eq(hiddenFromEye($("kp-unseen")), true, "a region under an sr-only ancestor was seen");
    eq(hiddenFromEar($("kp-unseen")), false, "sr-only hid a region from the ear");
    eq(occurrences("disconnected CONNECTED", "connected"), 1, "occurrences counted inside a word");
    eq(occurrences("Dismiss run-armed banner RUN ARMED", "run armed"), 1,
      "occurrences read a hyphenated control name as the phrase");
    eq(occurrences("cooler on ON", "on"), 2, "occurrences missed a case-insensitive repeat");
  } finally {
    kp.remove();
  }
});

// CONTROL: the mechanism the fix relies on. `Led` names a labelled LED and
// hides an unlabelled one. Every site case below leans on the second half; the
// first half is why FlowNodeCard and RestrictedAssetsPanel keep their labels.
//
// Its labelled half goes red under "descendant label ignored" (quoted above).
// A mutant of Led itself (ui.tsx's `aria-hidden={props.label ? undefined :
// true}` dropped) was NOT run: ui.tsx is outside this task's files. Such a
// mutant would not change what these cases hear anyway, because an unlabelled
// LED is an empty span; the aria-hidden assertion below is what would see it.
await test("control: Led names a labelled LED and hides an unlabelled one from the ear", async () => {
  const m = await mount(createElement("div", null,
    createElement("p", { id: "labelled" }, createElement(Led, { state: "on", label: "in use" })),
    createElement("p", { id: "bare" }, createElement(Led, { state: "on" }))));
  try {
    const labelled = m.box.querySelector("#labelled .led")!;
    eq(labelled.getAttribute("role"), "img", "a labelled LED is not an image");
    eq(squash(heardText(m.box.querySelector("#labelled")!)), "in use", "a labelled LED is not heard by its label");
    eq(m.box.querySelector("#bare .led")!.getAttribute("aria-hidden"), "true", "an unlabelled LED is not aria-hidden");
    eq(squash(heardText(m.box.querySelector("#bare")!)), "", "an unlabelled LED reached the ear");
  } finally {
    m.done();
  }
});

// THE THERMOMETER, no power readout (monitor.tsx; the classic Monitor's
// Thermal panel and the new Live screen both draw it). The LED said "cooler
// on" before the printed "ON".
//
// MUTANT "restore the label" (`label={on ? "cooler on" : "cooler off"}` put
// back on the LED). Observed, 12/14:
//   x the thermometer's cooler word is heard once, and printed, on and off: cooler on: "on" heard 2 time(s), heard "cooler on ON → -10°C at target (no power readout)"
//   cooler on: heard "cooler on ON → -10°C at target (no power readout)" beside printed "ON → -10°C at target (no power readout)"
//   cooler off: "off" heard 2 time(s), heard "cooler off OFF → -10°C (no power readout)"
//   cooler off: heard "cooler off OFF → -10°C (no power readout)" beside printed "OFF → -10°C (no power readout)"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: components/monitor.tsx:499: label={on ? "cooler on" : "cooler off"} repeats the word beside it: "cooler on" beside printed "ON"
//
// MUTANT "row hidden from the ear" (`aria-hidden` on the no-power row's <div>,
// the LED left unlabelled). Observed, 13/14:
//   x the thermometer's cooler word is heard once, and printed, on and off: cooler on: "on" heard 0 time(s), heard ""
//   cooler on: heard "" beside printed "ON → -10°C at target (no power readout)"
//   cooler off: "off" heard 0 time(s), heard ""
//   cooler off: heard "" beside printed "OFF → -10°C (no power readout)"
await test("the thermometer's cooler word is heard once, and printed, on and off", async () => {
  const wrong: string[] = [];
  for (const on of [true, false]) {
    const m = await mount(createElement(ThermometerBar, {
      power: null, on, target: -10, atTarget: on, canReportPower: false,
    }));
    try {
      checkSite(m.box, { who: `cooler ${on ? "on" : "off"}`, printed: on ? "ON" : "OFF",
        word: on ? "on" : "off", exact: true }, wrong);
    } finally {
      m.done();
    }
  }
  none(wrong);
});

// CONTROL: the power branch has no LED and never had one; the fix left it be.
await test("control: the thermometer with a power readout draws no LED and says its power", async () => {
  const m = await mount(createElement(ThermometerBar, {
    power: 40, on: true, target: -10, atTarget: false, canReportPower: true,
  }));
  try {
    eq(m.box.querySelector(".led"), null, "the power branch grew an LED");
    const heard = squash(heardText(m.box));
    assert(/cooler power 40%/.test(heard), `the power branch no longer says its power: ${JSON.stringify(heard)}`);
  } finally {
    m.done();
  }
});

// CAPTURE's cooler panel. The LED said "cooling on" before the printed
// "Cooling", and "cooling off" before "Off".
//
// MUTANT "restore the label" (`label={cooler?.on ? "cooling on" : "cooling
// off"}` put back). Observed, 12/14:
//   x Capture's cooling word is heard once, and printed, on and off: cooling on: "cooling" heard 2 time(s), heard "cooling on Cooling"
//   cooling on: heard "cooling on Cooling" beside printed "Cooling"
//   cooling off: "off" heard 2 time(s), heard "cooling off Off"
//   cooling off: heard "cooling off Off" beside printed "Off"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: views/CaptureView.tsx:1631: label={cooler?.on ? "cooling on" : "cooling off"} repeats the word beside it: "cooling on" beside printed "Cooling"
//
// MUTANT "word only in the label" (the label put back and the printed
// `{cooler?.on ? "Cooling" : "Off"}` deleted). Observed, 13/14:
//   x Capture's cooling word is heard once, and printed, on and off: cooling on: "Cooling" is printed by 0 element(s), not 1 - without the word the state is an LED's colour and shape alone
//   cooling off: "Off" is printed by 0 element(s), not 1 - without the word the state is an LED's colour and shape alone
await test("Capture's cooling word is heard once, and printed, on and off", async () => {
  const statusFor = (on: boolean) => ({
    status: {
      connected: { camera: { connected: true, name: "sim cam" } },
      looping: false, mode: "sim", busy_lanes: [],
      camera: {
        temperature: -4.2, can_cool: true, has_dew_heater: false,
        width: 1000, height: 800, max_gain: 300, max_bin: 2,
        cooler: { on, power: 40, target_c: -10, at_target: false, can_report_power: true },
      },
    },
    principal: { role: "operator", email: null, caps: ["view.status", "control.capture"] },
  });
  await seed(statusFor(true));
  const m = await mount(createElement(CaptureView));
  const wrong: string[] = [];
  try {
    checkSite(panel(m.box, "Cooler"), { who: "cooling on", printed: "Cooling", word: "cooling", exact: true }, wrong);
    await seed(statusFor(false));
    checkSite(panel(m.box, "Cooler"), { who: "cooling off", printed: "Off", word: "off", exact: true }, wrong);
  } finally {
    m.done();
  }
  none(wrong);
});

// POLAR: the verdict beside the total error, in each of its three tiers. The
// LED's label was `verdict.text`, the identical expression printed after it.
//
// MUTANT "restore the label" (`label={verdict.text}` put back). Observed,
// 12/14:
//   x the polar verdict is heard once, and printed, in every tier: 1.5′: "Excellent — stop here" heard 2 time(s), heard "Excellent — stop here Excellent — stop here"
//   1.5′: heard "Excellent — stop here Excellent — stop here" beside printed "Excellent — stop here"
//   5′: "Good — keep refining" heard 2 time(s), heard "Good — keep refining Good — keep refining"
//   5′: heard "Good — keep refining Good — keep refining" beside printed "Good — keep refining"
//   20′: "Keep going" heard 2 time(s), heard "Keep going Keep going"
//   20′: heard "Keep going Keep going" beside printed "Keep going"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: views/PolarView.tsx:319: label={verdict.text} repeats the word beside it: {verdict.text} is printed beside it
const polarPublish = (extra: Record<string, unknown>) => ({
  polar: {
    state: "running", az_error: 0, alt_error: 0, total_error: 0, progress: 0,
    message: "", source: "native", ...extra,
  },
});
await seed({
  status: { connected: {}, looping: false, mode: "sim", busy_lanes: [] },
  principal: { role: "operator", email: null, caps: ["view.status", "control.mount"] },
  ...polarPublish({ state: "idle" }),
});
await test("the polar verdict is heard once, and printed, in every tier", async () => {
  const m = await mount(createElement(PolarView));
  const wrong: string[] = [];
  try {
    for (const [total, text] of [
      [1.5, "Excellent — stop here"], [5, "Good — keep refining"], [20, "Keep going"],
    ] as const) {
      await seed(polarPublish({ phase: "adjusting", total_error: total, az_error: total, reading_ts: 1 }));
      checkSite(panel(m.box, "Total error"), { who: `${total}′`, printed: text, word: text, exact: true }, wrong);
    }
  } finally {
    m.done();
  }
  none(wrong);
});

// POLAR's busy row before the first reading. The LED said "measuring" in all
// three sub-states: beside "Measuring axis..." that is the word twice, and
// beside "Waiting for solve..." and "Starting - the mount is committed." it
// claimed a measurement nothing was making yet. The label is gone in all
// three, so the row is heard as it is printed. "Starting" is the window
// between a Start press the server accepted and the driver's first publish,
// so the case presses Start against an idle stream to reach it.
//
// MUTANT "restore the label" (`label="measuring"` put back). Observed,
// 12/14:
//   x the polar busy row says its sentence once, and nothing it does not print: measuring: "measuring" heard 2 time(s), heard "measuring Measuring axis…"
//   measuring: heard "measuring Measuring axis…" beside printed "Measuring axis…"
//   waiting: heard "measuring Waiting for solve…" beside printed "Waiting for solve…"
//   starting: heard "measuring Starting — the mount is committed." beside printed "Starting — the mount is committed."
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: views/PolarView.tsx:340: label="measuring" repeats the word beside it: "measuring" beside printed "Measuring axis…"
//
// MUTANT "label kept while waiting" (`label={measuring ? undefined :
// "measuring"}`: only the sub-state that repeats the word loses it).
// Observed, 12/14:
//   x the polar busy row says its sentence once, and nothing it does not print: waiting: heard "measuring Waiting for solve…" beside printed "Waiting for solve…"
//   starting: heard "measuring Starting — the mount is committed." beside printed "Starting — the mount is committed."
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: views/PolarView.tsx:340: label={measuring ? undefined : "measuring"} repeats the word beside it: "measuring" beside printed "Measuring axis…"
await test("the polar busy row says its sentence once, and nothing it does not print", async () => {
  const m = await mount(createElement(PolarView));
  const wrong: string[] = [];
  try {
    await seed(polarPublish({ phase: "measuring", point_index: 0 }));
    checkSite(panel(m.box, "Total error"),
      { who: "measuring", printed: "Measuring axis…", word: "measuring", exact: true }, wrong);
    await seed(polarPublish({ phase: undefined, point_index: undefined }));
    checkSite(panel(m.box, "Total error"),
      { who: "waiting", printed: "Waiting for solve…", word: "waiting", exact: true }, wrong);
    await seed(polarPublish({ state: "idle", phase: undefined, point_index: undefined }));
    routes.set("/api/polar/start", { started: true, source: "native" });
    const start = Array.from(m.box.querySelectorAll("button"))
      .find((b) => /Start Alignment/.test(b.textContent ?? ""));
    assert(start, "no Start Alignment button - the starting sub-state cannot be reached");
    await act(async () => {
      start!.dispatchEvent(new win.MouseEvent("click", { bubbles: true, cancelable: true }));
    });
    await flush();
    checkSite(panel(m.box, "Total error"),
      { who: "starting", printed: "Starting — the mount is committed.", word: "starting", exact: true }, wrong);
  } finally {
    routes.delete("/api/polar/start");
    m.done();
  }
  none(wrong);
});

// GUIDE's calibration panel, in its three states. Nearly the same word, and in
// the warn state not the same word at all: "calibrated" beside "Good
// calibration", "not calibrated" beside "No valid calibration", "check
// calibration" beside "Non-orthogonal - verify". The stem is counted for the
// two that share it; `exact` holds the warn state, where no word is shared and
// the label is a second wording of the same state.
//
// The source guard below is blind to this site by design (exact repeats only),
// so this case is the only thing holding it.
//
// MUTANT "restore the label" (`label={bad ? "not calibrated" : warn ? "check
// calibration" : "calibrated"}` put back). Observed, 13/14:
//   x the guide calibration's state is heard once, and printed, in all three states: good: /calibrat/iu heard 2 time(s), heard "calibrated Good calibration"
//   good: heard "calibrated Good calibration" beside printed "Good calibration"
//   warn: heard "check calibration Non-orthogonal — verify" beside printed "Non-orthogonal — verify"
//   bad: /calibrat/iu heard 2 time(s), heard "not calibrated No valid calibration"
//   bad: heard "not calibrated No valid calibration" beside printed "No valid calibration"
//
// MUTANT "labelled when warn" (`label={warn ? "check calibration" :
// undefined}`: only the paraphrase comes back, which no word count can see).
// Observed, 13/14:
//   x the guide calibration's state is heard once, and printed, in all three states: warn: heard "check calibration Non-orthogonal — verify" beside printed "Non-orthogonal — verify"
await test("the guide calibration's state is heard once, and printed, in all three states", async () => {
  const wrong: string[] = [];
  for (const [who, report, printed, word] of [
    ["good", { is_valid: true, ortho_error_deg: 2 }, "Good calibration", /calibrat/iu],
    ["warn", { is_valid: true, ortho_error_deg: 14 }, "Non-orthogonal — verify", "verify"],
    ["bad", { is_valid: false, ortho_error_deg: 0 }, "No valid calibration", /calibrat/iu],
  ] as const) {
    routes.set("/api/guide/calibration", {
      report: {
        ...report, declination_deg: 12, pier_side: "east", binning: 1,
        advisories: [], source: "native",
      },
    });
    await seed({
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
    });
    const m = await mount(createElement(GuideView));
    try {
      checkSite(panel(m.box, "Calibration"), { who, printed, word, exact: true }, wrong);
    } finally {
      m.done();
    }
  }
  routes.delete("/api/guide/calibration");
  none(wrong);
});

// MONITOR's Progress panel over a run that is armed and waiting. The LED said
// "Run armed and waiting" before the printed "RUN ARMED".
//
// MUTANT "restore the label" (`label="Run armed and waiting"` put back on the
// Monitor's LED). Observed, 12/14:
//   x the Monitor's RUN ARMED is heard once, and printed: armed: "run armed" heard 2 time(s), heard "Run armed and waiting RUN ARMED NGC 7000 mosaic"
//   armed: heard "Run armed and waiting RUN ARMED NGC 7000 mosaic" beside printed "RUN ARMED NGC 7000 mosaic"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: views/MonitorView.tsx:759: label="Run armed and waiting" repeats the word beside it: "Run armed and waiting" beside printed "RUN ARMED"
//
// MUTANT "word visually hidden" (the printed RUN ARMED given `sr-only`: heard
// once, seen nowhere, #217's verifier's shape). Observed, 13/14:
//   x the Monitor's RUN ARMED is heard once, and printed: armed: "RUN ARMED" is printed by 0 element(s), not 1 - without the word the state is an LED's colour and shape alone
const ARMED = {
  armed: {
    id: "s1", name: "NGC 7000 mosaic", owed: 12, accepted: 48, total: 60,
    origin: "flow", origin_id: "f1",
  },
  hold: null,
};
await test("the Monitor's RUN ARMED is heard once, and printed", async () => {
  await seed({
    status: { connected: {}, looping: false, mode: "sim", busy_lanes: [] },
    principal: { role: "operator", email: null, caps: ["view.status", "control.sequence"] },
    sequence: { state: "idle" },
    resumeArm: ARMED,
  });
  const m = await mount(createElement(MonitorView));
  const wrong: string[] = [];
  try {
    checkSite(panel(m.box, "Progress"), { who: "armed", printed: "RUN ARMED", word: "run armed", exact: true }, wrong);
  } finally {
    m.done();
  }
  await seed({ resumeArm: null });
  none(wrong);
});

// THE APP's two banners, above every classic view. Neither region is `exact`:
// each holds its buttons, and the Dismiss button's name ("Dismiss run-armed
// banner") is not what the eye reads off its x. The state phrase is counted,
// and the LED itself must not be heard (checkSite's non-exact rule).
//
// MUTANT "restore the armed label" (`label="Run armed and waiting"` put back
// on App's armed-banner LED). Observed, 12/14:
//   x the App banners say RUN ARMED and SEQUENCE RUNNING / PAUSED once each, and print them: armed banner: "run armed" heard 2 time(s), heard "Run armed and waiting RUN ARMED · NGC 7000 mosaic · 12 frames owed OPEN LIVE Dismiss run-armed banner"
//   armed banner: the LED beside it is heard, as "Run armed and waiting"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: App.tsx:734: label="Run armed and waiting" repeats the word beside it: "Run armed and waiting" beside printed "RUN ARMED"
//
// MUTANT "restore the sequence label" (`label={sequence.state === "paused" ?
// "Sequence paused" : "Sequence running"}` put back). Observed, 12/14:
//   x the App banners say RUN ARMED and SEQUENCE RUNNING / PAUSED once each, and print them: running banner: "SEQUENCE RUNNING" heard 2 time(s), heard "Sequence running SEQUENCE RUNNING · Tonight · 40% OPEN LIVE Dismiss sequence-running banner"
//   running banner: the LED beside it is heard, as "Sequence running"
//   paused banner: "SEQUENCE PAUSED" heard 2 time(s), heard "Sequence paused SEQUENCE PAUSED · Tonight · 40% OPEN LIVE Dismiss sequence-running banner"
//   paused banner: the LED beside it is heard, as "Sequence paused"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: App.tsx:779: label={sequence.state === "paused" ? "Sequence paused" : "Sequence running"} repeats the word beside it: "Sequence paused" beside printed "SEQUENCE PAUSED"
//
// MUTANT "a paraphrase label" on each banner (`label="Run is armed"` on the
// armed LED; separately `label="Plan in progress"` on the sequence LED). The
// phrase is still heard once and neither label shares a run of words with
// what is printed, so before the non-exact rule both SURVIVED, 14/14, with
// the state said twice in two wordings. Observed with it, each 13/14:
//   x the App banners say RUN ARMED and SEQUENCE RUNNING / PAUSED once each, and print them: armed banner: the LED beside it is heard, as "Run is armed"
//   x the App banners say RUN ARMED and SEQUENCE RUNNING / PAUSED once each, and print them: running banner: the LED beside it is heard, as "Plan in progress"
//   paused banner: the LED beside it is heard, as "Plan in progress"
await test("the App banners say RUN ARMED and SEQUENCE RUNNING / PAUSED once each, and print them", async () => {
  win.location.hash = "#/classic";
  await seed({
    authMethods: { methods: [], first_run: false }, // open LAN: never a login gate
    principal: { role: "operator", email: null, caps: ["view.status", "control.sequence"] },
    wsPhase: "up", telemetryStale: false, equipConnected: false, view: "connect",
    weather: null, runBanner: null, resumeArm: ARMED, armedBannerDismissed: null,
    sequence: { state: "idle" },
    status: { connected: {}, looping: false, mode: "sim", busy_lanes: [] },
  });
  const m = await mount(createElement(App));
  const wrong: string[] = [];
  try {
    checkSite(m.box, { who: "armed banner", printed: "RUN ARMED", word: "run armed" }, wrong);
    for (const state of ["running", "paused"] as const) {
      await seed({
        resumeArm: null, sequence: { state, plan_name: "Tonight" },
        runBanner: { active: true, plan_name: "Tonight", percent: 40 },
      });
      const phrase = `SEQUENCE ${state.toUpperCase()}`;
      checkSite(m.box, { who: `${state} banner`, printed: phrase, word: phrase }, wrong);
    }
  } finally {
    m.done();
  }
  await seed({ runBanner: null, resumeArm: null, sequence: { state: "idle" } });
  none(wrong);
});

// LINK STATUS (Settings and Equipment both draw this grid). A connect-attempt
// row said "Camera: connected" and then printed "Camera" and "CONNECTED"; a
// device-only row did the same from telemetry. The role and the word are each
// counted, in every tri-state and both device states. The failed row carries an
// error that names neither, so a count of 1 is the LED's doing alone.
//
// MUTANT "restore the attempt-row label" (`label={`${label}:
// ${meta.word.toLowerCase()}`}` put back on LinkRow's LED). Observed, 12/14:
//   x every Link Status row says its role and its state once each, and prints both: Camera (role): "Camera" heard 2 time(s), heard "Camera: connected Camera CONNECTED"
//   Camera (role): heard "Camera: connected Camera CONNECTED" beside printed "Camera CONNECTED"
//   Camera (state): "CONNECTED" heard 2 time(s), heard "Camera: connected Camera CONNECTED"
//   Camera (state): heard "Camera: connected Camera CONNECTED" beside printed "Camera CONNECTED"
//   Mount (role): "Mount" heard 2 time(s), heard "Mount: degraded Mount DEGRADED Came up at connect, but this role is not reporting a live link now and the backend gave no reason. Reconnect it from Equipment."
//   Mount (role): heard "Mount: degraded Mount DEGRADED Came up at connect, but this role is not reporting a live link now and the backend gave no reason. Reconnect it from Equipment." beside printed "Mount DEGRADED Came up at connect, but this role is not reporting a live link now and the backend gave no reason. Reconnect it from Equipment."
//   Mount (state): "DEGRADED" heard 2 time(s), heard "Mount: degraded Mount DEGRADED Came up at connect, but this role is not reporting a live link now and the backend gave no reason. Reconnect it from Equipment."
//   Mount (state): heard "Mount: degraded Mount DEGRADED Came up at connect, but this role is not reporting a live link now and the backend gave no reason. Reconnect it from Equipment." beside printed "Mount DEGRADED Came up at connect, but this role is not reporting a live link now and the backend gave no reason. Reconnect it from Equipment."
//   Focuser (role): "Focuser" heard 2 time(s), heard "Focuser: failed Focuser FAILED USB serial port did not answer"
//   Focuser (role): heard "Focuser: failed Focuser FAILED USB serial port did not answer" beside printed "Focuser FAILED USB serial port did not answer"
//   Focuser (state): "FAILED" heard 2 time(s), heard "Focuser: failed Focuser FAILED USB serial port did not answer"
//   Focuser (state): heard "Focuser: failed Focuser FAILED USB serial port did not answer" beside printed "Focuser FAILED USB serial port did not answer"
//   Rotator (role): "Rotator" heard 2 time(s), heard "Rotator: not requested Rotator NOT REQUESTED"
//   Rotator (role): heard "Rotator: not requested Rotator NOT REQUESTED" beside printed "Rotator NOT REQUESTED"
//   Rotator (state): "NOT REQUESTED" heard 2 time(s), heard "Rotator: not requested Rotator NOT REQUESTED"
//   Rotator (state): heard "Rotator: not requested Rotator NOT REQUESTED" beside printed "Rotator NOT REQUESTED"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: components/settings/BackendLinkGrid.tsx:78: label={`${label}: ${meta.word.toLowerCase()}`} repeats the word beside it: {label} is printed beside it
//
// MUTANT "restore the device-row label" (`label={`${ROLE_LABEL[role] ??
// role}: ${device?.connected ? "connected" : "disconnected"}`}` put back).
// Observed, 12/14:
//   x every Link Status row says its role and its state once each, and prints both: Guide camera (role): "Guide camera" heard 2 time(s), heard "Guide camera: connected Guide camera CONNECTED"
//   Guide camera (role): heard "Guide camera: connected Guide camera CONNECTED" beside printed "Guide camera CONNECTED"
//   Guide camera (state): "CONNECTED" heard 2 time(s), heard "Guide camera: connected Guide camera CONNECTED"
//   Guide camera (state): heard "Guide camera: connected Guide camera CONNECTED" beside printed "Guide camera CONNECTED"
//   Filter wheel (role): "Filter wheel" heard 2 time(s), heard "Filter wheel: disconnected Filter wheel DISCONNECTED"
//   Filter wheel (role): heard "Filter wheel: disconnected Filter wheel DISCONNECTED" beside printed "Filter wheel DISCONNECTED"
//   Filter wheel (state): "DISCONNECTED" heard 2 time(s), heard "Filter wheel: disconnected Filter wheel DISCONNECTED"
//   Filter wheel (state): heard "Filter wheel: disconnected Filter wheel DISCONNECTED" beside printed "Filter wheel DISCONNECTED"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: components/settings/BackendLinkGrid.tsx:134: label={`${ROLE_LABEL[role] ?? role}: ${device?.connected ? "connected" : "disconnected"}`} repeats the word beside it: {ROLE_LABEL[role]??role} is printed beside it
await test("every Link Status row says its role and its state once each, and prints both", async () => {
  const links = [
    { role: "camera", attempted: true, ok: true, connected: true, error: null },
    { role: "telescope", attempted: true, ok: true, connected: false, error: null },
    { role: "focuser", attempted: true, ok: false, connected: false, error: "USB serial port did not answer" },
    { role: "rotator", attempted: false, ok: false, connected: false, error: null },
  ];
  const connected = {
    guide_camera: { connected: true, name: "Guide cam" },
    filterwheel: { connected: false, name: "Wheel" },
  };
  const m = await mount(createElement(BackendLinkGrid, { links, connected } as any));
  const wrong: string[] = [];
  try {
    for (const [role, word] of [
      ["Camera", "CONNECTED"], ["Mount", "DEGRADED"], ["Focuser", "FAILED"],
      ["Rotator", "NOT REQUESTED"], ["Guide camera", "CONNECTED"], ["Filter wheel", "DISCONNECTED"],
    ] as const) {
      checkSite(m.box, { who: `${role} (role)`, printed: role, word: role, exact: true }, wrong);
      checkSite(m.box, { who: `${role} (state)`, printed: role, word, exact: true }, wrong);
    }
  } finally {
    m.done();
  }
  none(wrong);
});

// SETTINGS > PROFILES. The active row's LED said "Active profile" before the
// "Active" badge. The region is the whole card (its Rename and Update buttons
// among it), so the word is counted, the card is not held `exact`, and the
// LED itself must not be heard (checkSite's non-exact rule).
//
// MUTANT "restore the label" (`label={row.active ? "Active profile" :
// undefined}` put back). Observed, 12/14:
//   x the active profile says Active once, and prints it; an inactive one says nothing of it: active row: "active" heard 2 time(s), heard "Active profile Deep sky rig Active Native / Alpaca · 4 devices · auto-connects on boot Reconnect Rename Deep sky rig Update Deep sky rig from the current rig Export Deep sky rig Delete Deep sky rig"
//   active row: the LED beside it is heard, as "Active profile"
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: components/settings/ProfileList.tsx:620: label={row.active ? "Active profile" : undefined} repeats the word beside it: "Active profile" beside printed "Active"
//
// MUTANT "a paraphrase label" (`label={row.active ? "In use" : undefined}`).
// Before the non-exact rule it SURVIVED, 14/14: "active" is still heard once
// and the guard sees no shared word. Observed with it, 13/14:
//   x the active profile says Active once, and prints it; an inactive one says nothing of it: active row: the LED beside it is heard, as "In use"
await test("the active profile says Active once, and prints it; an inactive one says nothing of it", async () => {
  routes.set("/api/profiles", [
    { id: "a", name: "Deep sky rig", mode: "alpaca", devices_count: 4, active: true, site_name: null },
    { id: "b", name: "Planetary rig", mode: "empty", devices_count: 0, active: false, site_name: null },
  ]);
  await seed({
    status: { connected: {}, looping: false, mode: "none" },
    principal: { role: "admin", email: null, caps: ["view.status", "config.backend"] },
  });
  const m = await mount(createElement(ProfileList));
  const wrong: string[] = [];
  try {
    checkSite(m.box, { who: "active row", printed: "Active", word: "active" }, wrong);
    // CONTROL: the inactive row never had a label (the fix did not change it)
    // and must not start to say "active". Its button says "Activate", a
    // different word.
    const rename = m.box.querySelector(`[aria-label="Rename Planetary rig"]`);
    assert(rename, "no card for the inactive profile - the fixture never rendered it");
    const card = rename!.parentElement!.parentElement!;
    assert(card.querySelector(".led"), "the inactive card lookup walked off the row");
    const heard = squash(heardText(card));
    if (occurrences(heard, "active") !== 0) wrong.push(`inactive row: says "active": ${JSON.stringify(heard)}`);
  } finally {
    m.done();
  }
  routes.delete("/api/profiles");
  none(wrong);
});

// ============================================================ source guard
// A `<Led label=L>` is flagged when L repeats something printed beside it.
//
// BESIDE: the LED's closest JSX ancestor that holds anything but the LED. A
// wrapper that holds only the LED (App's banners put it in its own <span>) is
// climbed through, and so is the expression an LED is drawn under (`{on &&
// <Led/>}`, a ternary branch); the first ancestor with anything else in it is
// the region, and everything printed in it, at any depth, is beside the LED.
// The expression's own other operands are not: a ternary's other branch is
// never on screen with it.
//
// PRINTED: JSX text, and what an expression child can put on screen - its
// string literals, both branches of a ternary, the right side of `&&`, both
// sides of `||` / `??`, template text, a `.map` callback's JSX, and the
// expressions whose value it shows (`{verdict.text}`). A condition is not
// printed (`state === "paused"` puts no "paused" on screen), and neither is
// any attribute value (`title`, `aria-label`, `className`).
//
// REPEATS: the label shows the same expression that is printed
// (`label={verdict.text}` beside `{verdict.text}`, or a template's
// `${label}` beside `{label}`; a trailing `.toLowerCase()` does not make it a
// different value), or one side's words appear as a whole-word run in the
// other's, case-insensitively ("cooler on" beside "ON", "Active profile"
// beside "Active"). Exact repeats only: "calibrated" beside "Good
// calibration" is not flagged, and case 1 holds that site.

type Said = { literals: string[]; exprs: string[] };
type Flag = { file: string; line: number; label: string; why: string };

const CASE_ONLY = new Set(["toLowerCase", "toUpperCase", "toLocaleLowerCase", "toLocaleUpperCase", "trim"]);
function unwrap(n: ts.Node): ts.Node {
  while (ts.isParenthesizedExpression(n) || ts.isNonNullExpression(n) || ts.isAsExpression(n)
    || ts.isSatisfiesExpression(n)) n = n.expression;
  return n;
}
/** An expression's identity: its source without whitespace, less a trailing
 *  case conversion, which shows the same value in another case. */
function exprKey(node: ts.Node, sf: ts.SourceFile): string {
  const n = unwrap(node);
  if (ts.isCallExpression(n) && n.arguments.length === 0 && ts.isPropertyAccessExpression(n.expression)
    && CASE_ONLY.has(n.expression.name.text)) return exprKey(n.expression.expression, sf);
  return n.getText(sf).replace(/\s+/g, "");
}
const isLed = (n: ts.Node): n is ts.JsxSelfClosingElement | ts.JsxOpeningElement =>
  (ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n)) && n.tagName.getText() === "Led";

/** What `node` can put on screen (or into a label), into `out`. */
function said(node: ts.Node, sf: ts.SourceFile, out: Said): void {
  const n = unwrap(node);
  if (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n) || ts.isJsxText(n)) {
    out.literals.push(n.text);
  } else if (ts.isTemplateExpression(n)) {
    out.literals.push(n.head.text);
    for (const span of n.templateSpans) { said(span.expression, sf, out); out.literals.push(span.literal.text); }
  } else if (ts.isConditionalExpression(n)) {
    said(n.whenTrue, sf, out);
    said(n.whenFalse, sf, out);
  } else if (ts.isBinaryExpression(n)) {
    const op = n.operatorToken.kind;
    if (op === ts.SyntaxKind.AmpersandAmpersandToken) {
      said(n.right, sf, out);
    } else if (op === ts.SyntaxKind.BarBarToken || op === ts.SyntaxKind.QuestionQuestionToken) {
      out.exprs.push(exprKey(n, sf));
      said(n.left, sf, out);
      said(n.right, sf, out);
    } else if (op === ts.SyntaxKind.PlusToken) {
      said(n.left, sf, out);
      said(n.right, sf, out);
    }
    // Comparisons and the rest compute a boolean or a number: no word of the
    // source reaches the screen.
  } else if (ts.isJsxElement(n)) {
    for (const c of n.children) said(c, sf, out);
  } else if (ts.isJsxFragment(n)) {
    for (const c of n.children) said(c, sf, out);
  } else if (ts.isJsxExpression(n)) {
    if (n.expression) said(n.expression, sf, out);
  } else if (ts.isArrowFunction(n) || ts.isFunctionExpression(n)) {
    if (ts.isBlock(n.body)) {
      for (const st of n.body.statements) if (ts.isReturnStatement(st) && st.expression) said(st.expression, sf, out);
    } else {
      said(n.body, sf, out);
    }
  } else if (ts.isCallExpression(n)) {
    out.exprs.push(exprKey(n, sf));
    for (const a of n.arguments) if (ts.isArrowFunction(a) || ts.isFunctionExpression(a)) said(a, sf, out);
  } else if (ts.isJsxSelfClosingElement(n) || ts.isNumericLiteral(n)
    || n.kind === ts.SyntaxKind.NullKeyword || n.kind === ts.SyntaxKind.TrueKeyword
    || n.kind === ts.SyntaxKind.FalseKeyword || (ts.isIdentifier(n) && n.text === "undefined")) {
    // A self-closing element prints nothing of its own in source; the rest
    // print no word.
  } else {
    out.exprs.push(exprKey(n, sf));
  }
}

const words = (s: string) => s.toLowerCase().split(/[^\p{L}\p{N}]+/u).filter(Boolean);
function hasRun(hay: string[], needle: string[]): boolean {
  for (let i = 0; i + needle.length <= hay.length; i++) {
    if (needle.every((w, j) => hay[i + j] === w)) return true;
  }
  return false;
}
function repeat(label: Said, printed: Said): string | null {
  for (const e of label.exprs) if (printed.exprs.includes(e)) return `{${e}} is printed beside it`;
  for (const l of label.literals) {
    const lw = words(l);
    if (lw.length === 0) continue;
    for (const p of printed.literals) {
      const pw = words(p);
      if (pw.length === 0) continue;
      if (hasRun(lw, pw) || hasRun(pw, lw)) return `"${l.trim()}" beside printed "${squash(p)}"`;
    }
  }
  return null;
}

/** Every labelled Led in one source text, and the ones that repeat. */
function scanSource(file: string, text: string): { labelled: number; flags: Flag[] } {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let labelled = 0;
  const flags: Flag[] = [];
  const visit = (node: ts.Node): void => {
    if (isLed(node)) {
      const attr = node.attributes.properties
        .find((p): p is ts.JsxAttribute => ts.isJsxAttribute(p) && p.name.getText(sf) === "label");
      if (attr?.initializer) {
        labelled++;
        const label: Said = { literals: [], exprs: [] };
        said(attr.initializer, sf, label);
        // Climb out of wrappers that hold only the LED, and out of the
        // expression an LED is drawn under (`{on && <Led/>}`, a ternary
        // branch, parentheses): what is printed beside that expression is
        // printed beside the LED. The expression's own other operands are not
        // beside it; a ternary's other branch is never on screen with it.
        // Without this climb such an LED was counted as scanned and never
        // compared with anything.
        let child: ts.Node = ts.isJsxOpeningElement(node) ? node.parent : node;
        let up: ts.Node | undefined = child.parent;
        while (up) {
          if (ts.isJsxExpression(up) || ts.isParenthesizedExpression(up)
            || ts.isConditionalExpression(up) || ts.isBinaryExpression(up)) {
            child = up;
            up = up.parent;
            continue;
          }
          if (!ts.isJsxElement(up) && !ts.isJsxFragment(up)) break;
          // Whitespace and `{/* comments */}` are not beside anything: a
          // comment explaining the LED must not stop the climb in its wrapper.
          const others = up.children.filter((c) => c !== child
            && !(ts.isJsxText(c) && c.containsOnlyTriviaWhiteSpaces)
            && !(ts.isJsxExpression(c) && !c.expression));
          if (others.length > 0) {
            const printed: Said = { literals: [], exprs: [] };
            for (const c of others) said(c, sf, printed);
            const why = repeat(label, printed);
            if (why) {
              flags.push({
                file, line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
                label: attr.initializer.getText(sf), why,
              });
            }
            break;
          }
          child = up;
          up = up.parent;
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return { labelled, flags };
}

// THE DEFERRED SITES (#231's third table: "a label that repeats the printed
// name and leaves out the state"). Out of this task's scope and files, and
// flagged by the guard because each label does repeat the name printed beside
// it. Keyed by file and label source, not line, because lines drift. Each is
// asserted to STILL be flagged, so the list cannot rot: whoever fixes one gets
// a failure telling them to take it off.
const DEFERRED: { file: string; label: string }[] = [
  { file: "components/settings/DriversPanel.tsx", label: "{`${d.label} status`}" },
  { file: "views/EquipmentView.tsx", label: "{`${ROLE_LABEL[role] ?? role} link`}" },
];

const SRC = fileURLToPath(new URL("../../", import.meta.url));
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) {
      if (name !== "__tests__" && name !== "node_modules") sources(p, out);
    } else if (name.endsWith(".tsx") && !name.endsWith(".test.tsx")) {
      out.push(p);
    }
  }
  return out;
}
const rel = (p: string) => relative(SRC, p).split(sep).join("/");

// The guard's KNOWN POSITIVES and its fixture controls, in the shape of each
// rule above. Each positive is one of the eleven sites cut down to the shape
// the guard must see; each control is a way to be fooled into a flag.
//
// MUTANT "no climb" (the climb's `child = up; up = up.parent;` made
// `break;`: the scan stops at the LED's parent even when it holds only the
// LED). Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: positive "a wrapper holding only the LED": not flagged
//   positive "a wrapper holding the LED and a comment about it": not flagged
//
// MUTANT "no climb through expressions" (the climb's first `if`, which walks
// up through a JsxExpression, a ternary, `&&` / `||` / `??` or parentheses,
// deleted). Before that `if` existed an LED drawn as `{on && <Led label=.../>}`
// was counted as labelled and compared with nothing. Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: positive "an LED drawn under an &&, beside its word": not flagged
//
// MUTANT "child not advanced through expressions" (that `if` climbs without
// `child = up;`, so the expression holding the LED is read as beside it, and
// with it the ternary's other branch). Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: control "the word is only in the other branch of the LED's own ternary": flagged: "linked" beside printed "linked"
//
// MUTANT "comment stops the climb" (the climb's filter no longer drops an
// empty `{/* comment */}` expression). Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: positive "a wrapper holding the LED and a comment about it": not flagged
//
// MUTANT "case-sensitive words" (`words` without its `toLowerCase()`).
// Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: positive "a label beside the same word, another case": not flagged
//   positive "a wrapper holding only the LED": not flagged
//   positive "a wrapper holding the LED and a comment about it": not flagged
//   positive "a ternary beside a ternary": not flagged
//   positive "a repeat in one state only, the false branch": not flagged
//   positive "a sentence beside its first word": not flagged
//
// MUTANT "one branch" (said's ConditionalExpression reads `whenTrue` only).
// Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: positive "a repeat in one state only, the false branch": not flagged
//
// MUTANT "ternary reads its condition" (said's ConditionalExpression also
// reads `n.condition`). Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: control "the label's value only picks a ternary branch": flagged: {status} is printed beside it
//
// MUTANT "&& reads its condition" (said's `&&` branch also reads `n.left`).
// Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: control "the label's value only gates an &&": flagged: {status} is printed beside it
//
// MUTANT "comparisons printed" (said's `else if (op === PlusToken)` made
// `else`, so a comparison's operands are read like a concatenation's).
// Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: control "the word is only in a comparison beside ||": flagged: "paused" beside printed "paused"
//
// MUTANT "attributes printed" (said's JsxElement branch also reads every
// attribute initializer). Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: control "the word is only in an attribute": flagged: "ready" beside printed "ready"
//
// MUTANT "inside words" (`hasRun` made a substring test on the joined
// words). Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: control "the printed word only contains the label's word": flagged: "connected" beside printed "DISCONNECTED"
//
// MUTANT "case conversion is another value" (exprKey no longer strips a
// trailing `.toLowerCase()`). Observed, 13/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: positive "the printed value in another case": not flagged
const POSITIVE: [string, string][] = [
  ["a label beside the same word, another case",
    `<div><Led state="on" label="cooling on" /><span>Cooling</span></div>`],
  ["the same expression", `<span><Led state={v.led} label={v.text} /><span>{v.text}</span></span>`],
  ["a wrapper holding only the LED",
    `<div><span className="shrink-0"><Led state="warn" label="Run armed and waiting" /></span>`
    + `<span><span>RUN ARMED</span> · {name}</span></div>`],
  ["a wrapper holding the LED and a comment about it",
    `<div><span>{/* why the LED */}<Led state="warn" label="Run armed and waiting" /></span>`
    + `<span>RUN ARMED</span></div>`],
  ["a ternary beside a ternary",
    `<div><Led state={s} label={on ? "cooler on" : "cooler off"} /><span>{on ? "ON" : "OFF"}</span></div>`],
  ["a repeat in one state only, the false branch",
    `<div><Led state={s} label={ok ? "linked" : "disconnected"} /><span>{ok ? "CONNECTED" : "DISCONNECTED"}</span></div>`],
  ["a template's name beside the name",
    "<div><Led state={s} label={`${label}: ${word.toLowerCase()}`} /><span>{label}</span></div>"],
  ["the printed value in another case",
    `<div><Led state={s} label={meta.word.toLowerCase()} /><span>{meta.word}</span></div>`],
  ["a word printed under &&, deeper in the row",
    `<div><Led state={s} label={a ? "Active profile" : undefined} /><div><b>{name}</b>{a && (<i>Active</i>)}</div></div>`],
  ["a sentence beside its first word",
    `<div><Led state="busy" label="measuring" /><span>{m ? "Measuring axis…" : "Waiting for solve…"}</span></div>`],
  ["an LED drawn under an &&, beside its word",
    `<div>{on && <Led state="on" label="cooling on" />}<span>cooling on</span></div>`],
];
const NEGATIVE: [string, string][] = [
  ["the label is the only place the word appears",
    `<div><span>{def.label}</span><Led state={led} label={status} /><Edit /></div>`],
  ["the word is only in an attribute", `<div><Led state="on" label="ready" /><span title="ready">x</span></div>`],
  ["the word is only in a comparison",
    `<div><Led state="on" label="paused" /><span>{s === "paused" ? "Hold" : "Go"}</span></div>`],
  ["the label's value only picks a ternary branch",
    `<div><Led state={s} label={status} /><span>{status ? "Hold" : "Go"}</span></div>`],
  ["the label's value only gates an &&",
    `<div><Led state={s} label={status} /><span>{status && <b>!</b>}</span></div>`],
  ["the printed word only contains the label's word",
    `<div><Led state="off" label="connected" /><span>DISCONNECTED</span></div>`],
  ["the word is only in a comparison beside ||",
    `<div><Led state="on" label="paused" /><span>{note || state === "paused"}</span></div>`],
  ["the word is printed outside the LED's own row",
    `<div><span><Led state="on" label="cooler on" /><em>x</em></span><span>ON</span></div>`],
  ["the word is only in the other branch of the LED's own ternary",
    `<div>{ok ? <Led state="on" label="linked" /> : <span>linked</span>}<em>x</em></div>`],
];

await test("the guard flags each shape of an LED beside its own word, and none of its controls", () => {
  const wrong: string[] = [];
  for (const [who, jsx] of POSITIVE) {
    const r = scanSource(`positive: ${who}`, `const x = ${jsx};`);
    if (r.labelled !== 1) wrong.push(`positive "${who}": found ${r.labelled} labelled LED(s), not 1`);
    if (r.flags.length !== 1) wrong.push(`positive "${who}": not flagged`);
  }
  for (const [who, jsx] of NEGATIVE) {
    const r = scanSource(`control: ${who}`, `const x = ${jsx};`);
    if (r.labelled !== 1) wrong.push(`control "${who}": found ${r.labelled} labelled LED(s), not 1`);
    if (r.flags.length !== 0) wrong.push(`control "${who}": flagged: ${r.flags[0].why}`);
  }
  none(wrong);
});

// THE REAL TREE. No labelled LED in ui/src repeats a word printed beside it,
// apart from the deferred sites, which still do. And the two controls the task
// names are read and let be: the scan must FIND a labelled LED in each, so
// "not flagged" cannot mean "never scanned".
//
// Each "restore the label" mutant above, except the guide calibration's, also
// turns this case red; their records quote it.
//
// MUTANT "flag every labelled LED" (`repeat` returns a reason for any label).
// Observed, 12/14:
//   x the guard flags each shape of an LED beside its own word, and none of its controls: control "the label is the only place the word appears": flagged: any label
//   control "the word is only in an attribute": flagged: any label
//   control "the word is only in a comparison": flagged: any label
//   control "the label's value only picks a ternary branch": flagged: any label
//   control "the label's value only gates an &&": flagged: any label
//   control "the printed word only contains the label's word": flagged: any label
//   control "the word is only in a comparison beside ||": flagged: any label
//   control "the word is printed outside the LED's own row": flagged: any label
//   control "the word is only in the other branch of the LED's own ternary": flagged: any label
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: control components/flows/FlowNodeCard.tsx:218: flagged, but its label is the only place its word appears (any label)
//   control components/settings/RestrictedAssetsPanel.tsx:103: flagged, but its label is the only place its word appears (any label)
//   components/flows/FlowNodeCard.tsx:218: label={status} repeats the word beside it: any label
//   components/GotoStrip.tsx:38: label="mount not moving" repeats the word beside it: any label
//   components/settings/RestrictedAssetsPanel.tsx:103: label={a.satisfied ? "in use" : "not in use"} repeats the word beside it: any label
//   views/FocusView.tsx:949: label="sequence running" repeats the word beside it: any label
//   views/FocusView.tsx:964: label="live loop running" repeats the word beside it: any label
//   views/PolarView.tsx:228: label="alignment error" repeats the word beside it: any label
//
// MUTANT "scan skips settings" (`sources` does not descend into a directory
// named settings). Observed, 13/14:
//   x no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites: control components/settings/RestrictedAssetsPanel.tsx: no labelled LED found - the scan never saw it
//   deferred components/settings/DriversPanel.tsx label={`${d.label} status`} no longer repeats - take it off DEFERRED
await test("no labelled LED in ui/src repeats the word printed beside it, bar the deferred sites", () => {
  const flags: Flag[] = [];
  const labelledIn = new Map<string, number>();
  for (const p of sources(SRC)) {
    const r = scanSource(rel(p), readFileSync(p, "utf8"));
    labelledIn.set(rel(p), r.labelled);
    flags.push(...r.flags);
  }
  const wrong: string[] = [];
  for (const f of ["components/flows/FlowNodeCard.tsx", "components/settings/RestrictedAssetsPanel.tsx"]) {
    if ((labelledIn.get(f) ?? 0) < 1) wrong.push(`control ${f}: no labelled LED found - the scan never saw it`);
    for (const x of flags.filter((y) => y.file === f)) {
      wrong.push(`control ${f}:${x.line}: flagged, but its label is the only place its word appears (${x.why})`);
    }
  }
  const deferred = (x: Flag) => DEFERRED.some((d) => d.file === x.file && d.label === x.label);
  for (const x of flags.filter((y) => !deferred(y))) {
    wrong.push(`${x.file}:${x.line}: label=${x.label} repeats the word beside it: ${x.why}`);
  }
  for (const d of DEFERRED) {
    if (!flags.some((x) => x.file === d.file && x.label === d.label)) {
      wrong.push(`deferred ${d.file} label=${d.label} no longer repeats - take it off DEFERRED`);
    }
  }
  none(wrong);
});

// -------------------------------------------------------------------- report
const total = passed + failed;
console.log(`ledBesideItsWord.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
