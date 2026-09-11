import { useId, useState, type JSX, type ReactNode } from "react";
import { Label } from "./Label";
import { Mono } from "./Mono";
import { lockedAttrs, lockedClass } from "./honest";

/** The one collapsible group: a 44 px summary row over a body.
 *
 *  NOT `<details>`/`<summary>`. Safari paints its own disclosure marker and
 *  animates `[open]` on its own terms, both of which fight the token system,
 *  and neither is reachable from CSS in every engine we ship to. This is a
 *  plain `<button aria-expanded>` addressing a region by `aria-controls`, which
 *  announces identically and paints only what `next.css` says.
 *
 *  Uncontrolled by default (`defaultOpen`); pass `open` to drive it from the
 *  caller's own state, in which case `onToggle` is the only way it changes.
 *
 *  Honest-disabled (ARCHITECTURE section 6): a locked disclosure still renders,
 *  still focuses, and a press states the reason instead of expanding - a group
 *  that silently refuses to open is indistinguishable from an empty one.
 *
 *  The body element is always in the DOM so `aria-controls` never points at a
 *  missing id, but its CHILDREN are mounted only while open: eight call sites
 *  in this wave hide numeric fields and fetch-backed rows behind it, and a
 *  closed group must cost nothing to render. */
export function Disclosure({
  summary, sub, open, defaultOpen = false, onToggle, children,
  lockedReason = null, onExplain, className = "", ...rest
}: {
  /** The eyebrow word. Also the accessible name of the body region. */
  summary: string;
  /** The one-line value that says what is inside without opening it (a count,
   *  a state word, the current setting). */
  sub?: ReactNode;
  /** Controlled mode. Omit for a group that remembers its own state. */
  open?: boolean;
  defaultOpen?: boolean;
  onToggle?: (open: boolean) => void;
  children: ReactNode;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  /** Lands on the ROOT, so a test can assert the whole group; the row inside
   *  it is `.nx-disclosure-head` (or `button[aria-expanded]`). */
  "data-testid"?: string;
}): JSX.Element {
  const [ownOpen, setOwnOpen] = useState(defaultOpen);
  const controlled = open != null;
  const isOpen = controlled ? open : ownOpen;
  const auto = useId();
  const bodyId = `${auto}-body`;
  const headId = `${auto}-head`;

  const press = () => {
    // The guard is written here rather than borrowed from `honestPress` so the
    // refusal is visible in this file: a locked group must not toggle, and the
    // reason must reach the caller's channel exactly once per press.
    if (lockedReason) { onExplain?.(lockedReason); return; }
    const next = !isOpen;
    if (!controlled) setOwnOpen(next);
    onToggle?.(next);
  };

  return (
    <div className={`nx-disclosure ${className}`.trim()} data-open={isOpen ? "true" : "false"}
      data-testid={rest["data-testid"]}>
      <button
        type="button"
        id={headId}
        className={lockedClass(lockedReason, "nx-disclosure-head")}
        aria-expanded={isOpen}
        aria-controls={bodyId}
        onClick={press}
        {...lockedAttrs(lockedReason)}
      >
        <Label size={11}>{summary}</Label>
        {sub != null && <Mono size={10}>{sub}</Mono>}
        <span className="nx-disclosure-chev" data-open={isOpen ? "true" : "false"} aria-hidden="true">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor"
            strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M9 5l7 7-7 7" />
          </svg>
        </span>
      </button>
      <div
        id={bodyId}
        role="region"
        aria-labelledby={headId}
        className="nx-disclosure-body"
        hidden={!isOpen}
      >
        {isOpen && children}
      </div>
    </div>
  );
}
