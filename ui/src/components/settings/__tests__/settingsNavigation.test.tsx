/* eslint-disable @typescript-eslint/no-explicit-any */
// Exercise the real Settings shell while panel bodies are stand-ins: the
// contract under test is arriving at and focusing the right editor, including
// from another tab in an already mounted Settings screen.
const { registerHooks } = await import("node:module");
registerHooks({ load(url: string, context: any, next: any) {
  if (/\/components\/settings\/[A-Za-z]+(?:Panel|List|Grid)\.tsx$/.test(url)) {
    return { format: "module", shortCircuit: true, source: 'import {createElement} from "react"; export default function Panel(){return createElement("div",null,"Editor body");}' };
  }
  return next(url, context);
} } as any);
const { JSDOM } = await import("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://local/#/classic/settings?panel=optics&experience=guided", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
for (const key of ["window", "document", "navigator", "HTMLElement", "Element", "Event", "MouseEvent", "localStorage"]) {
  Object.defineProperty(globalThis, key, { value: key === "window" ? win : win[key], configurable: true });
}
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const scrolled: string[] = [];
win.HTMLElement.prototype.scrollIntoView = function () { scrolled.push(this.getAttribute("data-settings-panel")); };
const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const { openSettingsPanel } = await import("../../../lib/settingsNavigation");
const { parseHash } = await import("../../../next/router");
const SettingsView = (await import("../SettingsView")).default;
useStore.setState({ principal: { role: "admin", email: null, caps: ["config.backend", "view.status"] } });
let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`${name}: ${e}`); } }
function assert(value: unknown, message: string) { if (!value) throw new Error(message); }
const root = createRoot(win.document.getElementById("root"));
await act(async () => { root.render(h(SettingsView)); });
test("an optics deep link scrolls to and focuses the existing editor", () => {
  assert(scrolled.includes("optics"), "editor was not revealed");
  assert(win.document.activeElement?.getAttribute("data-settings-panel") === "optics", "keyboard focus stayed behind");
});
const profiles = [...win.document.querySelectorAll('[role="radio"]')].find((el: any) => el.textContent === "Profiles") as any;
act(() => { profiles.click(); });
test("precondition: a different settings tab really opened", () => {
  assert(!win.document.querySelector('[data-settings-panel="site"]'), "Connect never closed");
});
await act(async () => {
  openSettingsPanel("site");
  await new Promise((resolve) => setTimeout(resolve, 20));
});
test("a link from another tab reveals the site and preserves Guided", () => {
  assert(win.location.hash.includes("experience=guided"), "presentation flag dropped");
  assert(scrolled.includes("site"), "site was not revealed");
  assert(win.document.activeElement?.getAttribute("data-settings-panel") === "site", "site did not receive focus");
});
act(() => { profiles.click(); });
const historyLength = win.history.length;
await act(async () => { openSettingsPanel("site"); });
test("reopening the same panel link leaves another settings tab without adding history", () => {
  assert(win.document.activeElement?.getAttribute("data-settings-panel") === "site", "same-URL link left the wrong tab open");
  assert(win.history.length === historyLength, "same-URL reveal added duplicate history");
});
act(() => { root.unmount(); });
win.history.replaceState(null, "", "#/rig?tab=guide");
openSettingsPanel("optics");
test("the new UI opens its optics sheet without switching roots", () => {
  assert(win.location.hash.startsWith("#/rig"), "new UI was forced into classic");
  assert(parseHash(win.location.hash).sheets.includes("optics"), `no optics sheet: ${win.location.hash}`);
});
console.log(`settings navigation: ${passed}/${passed + failed} passed`);
for (const failure of failures) console.log(failure);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
