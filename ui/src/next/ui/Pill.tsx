import type { JSX, ReactNode } from "react";
import type { Tone } from "./types";

/** 26 px pill, mono 10 px. The header's rig chips, the status row's SITE pill,
 *  the flows shortcut. Renders a `<button>` only when it does something - a
 *  read-only chip must not be a tab stop that goes nowhere. */
export function Pill({ children, tone = "dim", glyph, onClick, ariaLabel, dashed = false, className = "", ...rest }: {
  children: ReactNode;
  tone?: Tone;
  glyph?: ReactNode;
  onClick?: () => void;
  ariaLabel?: string;
  dashed?: boolean;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const inner = (
    <>
      {glyph != null && <span className="nx-pill-glyph">{glyph}</span>}
      <span className="nx-pill-text">{children}</span>
    </>
  );
  const cls = `nx-pill ${className}`.trim();
  if (!onClick) {
    return (
      <span className={cls} data-tone={tone} data-dashed={dashed ? "true" : undefined}
        aria-label={ariaLabel} data-testid={rest["data-testid"]}>{inner}</span>
    );
  }
  return (
    <button type="button" className={cls} data-tone={tone} data-dashed={dashed ? "true" : undefined}
      aria-label={ariaLabel} onClick={onClick} data-testid={rest["data-testid"]}>{inner}</button>
  );
}
