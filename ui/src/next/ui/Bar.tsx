import type { JSX } from "react";
import type { Tone } from "./types";

export interface BarSegment {
  /** Share of the bar's width. Widths are proportional, so the numbers can be
   *  planned minutes per filter straight from the ledger. */
  frac: number;
  /** 0..1 of THIS segment that is done. */
  fill: number;
  /** A colour value (the filter colour), not a token name. */
  color: string;
  label?: string;
}

/** Two shapes behind one name, because the design uses one bar for both: the
 *  3 px sub-progress strip under the live stack, and the 6-14 px segmented
 *  integration bar with a legend (`L 4/12`). */
export function Bar({ value = 0, tone = "accent", height = 3, segments, label, className = "", ...rest }: {
  value?: number;
  tone?: Tone;
  height?: 3 | 6 | 14;
  segments?: BarSegment[];
  label?: string;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  if (segments && segments.length > 0) {
    return (
      <div className={`nx-bar-wrap ${className}`.trim()} data-testid={rest["data-testid"]}>
        <div
          className="nx-bar-segs"
          style={{ height: `${height}px` }}
          role="img"
          aria-label={label ?? segments.map((s) => s.label).filter(Boolean).join(", ")}
        >
          {segments.map((s, i) => (
            <div
              key={s.label ?? i}
              className="nx-bar-seg"
              data-seg={s.label ?? String(i)}
              // flexGrow, not the `flex` shorthand: the shorthand is what the
              // prototype used, but a test (and a reader) can only see the
              // planned share when it is its own longhand.
              style={{ flexGrow: s.frac, flexShrink: 1, flexBasis: 0 }}
            >
              <div
                className="nx-bar-seg-fill"
                style={{ width: `${Math.min(100, Math.max(0, s.fill * 100))}%`, background: s.color }}
              />
            </div>
          ))}
        </div>
        <div className="nx-bar-legend">
          {segments.filter((s) => s.label != null).map((s, i) => (
            <span key={s.label ?? i} className="nx-bar-legend-item">
              <span className="nx-bar-swatch" style={{ background: s.color }} aria-hidden="true" />
              {s.label}
            </span>
          ))}
        </div>
      </div>
    );
  }
  const pct = Math.min(100, Math.max(0, value * 100));
  return (
    <div
      className={`nx-bar ${className}`.trim()}
      data-tone={tone}
      style={{ height: `${height}px` }}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      data-testid={rest["data-testid"]}
    >
      <div className="nx-bar-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}
