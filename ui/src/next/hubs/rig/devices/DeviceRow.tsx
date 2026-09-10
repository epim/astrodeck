// DeviceRow.tsx - one row of the device list (plan hub-rig.md A.4 "Row chrome").
//
// Props only: the model comes from `roster.ts`, which is where the judgement
// lives and where it is tested. This file is chrome plus one rule the model
// cannot enforce on its own - THE THIRD LINE. A row whose state is FAILED or
// DEGRADED always renders its reason, because an alarm word with no stated
// cause is the defect, not the fix (`BackendLinkGrid`'s own note, UX #52), and
// the grid that used to carry those reasons is a Settings surface in the new IA
// (plan E31). This line is now their only home.
//
// A row with no sheet (a guide camera, a flat panel - roles the server reports
// and this build has no screen for) renders as a plain row: no chevron, no tab
// stop. Pressable-into-nothing reads as a broken app; unpressable reads as what
// it is.

import type { JSX } from "react";
import { DeviceGlyphTile, ListRow, Mono } from "../../../ui";
import { NxIcon } from "../../../icons";
import type { Tone } from "../../../ui";
import type { DeviceRowModel, Led } from "./roster";

const LED_TONE: Record<Led, Tone> = {
  on: "good", warn: "warn", bad: "bad", off: "dim",
};

export function DeviceRow({ row, onOpen }: {
  row: DeviceRowModel;
  onOpen: (sheet: string) => void;
}): JSX.Element {
  const tone = LED_TONE[row.led];
  const sub = (
    <>
      {row.driverLine !== "" && <span style={{ display: "block" }}>{row.driverLine}</span>}
      {row.reason != null && (
        <span
          data-testid={`device-reason-${row.role}`}
          style={{
            display: "block",
            whiteSpace: "normal",
            color: row.led === "bad" ? "var(--bad)" : "var(--warn)",
          }}
        >
          {row.reason}
        </span>
      )}
    </>
  );

  return (
    <ListRow
      data-testid={`device-row-${row.role}`}
      icon={<DeviceGlyphTile glyph={<NxIcon name={row.glyph} size={18} />} led={row.led} />}
      title={row.label}
      sub={row.driverLine === "" && row.reason == null ? undefined : sub}
      right={<Mono size={10} tone={tone}>{row.state}</Mono>}
      chevron={row.sheet != null}
      onPress={row.sheet != null ? () => onOpen(row.sheet as string) : undefined}
    />
  );
}
