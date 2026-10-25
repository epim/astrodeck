// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// w1Keys.test.ts - lib/keys.ts, the one place an editor key binding is matched
// (#1124, #769).
//
//   Run:  node --import tsx src/lib/__tests__/w1Keys.test.ts   (from ui/)
//   Also run by `npm test` (run-tests.mjs) and type-checked by `tsc -b`.
//
// The rule graded here: an event that carries ctrl, meta or alt the binding
// did not declare is not that binding. The DOM files next door
// (w1KeysAndUndoClassic, w1KeysAndUndoNext) prove each editor surface obeys it.

import { EDITOR_KEYS, actionFor, editorKeyAction, matches, type KeyBinding } from "../keys";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg}\n  expected ${String(want)}\n  got      ${String(got)}`);
}

const ev = (key: string, o: Partial<Record<"ctrlKey" | "metaKey" | "shiftKey" | "altKey", boolean>> = {}) =>
  ({ key, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...o });

test("a binding with no modifiers declared is a bare key, and ignores shift", () => {
  const b: KeyBinding = { keys: ["f"] };
  eq(matches(ev("f"), b), true, "a bare f");
  eq(matches(ev("F", { shiftKey: true }), b), true, "Shift+F reads F and is still the key");
  eq(matches(ev("f", { ctrlKey: true }), b), false, "Ctrl+F is the browser's find");
  eq(matches(ev("f", { metaKey: true }), b), false, "Cmd+F is the browser's find");
  eq(matches(ev("f", { altKey: true }), b), false, "Alt+F is a menu accelerator");
  eq(matches(ev("g"), b), false, "another key");
});

test("a binding that declares a modifier needs it, and the others still stay up", () => {
  const b: KeyBinding = { keys: ["z"], ctrl: true, shift: false };
  eq(matches(ev("z", { ctrlKey: true }), b), true, "Ctrl+Z");
  eq(matches(ev("z"), b), false, "a bare z");
  eq(matches(ev("Z", { ctrlKey: true, shiftKey: true }), b), false, "Ctrl+Shift+Z declared shift off");
  eq(matches(ev("z", { ctrlKey: true, altKey: true }), b), false, "Ctrl+Alt+Z carries an undeclared alt");
  eq(matches(ev("z", { ctrlKey: true, metaKey: true }), b), false, "Ctrl+Cmd+Z carries an undeclared meta");
});

test("a binding lists several keys, compared without regard to case", () => {
  const b: KeyBinding = { keys: ["Delete", "Backspace"] };
  eq(matches(ev("Delete"), b), true, "Delete");
  eq(matches(ev("Backspace"), b), true, "Backspace");
  eq(matches(ev("delete"), b), true, "the case of the key does not matter");
  eq(matches(ev("Backspace", { ctrlKey: true }), b), false, "Ctrl+Backspace is a word delete in a browser");
});

test("actionFor answers the first action one of whose bindings the event is", () => {
  const table = { a: [{ keys: ["a"] }], b: [{ keys: ["b"] }, { keys: ["c"], ctrl: true }] } as const;
  eq(actionFor(ev("a"), table), "a", "a");
  eq(actionFor(ev("c", { ctrlKey: true }), table), "b", "the second binding of b");
  eq(actionFor(ev("c"), table), null, "c without its ctrl");
});

test("the editor chords: Ctrl or Cmd+Z undo, +Shift redo, Ctrl+Y redo, Delete, f, and nothing else", () => {
  eq(editorKeyAction(ev("z", { ctrlKey: true })), "undo", "Ctrl+Z");
  eq(editorKeyAction(ev("z", { metaKey: true })), "undo", "Cmd+Z");
  eq(editorKeyAction(ev("Z", { ctrlKey: true, shiftKey: true })), "redo", "Ctrl+Shift+Z");
  eq(editorKeyAction(ev("Z", { metaKey: true, shiftKey: true })), "redo", "Cmd+Shift+Z");
  eq(editorKeyAction(ev("y", { ctrlKey: true })), "redo", "Ctrl+Y");
  eq(editorKeyAction(ev("y", { metaKey: true })), null, "Cmd+Y is the browser's History on a Mac");
  eq(editorKeyAction(ev("y", { ctrlKey: true, shiftKey: true })), null, "Ctrl+Shift+Y is not redo");
  eq(editorKeyAction(ev("z")), null, "a bare z");
  eq(editorKeyAction(ev("z", { shiftKey: true })), null, "Shift+Z");
  eq(editorKeyAction(ev("z", { ctrlKey: true, altKey: true })), null, "Ctrl+Alt+Z is somebody else's chord");
  eq(editorKeyAction(ev("x", { ctrlKey: true })), null, "Ctrl+X");
  eq(editorKeyAction(ev("Delete")), "delete", "Delete");
  eq(editorKeyAction(ev("Backspace")), "delete", "Backspace");
  eq(editorKeyAction(ev("f")), "fit", "f");
  eq(editorKeyAction(ev("F", { shiftKey: true })), "fit", "Shift+F");
});

test("no editor chord answers to a browser chord: ctrl, meta or alt with a key that declares none of them", () => {
  for (const key of ["f", "Delete", "Backspace"]) {
    for (const mod of ["ctrlKey", "metaKey", "altKey"] as const) {
      eq(editorKeyAction(ev(key, { [mod]: true })), null, `${mod} with ${key}`);
    }
  }
});

test("every editor action has a binding", () => {
  for (const action of Object.keys(EDITOR_KEYS) as (keyof typeof EDITOR_KEYS)[]) {
    eq(EDITOR_KEYS[action].length > 0, true, `${action} has no binding`);
  }
});

const total = passed + failed;
console.log(`w1Keys.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as unknown as { process: { exitCode: number } }).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
