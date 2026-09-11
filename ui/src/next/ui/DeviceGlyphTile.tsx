import type { JSX, ReactNode } from "react";

/** The 34 px tile that fronts every device row, with the driver's state as an
 *  LED in the corner. `off` draws a BAR rather than a dot, the way the
 *  prototype does, so "not connected" is a different SHAPE and not just a
 *  greyer circle. */
export function DeviceGlyphTile({ glyph, led, accent = false, className = "", ...rest }: {
  glyph: ReactNode;
  led?: "on" | "off" | "warn" | "bad";
  accent?: boolean;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <span className={`nx-glyphtile ${className}`.trim()} data-accent={accent ? "true" : undefined}
      data-testid={rest["data-testid"]}>
      <span className="nx-glyphtile-glyph" aria-hidden="true">{glyph}</span>
      {led != null && <span className="nx-glyphtile-led" data-led={led} aria-hidden="true" />}
    </span>
  );
}
