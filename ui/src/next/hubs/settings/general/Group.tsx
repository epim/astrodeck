// Group.tsx - a Settings group: the 10 px caps label and the rounded card of
// rows under it (`proto/22-settings.html`, RIG / SKY / PHONE / LIBRARY).
//
// The rows sit edge-to-edge inside the card with a hairline between them, which
// `Card`'s own 12/14 px padding would break - so the card is rendered with zero
// padding and each row carries the separator. `next.css` is outside this task's
// ownership, so the two rules that would otherwise be classes are inline here
// and read from the same tokens the sheet does (`--line`).

import { Children, type JSX, type ReactNode } from "react";
import { Card, Label } from "../../../ui";

const WRAP = { display: "flex", flexDirection: "column", gap: "6px" } as const;
const SEPARATED = { borderBottom: "1px solid var(--line)" } as const;

export function Group({ label, children, testId }: {
  label: string;
  children: ReactNode;
  testId?: string;
}): JSX.Element | null {
  const rows = Children.toArray(children).filter(Boolean);
  if (rows.length === 0) return null;
  return (
    <div style={WRAP} data-testid={testId}>
      <Label>{label}</Label>
      <Card padding={0}>
        {rows.map((row, i) => (
          // `Children.toArray` stamps a key on every element it keeps, so a row
          // that appears or disappears (RESET BRIGHTNESS, HAPTICS on a device
          // with no vibrator) does not renumber the wrappers under it and force
          // its neighbours to remount.
          <div
            key={(row as { key?: string | null }).key ?? i}
            style={i < rows.length - 1 ? SEPARATED : undefined}
          >{row}</div>
        ))}
      </Card>
    </div>
  );
}
