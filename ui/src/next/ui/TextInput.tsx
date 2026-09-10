import type { ChangeEvent, FocusEvent, JSX, KeyboardEvent } from "react";
import { lockedAttrs, lockedClass } from "./honest";

/** The one text input. `readOnly` (not `disabled`) when locked: a locked input
 *  stays focusable and selectable, so the value can still be read and copied,
 *  and the reason rides in `title` + `aria-disabled`. */
export function TextInput({ value, onChange, onBlur, onEnter, placeholder, mono = false, type = "text", ariaLabel, id, lockedReason = null, className = "", ...rest }: {
  value: string;
  onChange: (next: string) => void;
  /** Commit-on-leave. Called with the CURRENT value when focus leaves, so a
   *  field that writes to the rig can hold the keystrokes and send once. Never
   *  called while locked - a read-only field has nothing to commit. */
  onBlur?: (value: string) => void;
  /** Commit-on-Enter, the keyboard half of the same pair: on a phone the return
   *  key dismisses the keyboard without ever blurring, so a field with only
   *  `onBlur` looks like it swallowed the edit. */
  onEnter?: (value: string) => void;
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
      onBlur={(e: FocusEvent<HTMLInputElement>) => { if (!lockedReason) onBlur?.(e.target.value); }}
      onKeyDown={(e: KeyboardEvent<HTMLInputElement>) => {
        if (lockedReason || e.key !== "Enter") return;
        onEnter?.((e.target as HTMLInputElement).value);
      }}
      data-testid={rest["data-testid"]}
      {...lockedAttrs(lockedReason)}
    />
  );
}
