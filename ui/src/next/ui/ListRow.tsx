import type { JSX, ReactNode } from "react";
import { lockedAttrs, lockedClass, honestPress } from "./honest";
import type { Tone } from "./types";

/** The Settings group row and the Rig device row are the same object: 34 px
 *  icon tile, title, sub, a right slot (a state word, a segmented control, a
 *  switch) and an optional chevron. Renders a `<div>` when there is nothing to
 *  press, so a read-only row is not a tab stop that goes nowhere. */
export function ListRow({ icon, title, sub, right, onPress, chevron = false, tone, lockedReason = null, onExplain, className = "", ...rest }: {
  icon?: ReactNode;
  title: ReactNode;
  sub?: ReactNode;
  right?: ReactNode;
  onPress?: () => void;
  chevron?: boolean;
  tone?: Tone;
  lockedReason?: string | null;
  onExplain?: (reason: string) => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const body = (
    <>
      {icon != null && <span className="nx-row-tile" aria-hidden="true">{icon}</span>}
      <span className="nx-row-text">
        <span className="nx-row-title">{title}</span>
        {sub != null && <span className="nx-row-sub">{sub}</span>}
      </span>
      {right != null && <span className="nx-row-right">{right}</span>}
      {chevron && <span className="nx-row-chevron" aria-hidden="true">&rsaquo;</span>}
    </>
  );
  const cls = `nx-row ${className}`.trim();
  if (!onPress) {
    return <div className={cls} data-tone={tone} data-testid={rest["data-testid"]}>{body}</div>;
  }
  return (
    <button
      type="button"
      className={lockedClass(lockedReason, cls)}
      data-tone={tone}
      onClick={honestPress(lockedReason, onExplain, onPress)}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    >
      {body}
    </button>
  );
}
