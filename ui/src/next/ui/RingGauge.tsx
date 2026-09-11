import type { JSX, ReactNode } from "react";

/** The camera sheet's cooler gauge: a 92 px ring with the value inside and the
 *  label under it. Accent while it is doing its job, warn when it is not,
 *  dim when it is off. */
export function RingGauge({ value, min, max, label, sub, tone = "accent", size = 92, className = "", ...rest }: {
  value: number;
  min: number;
  max: number;
  label: string;
  sub?: ReactNode;
  tone?: "accent" | "warn" | "dim";
  size?: number;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const span = max - min;
  const frac = span === 0 ? 0 : Math.min(1, Math.max(0, (value - min) / span));
  const r = 42;
  const circ = 2 * Math.PI * r;
  const on = circ * frac;

  return (
    <div
      className={`nx-ring ${className}`.trim()}
      data-tone={tone}
      style={{ width: `${size}px` }}
      role="img"
      aria-label={`${label} ${value}`}
      data-testid={rest["data-testid"]}
    >
      <div className="nx-ring-dial" style={{ width: `${size}px`, height: `${size}px` }}>
        <svg viewBox="0 0 100 100" width={size} height={size} aria-hidden="true">
          <circle className="nx-ring-back" cx="50" cy="50" r={r} fill="none" strokeWidth="7" />
          <circle
            className="nx-ring-fill"
            cx="50" cy="50" r={r} fill="none" strokeWidth="7" strokeLinecap="round"
            strokeDasharray={`${on.toFixed(2)} ${(circ - on).toFixed(2)}`}
          />
        </svg>
        <div className="nx-ring-inner">
          <span className="nx-ring-value">{value}</span>
          <span className="nx-ring-label">{label}</span>
        </div>
      </div>
      {sub != null && <div className="nx-ring-sub">{sub}</div>}
    </div>
  );
}
