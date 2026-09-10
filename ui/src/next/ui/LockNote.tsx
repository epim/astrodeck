import type { JSX } from "react";

/** The one read-only note: a lock glyph and `Read-only - <reason>`.
 *
 *  Twenty-one panels each invented their own version of this sentence, in three
 *  different shapes and two different dashes. One component means one shape,
 *  and it means the reason a control gives when PRESSED is word-for-word the
 *  reason the panel prints when idle.
 *
 *  Renders `null` for a null or empty reason, so a caller passes its
 *  `lockedReason` straight through with no ternary and no `&&` that would leave
 *  a stray `false` in the tree:
 *
 *      <LockNote reason={lockedReason} />
 *
 *  The reason is rendered VERBATIM - `lockReason()` already writes the copy
 *  ("needs operator or admin access", "the rig is not reachable"), and a
 *  component that re-punctuates it would drift from the toast that quotes it.
 *
 *  The glyph is drawn here rather than imported from `next/icons.tsx` for the
 *  same reason `Checkbox22` draws its tick: the primitives are self-contained,
 *  and the path is the same `d` string the icon set uses. */
export function LockNote({ reason, className = "", ...rest }: {
  reason?: string | null;
  className?: string;
  "data-testid"?: string;
}): JSX.Element | null {
  if (!reason) return null;
  return (
    <p className={`nx-locknote ${className}`.trim()} data-testid={rest["data-testid"]}>
      <span className="nx-locknote-glyph" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor"
          strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 11h12v10H6zM9 11V7a3 3 0 0 1 6 0v4" />
        </svg>
      </span>
      {`Read-only - ${reason}`}
    </p>
  );
}
