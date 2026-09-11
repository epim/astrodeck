import type { JSX, ReactNode } from "react";

/** The dashed empty state: NOTHING IN THE RETICLE, NO SESSION RUNNING, NO SUBS
 *  YET. `hint` must say what would fill it, not narrate that it is empty. */
export function EmptyCard({ title, hint, action, className = "", ...rest }: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <div className={`nx-empty ${className}`.trim()} data-testid={rest["data-testid"]}>
      <div className="nx-empty-title">{title}</div>
      {hint != null && <div className="nx-empty-hint">{hint}</div>}
      {action != null && <div className="nx-empty-action">{action}</div>}
    </div>
  );
}
