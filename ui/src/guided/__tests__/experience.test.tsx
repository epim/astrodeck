import { JSDOM } from "jsdom";
const dom = new JSDOM('<div id="root"></div>', { url: "http://local/#/classic/flows?sky=preview", pretendToBeVisual: true });
const win = dom.window;
for (const key of ["window", "document", "navigator", "HTMLElement", "Event", "MouseEvent", "localStorage"] as const) {
  Object.defineProperty(globalThis, key, { value: key === "window" ? win : win[key], configurable: true });
}
Object.defineProperty(win, "matchMedia", { value: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }) });
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
const { createElement: h, useState, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { RetainedTool } = await import("../GuidedWorkspace");
const { useExperience, initialExperience } = await import("../experience");
const { ExperienceSwitch } = await import("../ExperienceSwitch");
const { useStore } = await import("../../store");
let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`${name}: ${e}`); } }
function assert(value: unknown, message: string) { if (!value) throw new Error(message); }
let mounts = 0;
function Tool() {
  const [draft, setDraft] = useState(() => { mounts++; return "My unsaved flow"; });
  return h("button", { onClick: () => setDraft("Edited unsaved flow"), "data-testid": "draft" }, draft);
}
function Harness() {
  const mode = useExperience((s) => s.mode);
  return h("div", null, h(ExperienceSwitch), h(RetainedTool, { hidden: mode === "guided", children: h(Tool) }));
}
test("existing users default to Pro", () => assert(initialExperience(undefined) === "pro", "migration changed presentation"));
test("blocked storage falls back to Pro", () => assert(initialExperience({ getItem: () => { throw new Error("blocked"); } }) === "pro", "storage exception escaped"));
test("explicit preview link can choose Guided", () => assert(initialExperience(undefined, "#/classic/monitor?experience=guided") === "guided", "link ignored"));
const root = createRoot(win.document.getElementById("root")!);
await act(async () => { root.render(h(Harness)); });
const draft = win.document.querySelector<HTMLButtonElement>('[data-testid="draft"]')!;
act(() => { draft.click(); });
const radios = win.document.querySelectorAll<HTMLButtonElement>('[role="radio"]');
act(() => { radios[0].click(); });
test("Guided hides the tool without discarding its draft", () => {
  assert(win.document.querySelector<HTMLElement>('[data-testid="retained-tool"]')!.style.display === "none", "tool still visible");
  assert(mounts === 1 && draft.textContent === "Edited unsaved flow", "draft lost on switch");
});
test("switching mode keeps the current view and other query flags", () => {
  assert(win.location.hash.includes("/classic/flows?") && win.location.hash.includes("sky=preview"), "view or flags were replaced");
});
act(() => { radios[1].click(); });
test("Pro restores the exact same tool instance", () => {
  assert(win.document.querySelector('[data-testid="draft"]') === draft && mounts === 1, "tool remounted");
  assert(draft.textContent === "Edited unsaved flow", "edited draft lost");
  assert(win.document.querySelector<HTMLElement>('[data-testid="retained-tool"]')!.style.display === "flex", "tool remained hidden");
});
test("explicit Pro selection survives refreshing a Guided preview link", () => {
  assert(initialExperience(win.localStorage, win.location.hash) === "pro", "preview forced Guided back on");
});
test("remembering Another Night changes only a local presentation preference", () => {
  const rigBefore = useStore.getState();
  useExperience.getState().setJourney("returning");
  assert(win.localStorage.getItem("astrodeck.journey.v1") === "returning", "choice was not saved");
  assert(useStore.getState() === rigBefore, "choosing a journey mutated rig state");
});
const stateBeforeSwitch = useStore.getState();
act(() => {
  useExperience.getState().openWizard("location");
  useStore.setState({ sequence: { state: "holding" } });
  radios[0].click();
});
test("an active run keeps its tools exposed on switching to Guided", () => {
  assert(!useExperience.getState().home, "mode switch covered the active operation");
  assert(useExperience.getState().wizard === null, "setup wizard covered the active operation");
  assert(useStore.getState().sequence.state === "holding", "mode switch changed operation state");
  assert(useStore.getState().view === stateBeforeSwitch.view, "mode switch changed tool");
});
act(() => { root.unmount(); });
console.log(`guided experience: ${passed}/${passed + failed} passed`);
for (const failure of failures) console.log(failure);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
