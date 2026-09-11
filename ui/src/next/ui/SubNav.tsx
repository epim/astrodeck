import type { JSX } from "react";
import { Chip } from "./Chip";
import type { Tone } from "./types";

export interface SubNavItem {
  id: string;
  label: string;
  count?: number;
  /** A dot in this tone rides on the chip - the Session chip wears the top
   *  incident's colour so a hold is visible from any hub. */
  dot?: Tone;
}

/** The chips row under the header: NOW / GALLERY 8 / FLOWS 9. */
export function SubNav({ items, value, onChange, ariaLabel = "Section", className = "", ...rest }: {
  items: SubNavItem[];
  value: string;
  onChange: (id: string) => void;
  ariaLabel?: string;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <nav className={`nx-subnav ${className}`.trim()} aria-label={ariaLabel}
      data-testid={rest["data-testid"]}>
      {items.map((it) => (
        <Chip
          key={it.id}
          active={it.id === value}
          count={it.count}
          onClick={() => onChange(it.id)}
          data-testid={`subnav-${it.id}`}
        >
          {it.dot != null && <span className="nx-subnav-dot" data-tone={it.dot} aria-hidden="true" />}
          {it.label}
        </Chip>
      ))}
    </nav>
  );
}
