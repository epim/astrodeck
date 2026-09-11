import type { JSX, ReactNode } from "react";

/** The design's card: radius 16, `--bg-panel` ground, `--line` border.
 *  `accent` and `purple` add a coloured border plus the matching glow;
 *  `dashed` is the empty state (see `EmptyCard`). */
export function Card({ children, tone = "default", padding, className = "", ...rest }: {
  children: ReactNode;
  tone?: "default" | "accent" | "purple" | "dashed";
  padding?: number;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <div
      className={`nx-card ${className}`.trim()}
      data-tone={tone}
      style={padding == null ? undefined : { padding: `${padding}px` }}
      data-testid={rest["data-testid"]}
    >
      {children}
    </div>
  );
}
