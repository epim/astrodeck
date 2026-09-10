import { useId, type JSX, type ReactNode } from "react";

/** A labelled input row. The label is a real `<label>` bound by id, so the
 *  control it wraps gets its accessible name from the visible text rather than
 *  from a duplicated `aria-label` that can drift out of step with it. */
export function Field({ label, hint, htmlFor, children, className = "", ...rest }: {
  label: string;
  hint?: ReactNode;
  htmlFor?: string;
  children: ReactNode;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  const auto = useId();
  const id = htmlFor ?? auto;
  return (
    <div className={`nx-field ${className}`.trim()} data-testid={rest["data-testid"]}>
      <label className="nx-field-label" htmlFor={id}>{label}</label>
      {children}
      {hint != null && <div className="nx-field-hint">{hint}</div>}
    </div>
  );
}
