// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
/* eslint-disable @typescript-eslint/no-explicit-any */
// w17SettingsProfilesTab.test.tsx - who is shown the CLASSIC Settings -> Profiles
// panel (#759, #839).
//
//   Run directly:  npx tsx src/components/settings/__tests__/w17SettingsProfilesTab.test.tsx
//   Also run by `npm test` (run-tests.mjs).
//
// The panel was drawn for `config.backend` alone and replaced by a sentence for
// everyone else. An unforced activate is `control.reconnect` now (operator and
// admin), so the panel is drawn for a principal holding EITHER, and ProfileList
// locks each control by its own capability (`w17ClassicReconnect.test.tsx`
// mounts that part). What is worth asserting HERE is the shell's decision:
//
//   an admin and an operator are shown the list; a viewer is shown a sentence
//   that names both capabilities by the role table's own phrases; and the
//   read-only banner on the tab stops calling an operator's role unable to touch
//   profiles at all.
//
// The panel bodies are stand-ins (a loader hook, the same trick as
// settingsNavigation.test.tsx) that print their own name, so "ProfileList was
// mounted" is a rendered fact and not an import.
//
// MUTANT "the panel is admin-only again" (SettingsView.tsx: `canConfig ||
// canReconnect ? (` made `canConfig ? (`). Run from a byte backup in this
// worktree, restored byte-identically (sha256 compared): see the case below.
// MUTANT "the banner ignores the operator" (the `: canReconnect ? "Your role
// can't change backends or profiles, but it can reconnect ..."` arm made
// `: false ? ...`): see the case below.

const { registerHooks } = await import("node:module");
registerHooks({ load(url: string, context: any, next: any) {
  const m = /\/components\/settings\/([A-Za-z]+)\.tsx$/.exec(url);
  if (m && /(?:Panel|List|Grid)$/.test(m[1])) {
    return {
      format: "module", shortCircuit: true,
      source: `import {createElement} from "react"; export default function Stub(){return createElement("div",{"data-stub":"${m[1]}"},"stub ${m[1]}");}`,
    };
  }
  return next(url, context);
} } as any);

const { JSDOM } = await import("jsdom");
const dom = new JSDOM('<div id="root"></div>', { url: "http://local/#/classic/settings", pretendToBeVisual: true });
const win = dom.window as any;
win.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} });
for (const key of ["window", "document", "navigator", "HTMLElement", "Element", "Event", "MouseEvent", "localStorage"]) {
  Object.defineProperty(globalThis, key, { value: key === "window" ? win : win[key], configurable: true });
}
Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
win.HTMLElement.prototype.scrollIntoView = function () {};

const { createElement: h, act } = await import("react");
const { createRoot } = await import("react-dom/client");
const { useStore } = await import("../../../store");
const SettingsView = (await import("../SettingsView")).default;

let passed = 0, failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void) { try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); } }
function assert(value: unknown, message: string) { if (!value) throw new Error(message); }

const ADMIN = { role: "admin", email: null, caps: ["view.status", "control.reconnect", "config.backend"] };
const OPERATOR = { role: "operator", email: "op@rig", caps: ["view.status", "view.preview", "control.capture", "control.reconnect"] };
const VIEWER = { role: "viewer", email: null, caps: ["view.status", "view.preview"] };

const container = win.document.getElementById("root") as any;
let root: ReturnType<typeof createRoot> | null = null;

async function openProfilesTab(principal: unknown): Promise<string> {
  if (root) await act(async () => { root!.unmount(); });
  root = createRoot(container);
  useStore.setState({ principal, authGate: "open" } as never);
  await act(async () => { root!.render(h(SettingsView)); });
  const tab = [...container.querySelectorAll('[role="radio"]')].find((el: any) => el.textContent === "Profiles") as any;
  assert(tab, "the Profiles tab is not rendered");
  act(() => { tab.click(); });
  return (container.textContent || "") as string;
}
const listMounted = () => container.querySelector('[data-stub="ProfileList"]') != null;

// MUTANT "the panel is admin-only again": observed from a byte backup (restored,
// sha256 compared): "w17SettingsProfilesTab: 5/6 passed" with this case red -
// "x an operator is shown the profile list (Activate is theirs): the Profiles
// tab replaced the list with a sentence for an operator who may reconnect".
let text = await openProfilesTab(OPERATOR);
test("an operator is shown the profile list (Activate is theirs)", () => {
  assert(listMounted(), "the Profiles tab replaced the list with a sentence for an operator who may reconnect");
});

// MUTANT "the banner ignores the operator": observed (restored, sha256
// compared): "w17SettingsProfilesTab: 5/6 passed" with this case red - "x the
// read-only banner tells an operator what they can still do: SettingsOpen the
// new six-hub UIConnectProfilesCalibrationSafetyAccountCreditsYour role can't
// change backends or profiles. Connection status is shown for reference.stub
// ProfileList..." (the page text, flattened).
test("the read-only banner tells an operator what they can still do", () => {
  assert(/can reconnect a saved profile from the Profiles tab/.test(text),
    text.replace(/\s+/g, " ").slice(0, 400));
});

text = await openProfilesTab(ADMIN);
test("an admin is shown the profile list and no read-only banner", () => {
  assert(listMounted(), "an admin lost the profile list");
  assert(!/Your role can't change/.test(text) && !/Read-only/.test(text), "an admin was shown a read-only banner");
});

text = await openProfilesTab(VIEWER);
test("a viewer is shown a sentence naming both capabilities by the role table's phrases", () => {
  assert(!listMounted(), "a viewer was shown the profile list");
  assert(/Activating a profile needs operator or admin access\./.test(text),
    `the activate half is missing or wrong: ${text.replace(/\s+/g, " ").slice(0, 400)}`);
  assert(/Managing profiles needs admin access\./.test(text),
    `the manage half is missing or wrong: ${text.replace(/\s+/g, " ").slice(0, 400)}`);
});

test("a viewer's banner is still the viewer one, not the operator's", () => {
  assert(/connecting rigs and editing profiles needs admin access/.test(text),
    text.replace(/\s+/g, " ").slice(0, 400));
  assert(!/can reconnect a saved profile/.test(text), "a viewer was told they can reconnect");
});

test("precondition: the tab really is Profiles (the connect-tab panels are gone)", () => {
  assert(container.querySelector('[data-stub="DriversPanel"]') == null, "the Connect tab is still showing");
});

console.log(`w17SettingsProfilesTab: ${passed}/${passed + failed} passed`);
for (const failure of failures) console.log(failure);
export const result = { passed, failed, total: passed + failed };
if (failed) process.exitCode = 1;
