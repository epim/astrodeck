import type { JSX, ReactNode } from "react";

/** Chakra Petch 600 uppercase, .16em tracking, `--text-faint`.
 *  The design's 9 px group labels are raised to the 10 px floor (README
 *  "raise to a 10 px floor in production"). */
export function Label({ children, size = 10, className = "" }: {
  children: ReactNode;
  size?: 10 | 11;
  className?: string;
}): JSX.Element {
  return (
    <div className={`nx-label ${className}`.trim()} data-size={size}>{children}</div>
  );
}
