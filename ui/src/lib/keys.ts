// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// keys.ts - the one place a keyboard binding is matched (#1124, #769).
//
// WHY ONE MODULE. A handler on the window that tests `e.key === "f"` claims
// the key whatever else is held, so Ctrl+F and Cmd+F fitted the flow canvas
// instead of opening the browser's find, and Ctrl+Backspace deleted the
// selected stage. The fix is a rule, not a patch per handler: a binding says
// which modifiers it needs, and an event that carries any other of ctrl, meta
// or alt is not that binding. Every editor shortcut goes through `matches`, so
// a new one cannot forget the rule.
//
// WHAT A BINDING DECLARES. `ctrl`, `meta` and `alt` default to false: the
// modifier must be UP. `shift` is the exception, because it changes what a key
// types ("f" reads "F") as often as it changes what the chord means: left
// undeclared, shift is ignored; declared, it must match.
//
// WHETHER A KEY MAY ACT AT ALL (not while typing in a field, not under a
// modal) is the caller's decision and is made before it asks here.

export interface KeyEventLike {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
}

export interface KeyBinding {
  /** The `KeyboardEvent.key` values this binding answers to, compared
   *  without regard to case. */
  readonly keys: readonly string[];
  readonly ctrl?: boolean;
  readonly meta?: boolean;
  readonly alt?: boolean;
  /** Undeclared: shift is ignored. */
  readonly shift?: boolean;
}

/** True when `e` is exactly `binding`: the key, and the modifiers it
 *  declared, with every modifier it did not declare up. */
export function matches(e: KeyEventLike, binding: KeyBinding): boolean {
  if (e.ctrlKey !== (binding.ctrl ?? false)) return false;
  if (e.metaKey !== (binding.meta ?? false)) return false;
  if (e.altKey !== (binding.alt ?? false)) return false;
  if (binding.shift !== undefined && e.shiftKey !== binding.shift) return false;
  const k = e.key.toLowerCase();
  return binding.keys.some((want) => want.toLowerCase() === k);
}

/** The first action of `table` one of whose bindings `e` is, or null. */
export function actionFor<A extends string>(
  e: KeyEventLike,
  table: Readonly<Record<A, readonly KeyBinding[]>>,
): A | null {
  for (const action of Object.keys(table) as A[]) {
    if (table[action].some((b) => matches(e, b))) return action;
  }
  return null;
}

export type EditorKeyAction = "undo" | "redo" | "delete" | "fit";

/** The flow editor's keys, shared by the classic canvas and the #/next
 *  surface. Undo is Ctrl or Cmd+Z, redo Ctrl or Cmd+Shift+Z and Ctrl+Y (Windows'
 *  own spelling; Cmd+Y is the browser's History on a Mac and is left alone).
 *  Delete and fit take no modifier: Ctrl+Backspace and Ctrl+F belong to the
 *  browser. */
export const EDITOR_KEYS: Readonly<Record<EditorKeyAction, readonly KeyBinding[]>> = {
  undo: [
    { keys: ["z"], ctrl: true, shift: false },
    { keys: ["z"], meta: true, shift: false },
  ],
  redo: [
    { keys: ["z"], ctrl: true, shift: true },
    { keys: ["z"], meta: true, shift: true },
    { keys: ["y"], ctrl: true, shift: false },
  ],
  delete: [{ keys: ["Delete", "Backspace"] }],
  fit: [{ keys: ["f"] }],
};

/** What a keystroke asks of the flow editor, or null for nothing. */
export function editorKeyAction(e: KeyEventLike): EditorKeyAction | null {
  return actionFor(e, EDITOR_KEYS);
}
