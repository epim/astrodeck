// PeopleSection.tsx - the layout shell every PEOPLE surface renders through,
// and the area's SINGLE `people.css` import site (wave R7, T-R7-11).
//
// WHY THE CSS IMPORT LIVES HERE. The wave's CSS rule is one `<area>.css` per
// area, imported by that area's root component and never by `NextApp.tsx`. This
// area has three roots, not one - `UsersEditor`, `AuthMethodsEditor` and
// `AccountIdentity`, mounted from three different sheets and, in the account's
// case, inline on the USERS screen as well. Hanging the import on any one of
// them would leave the other two unstyled the moment the settings hub is code
// split (D-FU-2 / U7a-6), because a chunk that never imports that module never
// pulls its stylesheet. `Section` is the one component all three render, so the
// import is here: one site, and it cannot be reached without the styles.
//
// Everything in this file is presentation with no state, no store and no fetch.

import type { JSX, ReactNode } from "react";
import { Label } from "../../../../ui";
import "./people.css";

/** An eyebrow, an optional right-hand action, and a body. The eyebrow is the
 *  design's `Label`; the action slot is where ADD USER / SAVE METHODS live so a
 *  360 px phone wraps the button under the heading instead of squeezing it
 *  below the 44 px hit floor. */
export function Section({ eyebrow, action, children, ...rest }: {
  eyebrow: string;
  action?: ReactNode;
  children: ReactNode;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <section className="nx-people" data-testid={rest["data-testid"]}>
      <div className="nx-people-head">
        <Label size={11}>{eyebrow}</Label>
        {action != null && <span className="nx-people-headact">{action}</span>}
      </div>
      {children}
    </section>
  );
}

/** A paragraph of explanation under a heading or beside a control. 11.5 px,
 *  `--text-faint`: the same weight the rest of the new UI gives a sentence that
 *  is not itself a value. */
export function Note({ children, tone, ...rest }: {
  children: ReactNode;
  /** `warn` and `bad` raise the note into a bordered box - used only where the
   *  sentence describes something the next press will actually do. */
  tone?: "warn" | "bad";
  "data-testid"?: string;
}): JSX.Element {
  return (
    <p className="nx-people-note" data-tone={tone} data-testid={rest["data-testid"]}>
      {children}
    </p>
  );
}

/** A control row: a title, a sentence, and the control that decides it. The
 *  control comes LAST in the DOM and is floated right by the stylesheet, so a
 *  screen reader hears what the switch is for before it hears its state. */
export function ControlRow({ title, blurb, right, badge, children }: {
  title: string;
  blurb?: ReactNode;
  right?: ReactNode;
  badge?: ReactNode;
  children?: ReactNode;
}): JSX.Element {
  return (
    <div className="nx-people-ctlrow">
      <div className="nx-people-ctltext">
        <div className="nx-people-ctltitle">
          {title}
          {badge != null && <span className="nx-people-badge" data-tone="warn">{badge}</span>}
        </div>
        {blurb != null && <p className="nx-people-note">{blurb}</p>}
        {children}
      </div>
      {right != null && <div className="nx-people-ctlright">{right}</div>}
    </div>
  );
}

/** A horizontal scroll box for a `Segmented` that will not fit 360 px. The
 *  radiogroup itself is untouched design vocabulary; only the container is
 *  ours, so no area class ever styles an `nx-seg*` rule from `next.css`. */
export function ScrollRow({ children, ...rest }: {
  children: ReactNode;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <div className="nx-people-scroll" data-testid={rest["data-testid"]}>{children}</div>
  );
}

/** An inline error or success sentence. Both shapes exist in the legacy panels;
 *  one component means they cannot drift apart. */
export function Verdict({ tone, children, ...rest }: {
  tone: "bad" | "good";
  children: ReactNode;
  "data-testid"?: string;
}): JSX.Element {
  return (
    <p className="nx-people-verdict" data-tone={tone} data-testid={rest["data-testid"]}>
      {children}
    </p>
  );
}
