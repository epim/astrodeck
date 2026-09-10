import type { JSX } from "react";

/** A 1 px hairline in `--line`. Presentational: no separator role, because
 *  every place it is used already has a heading or a card boundary carrying
 *  the structure for a screen reader. */
export function Divider({ className = "" }: { className?: string }): JSX.Element {
  return <div className={`nx-divider ${className}`.trim()} aria-hidden="true" />;
}
