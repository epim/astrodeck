// Glossary.tsx - the plain-language definitions (wave R7, T-R7-16).
//
// Sixteen terms straight from `help.ts` HELP, in declaration order (the
// beginner-first set was appended there deliberately, so alphabetising it would
// put "bias" above "gain" for someone who has never heard of either).
//
// The term and its definition both ride in `ListRow`'s title slot rather than
// title + sub. `next.css`'s `.nx-row-sub` is a single ellipsised mono line -
// correct for a device row's state line, and wrong for a four-sentence
// definition, which would be truncated to about six words. `next.css` belongs
// to T-R7-0 and an area sheet may not redefine its classes, so the two lines
// carry their own type instead; the title slot is a flex item and therefore
// already a block box, so they stack.

import type { JSX } from "react";
import { Card, Label, ListRow } from "../../../ui";
import { HELP } from "../../../../help";
import { HELP_KEYS, hyphenate, termLabel } from "./helpModel";

export function Glossary(): JSX.Element {
  return (
    <section className="nx-help-block" aria-label="Glossary">
      <Label size={11}>GLOSSARY</Label>
      <Card padding={0}>
        <div className="nx-help-gloss">
          {HELP_KEYS.map((k) => (
            <div key={k} className="nx-help-gloss-cell">
              <ListRow
                data-testid="help-glossary"
                title={
                  <>
                    <span className="nx-help-term">{termLabel(k)}</span>
                    <span className="nx-help-def">{hyphenate(HELP[k])}</span>
                  </>
                }
              />
            </div>
          ))}
        </div>
      </Card>
    </section>
  );
}
