// next/ui/honest.ts - the one honest-disabled idiom for the new UI.
//
// ARCHITECTURE.md section 6 and non-negotiable 6: a control the user could
// plausibly want to press NEVER gets the native `disabled` attribute. That
// attribute strips the element from the accessibility tree, taking the reason
// with it, and leaves a grey rectangle that cannot even be focused to ask why.
// Instead the control dims, carries `aria-disabled="true"`, stays focusable and
// tappable, and a press STATES the reason rather than acting.
//
// Primitives have no store, so they cannot raise the toast themselves: the
// CALLER nominates the channel through `onExplain` (the shell exports
// `explainLock(reason)`, which enqueues the warning toast).

/** Attributes for a locked control. Returns an empty object when live, so call
 *  sites read `{...lockedAttrs(reason)}` with no ternary. */
export function lockedAttrs(reason?: string | null): {
  "aria-disabled"?: true;
  "data-locked"?: "true";
  title?: string;
} {
  if (!reason) return {};
  // `title` too, not only on press: on a desktop the reason is already known
  // and withholding it until a click makes a live-looking button feel broken.
  return { "aria-disabled": true, "data-locked": "true", title: reason };
}

/** `nx-locked` alongside the component's own class when the reason is set. */
export function lockedClass(reason?: string | null, base = ""): string {
  return reason ? `${base} nx-locked`.trim() : base;
}

/** Wrap an action so a locked press explains instead of acting. */
export function honestPress(
  reason: string | null | undefined,
  onExplain: ((reason: string) => void) | undefined,
  action: () => void,
): () => void {
  return () => {
    if (reason) { onExplain?.(reason); return; }
    action();
  };
}
