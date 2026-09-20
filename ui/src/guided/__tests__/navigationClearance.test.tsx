import { JSDOM } from "jsdom";

const dom = new JSDOM('<div id="root"></div>', { url: "http://local/", pretendToBeVisual: true });
const win = dom.window as any;
for (const key of ["window", "document", "navigator", "HTMLElement", "Element", "Node", "Event", "MouseEvent", "localStorage"]) {
  Object.defineProperty(globalThis, key, { value: key === "window" ? win : win[key], configurable: true });
}
const observers: Array<{ callback: () => void; node?: Element; disconnected?: boolean }> = [];
class Observer {
  entry: typeof observers[number];
  constructor(callback: () => void) { this.entry = { callback }; observers.push(this.entry); }
  observe(node: Element) { this.entry.node = node; }
  disconnect() { this.entry.disconnected = true; }
}
Object.assign(globalThis, { ResizeObserver: Observer, IS_REACT_ACT_ENVIRONMENT: true });
const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { WizardFooter } = await import("../GuidedWizard");
const { default: Toasts } = await import("../../components/Toasts");
const { useExperience } = await import("../experience");
const { useStore } = await import("../../store");
let passed = 0, failed = 0;
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; console.log(name, e); } }
function assert(value: unknown, message: string) { if (!value) throw new Error(message); }
const root = createRoot(win.document.getElementById("root"));
await act(async () => {
  useExperience.setState({ mode: "guided", home: false, wizard: "focus" });
  useStore.setState({ toasts: [{ id: 900001, kind: "generic", level: "warning", title: "Equipment connection lost", detail: "Reconnect before continuing.", count: 1, ttl: 0, createdAt: Date.now() }] });
  root.render(h("div", null, h(WizardFooter, { step: "focus", onStep() {}, onTool() {} }), h(Toasts)));
});
const footer = win.document.querySelector("footer");
const observer = observers.find(o => o.node === footer)!;
let height = 94;
footer.getBoundingClientRect = () => ({ height });
observer.callback();
test("notifications reserve the measured navigation height", () => {
  assert(win.document.documentElement.style.getPropertyValue("--guided-footer-height") === "94px", "footer height not published");
  const toast = win.document.querySelector("[data-guided-notifications]");
  assert(toast.style.bottom.includes("--guided-footer-height"), "toast does not clear footer");
  assert(toast.style.top === "auto", "phone top positioning competes with dock");
});
height = 187;
observer.callback();
test("wrapped blockers and lesson controls update notification clearance", () => {
  assert(win.document.documentElement.style.getPropertyValue("--guided-footer-height") === "187px", "stale footer clearance");
});
test("long notification stacks scroll instead of covering navigation", () => {
  const toast = win.document.querySelector("[data-guided-notifications]");
  assert(toast.style.maxHeight.includes("--guided-footer-height") && toast.style.overflowY === "auto", "unbounded notification stack");
});
await act(async () => { win.document.querySelector('button[aria-label="Dismiss"]').click(); });
test("notifications remain dismissible", () => assert(useStore.getState().toasts.length === 0, "dismiss was lost"));
await act(async () => root.unmount());
test("leaving walkthrough removes its reserved area", () => {
  assert(observer.disconnected && !win.document.documentElement.style.getPropertyValue("--guided-footer-height"), "footer reservation leaked");
});
console.log(`guided navigation clearance: ${passed}/${passed + failed} passed`);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
