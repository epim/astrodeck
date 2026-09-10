import type { JSX, ReactNode } from "react";

/** The content frame every sheet shares (ARCHITECTURE section 5): a header row
 *  with the BACK pill, a 34 px icon tile, the 13 px title and a live line, then
 *  a scrolling body and an optional sticky footer.
 *
 *  BACK calls the `onBack` PROP rather than the router directly: primitives
 *  import no router, so the shell's `SheetHost` wires `nav.back()` in. That is
 *  a deliberate narrowing of section 5's "BACK calls nav.back()" - the
 *  behaviour is the same, the dependency does not point the wrong way. */
export function Sheet({ title, sub, icon, live, right, footer, children, backLabel = "BACK", onBack, className = "", ...rest }: {
  title: string;
  sub?: ReactNode;
  icon?: ReactNode;
  live?: ReactNode;
  right?: ReactNode;
  footer?: ReactNode;
  children: ReactNode;
  backLabel?: string;
  /** Omit on a sheet with nowhere to go back to (the login gate). */
  onBack?: () => void;
  className?: string;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <section className={`nx-sheet ${className}`.trim()} aria-label={title} data-testid={rest["data-testid"]}>
      <header className="nx-sheet-head">
        {onBack && (
          <button type="button" className="nx-sheet-back" onClick={onBack}
            aria-label={`Back to ${backLabel.toLowerCase()}`}>
            <span aria-hidden="true">&lsaquo; </span>{backLabel}
          </button>
        )}
        {icon != null && <span className="nx-sheet-tile" aria-hidden="true">{icon}</span>}
        <span className="nx-sheet-titles">
          <h2 className="nx-sheet-title">{title}</h2>
          {sub != null && <span className="nx-sheet-sub">{sub}</span>}
          {live != null && <span className="nx-sheet-live">{live}</span>}
        </span>
        {right != null && <span className="nx-sheet-right">{right}</span>}
      </header>
      <div className="nx-sheet-body">{children}</div>
      {footer != null && <footer className="nx-sheet-foot">{footer}</footer>}
    </section>
  );
}
