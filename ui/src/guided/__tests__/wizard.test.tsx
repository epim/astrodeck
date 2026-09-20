/* eslint-disable @typescript-eslint/no-explicit-any */
// Real wizard navigation and lifetime, with editor bodies standing in for
// their separately tested APIs. No telescope command belongs to navigation.
const { registerHooks } = await import("node:module");
registerHooks({ load(url: string, context: any, next: any) {
  if (url.endsWith("/guided/GuidedLocation.tsx")) return { format: "module", shortCircuit: true,
    source: 'import {createElement as h,useState} from "react"; export default function Site({onContinue}){const [v,set]=useState("Draft site");return h("form",{id:"guided-location-form",onSubmit:e=>{e.preventDefault();onContinue();}},h("input",{"aria-label":"Site draft",value:v,onChange:e=>set(e.target.value)}));}' };
  if (url.endsWith("/next/hubs/sky/sheets/horizon.tsx")) return { format: "module", shortCircuit: true,
    source: 'import {createElement as h} from "react"; export function HorizonSheet(){return h("div",{"data-testid":"horizon"},"Horizon editor");}' };
  if (url.endsWith("/guided/GuidedFirstImage.tsx")) return { format: "module", shortCircuit: true, source: 'export default function FirstImage(){return null;}' };
  return next(url, context);
} } as any);
const { JSDOM } = await import("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://local/#/classic/monitor?experience=guided", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
for (const key of ["window", "document", "navigator", "HTMLElement", "Event", "MouseEvent", "localStorage"]) {
  Object.defineProperty(globalThis, key, { value: key === "window" ? win : win[key], configurable: true });
}
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { WizardHeader, WizardFooter, WizardPanels } = await import("../GuidedWizard");
const { useExperience } = await import("../experience");
const { useStore } = await import("../../store");
const { wizardAcceptsView } = await import("../wizard");
let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`${name}: ${e}`); } }
function assert(value: unknown, message: string) { if (!value) throw new Error(message); }
function button(text: string) {
  const found = [...win.document.querySelectorAll("button")].find((el: any) => el.textContent.replace(/\s+/g, " ").trim() === text);
  if (!found) throw new Error(`Missing button ${text}`);
  return found as HTMLButtonElement;
}
const tools: string[] = [];
function Harness() {
  const state = useExperience();
  const active = state.mode === "guided" && !state.home && state.wizard !== null;
  return h("div", null,
    active && h(WizardHeader, { step: state.wizard!, onStep: state.openWizard }),
    h(WizardPanels, { step: state.wizard, active, onStep: state.openWizard }),
    active && h(WizardFooter, { step: state.wizard!, onStep: state.openWizard, onTool: (view) => tools.push(view) }));
}
const root = createRoot(win.document.getElementById("root"));
const rigBefore = useStore.getState();
await act(async () => { useExperience.getState().openWizard("location"); root.render(h(Harness)); });
test("location is embedded without mounting the settings shell", () => {
  assert(win.document.querySelector('[aria-label="Site draft"]'), "no embedded editor");
  assert(!win.document.querySelector('[data-settings-panel]'), "settings shell was mounted");
  assert(win.document.activeElement?.textContent === "Where is your telescope?", "step heading was not focused");
});
const draft = win.document.querySelector('[aria-label="Site draft"]');
await act(async () => { button("Save site and continue").click(); });
test("Continue advances to the direct horizon editor without claiming readiness", () => {
  assert(useExperience.getState().wizard === "horizon", "wrong step");
  assert(win.document.querySelector('[data-testid="horizon"]'), "horizon missing");
  assert(win.document.querySelector('[aria-current="step"]')?.getAttribute("aria-label") === "3. Surroundings", "current step unclear");
  assert(!win.document.body.textContent.includes("Setup complete"), "navigation certified readiness");
});
await act(async () => { button("Back").click(); });
test("Back preserves the exact location editor instance and releases horizon tools", () => {
  assert(win.document.querySelector('[aria-label="Site draft"]') === draft, "draft editor remounted");
  assert(!win.document.querySelector('[data-testid="horizon"]'), "camera-capable editor remained mounted");
});
await act(async () => { button("Pause walkthrough").click(); });
test("pause remembers the current step without destroying the form", () => {
  assert(useExperience.getState().home && useExperience.getState().wizard === "location", "pause lost step");
  assert(win.document.querySelector('[aria-label="Site draft"]') === draft, "pause destroyed draft");
  assert(win.document.querySelector('#wizard-panel').hidden, "paused editor is still exposed");
});
await act(async () => { useExperience.getState().openWizard("location"); useExperience.getState().setMode("pro"); });
test("Pro hides the wizard without resetting its location draft", () => {
  assert(win.document.querySelector('#wizard-panel').hidden, "Pro still shows wizard");
  assert(win.document.querySelector('[aria-label="Site draft"]') === draft, "Pro lost draft");
});
await act(async () => { useExperience.getState().setMode("guided"); useExperience.getState().openWizard("image"); });
act(() => { button("Open camera controls").click(); });
test("image step opens controls without taking an exposure", () => {
  assert(tools.join() === "capture", "camera controls were not requested");
  assert(useStore.getState() === rigBefore, "wizard navigation mutated rig state");
});
test("target browsing and framing stay in the image step, unrelated navigation leaves it", () => {
  assert(["tonight", "atlas", "capture"].every(v => wizardAcceptsView("image", v as any)), "image substep escaped");
  assert(!wizardAcceptsView("image", "settings") && !wizardAcceptsView("location", "settings"), "unrelated tool stayed covered");
});
act(() => { root.unmount(); });
console.log(`guided wizard: ${passed}/${passed + failed} passed`);
for (const failure of failures) console.log(failure);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
