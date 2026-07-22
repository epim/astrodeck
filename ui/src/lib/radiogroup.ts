// Shared radiogroup keyboard model (UX-20). Lifted from SegmentedControl so every
// hand-rolled `role="radiogroup"` gets the same WAI-ARIA behaviour: arrow keys
// move+select (wrapping), Home/End jump to ends, Enter/Space select the focused
// radio, and a roving tabindex keeps a single tab stop on the group.

import type { KeyboardEvent as RKeyboardEvent } from "react";

/** Target index for a navigation key (arrows wrap; Home/End clamp), or `null`
 *  for any non-navigation key (Enter/Space/typing). */
export function radioNextIndex(key: string, current: number, count: number): number | null {
  switch (key) {
    case "ArrowRight":
    case "ArrowDown":
      return (current + 1) % count;
    case "ArrowLeft":
    case "ArrowUp":
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

/** Move DOM focus to the `idx`-th `[role=radio]` inside the event target's
 *  enclosing `[role=radiogroup]`. Guarded end-to-end so a synthetic event with
 *  no real DOM (unit tests) is a harmless no-op. */
export function focusRadioAt(
  e: { currentTarget?: { closest?: (s: string) => Element | null } },
  idx: number,
): void {
  const group = e.currentTarget?.closest?.("[role=radiogroup]");
  const radios = group?.querySelectorAll?.("[role=radio]");
  (radios?.[idx] as HTMLElement | undefined)?.focus?.();
}

/** Full radiogroup key handler: Arrow/Home/End move+select (focus follows the
 *  selection), Enter/Space select the focused radio. `select` is called with the
 *  target index; callers gate on `disabled` before invoking this. */
export function handleRadioKeyDown(
  e: RKeyboardEvent<HTMLElement>,
  i: number,
  count: number,
  select: (idx: number) => void,
): void {
  const next = radioNextIndex(e.key, i, count);
  if (next !== null) {
    e.preventDefault();
    select(next);
    focusRadioAt(e, next);
    return;
  }
  if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
    e.preventDefault();
    select(i);
  }
}

/** Roving tabindex: only the active radio (or the first, when none is active) is
 *  in the tab order. */
export function rovingTabIndex(i: number, activeIndex: number): 0 | -1 {
  return i === (activeIndex >= 0 ? activeIndex : 0) ? 0 : -1;
}
