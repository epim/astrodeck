import type { JSX, ReactNode } from "react";

/** Chakra Petch 600 uppercase, .16em tracking, `--text-faint`.
 *  The design's 9 px group labels are raised to the 10 px floor (README
 *  "raise to a 10 px floor in production"). */
export function Label({ children, size = 10, className = "", ...rest }: {
  children: ReactNode;
  size?: 10 | 11;
  className?: string;
  /** Forwarded so a test can assert THIS heading rather than a substring of the
   *  whole card, and so a caller can hang a layout class off the div. */
  "data-testid"?: string;
}): JSX.Element {
  return (
    <div
      className={`nx-label ${className}`.trim()}
      data-size={size}
      data-testid={rest["data-testid"]}
    >
      {children}
    </div>
  );
}
