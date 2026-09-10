import type { ChangeEvent, JSX } from "react";
import { lockedAttrs, lockedClass } from "./honest";

/** The one text input. `readOnly` (not `disabled`) when locked: a locked input
 *  stays focusable and selectable, so the value can still be read and copied,
 *  and the reason rides in `title` + `aria-disabled`. */
export function TextInput({ value, onChange, placeholder, mono = false, type = "text", ariaLabel, id, lockedReason = null, className = "", ...rest }: {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  mono?: boolean;
  type?: string;
  ariaLabel: string;
  id?: string;
  lockedReason?: string | null;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <input
      id={id}
      type={type}
      className={lockedClass(lockedReason, `nx-input ${className}`.trim())}
      data-mono={mono ? "true" : undefined}
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel}
      readOnly={!!lockedReason}
      onChange={(e: ChangeEvent<HTMLInputElement>) => { if (!lockedReason) onChange(e.target.value); }}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    />
  );
}
