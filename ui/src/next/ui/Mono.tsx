import type { JSX, ReactNode } from "react";
import type { Tone } from "./types";

/** IBM Plex Mono value text with tabular figures. `size` is a px number; the
 *  10 px floor is enforced here so no call site can undercut it. */
export function Mono({ children, size = 10.5, tone, className = "", ...rest }: {
  children: ReactNode;
  size?: number;
  tone?: Tone;
  className?: string;
  /** Forwarded so a test can assert THIS value rather than a substring of the
   *  whole card, and so a caller can hang a layout class off the span. */
  "data-testid"?: string;
}): JSX.Element {
  return (
    <span
      className={`nx-mono ${className}`.trim()}
      data-tone={tone}
      data-testid={rest["data-testid"]}
      style={{ fontSize: `${Math.max(10, size)}px` }}
    >
      {children}
    </span>
  );
}
