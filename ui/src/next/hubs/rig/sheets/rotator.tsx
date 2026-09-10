// rotator.tsx - the ROTATOR device sheet (plan hub-rig.md B.8; GAP-ANALYSIS
// section 2 "Missing - rotator"; deviation E28).
//
// THERE IS NO DESIGN FRAGMENT FOR THIS SHEET. The design's device list has no
// rotator row at all, so the sheet is built in the sheet language around the
// component that already implements every control the gap analysis asks for:
// `components/equipment/RotatorCard.tsx`.
//
// STAGE 1, DELIBERATELY. The plan's two-stage approach picks stage 1 here:
// mount `RotatorCard` inside the `Sheet` chrome for the dial and the motion
// controls, and re-skin only the header and the readout tiles. The rationale is
// the reuse map's own: the range-of-motion arc maths (`lib/rotatorDial.ts`), the
// roving-tabindex radiogroup (`lib/radiogroup.ts`) and the out-of-range warning
// computed from `adjustedPa` (`lib/rotation.ts:103-113`, where the sky/mech
// offset is DERIVED because it is not on the wire) are exactly the kind of logic
// that must not be re-derived in a second place. A full re-skin is a named
// follow-up, not this task.
//
// WHAT THE SHEET ADDS AROUND IT:
//
//   - The header and its ONE live line, which carries the numbers the body does
//     not repeat: sky PA, mechanical angle, and whether the two are related by a
//     KNOWN offset (`synced`) or an unknown one.
//   - Three read-only readout tiles, so the sheet reads as a device sheet at a
//     glance rather than as a settings panel.
//   - The framing hand-off row (GAP-2: "Framing already has camera rotation;
//     when a rotator exists, DONE should send the PA to it"). This sheet does
//     NOT implement the hand-off - the Sky hub owns framing - so the row states
//     the contract and, when the rotator is not synced, warns that the angle
//     framing sends would be off by the mechanical offset.
//   - An EmptyCard when no rotator is connected. `RotatorCard` itself returns
//     `null` in that case, which is right for a card in a list and wrong for a
//     whole route: a sheet that renders nothing is a dead end.
//
// THE SPLIT GATE IS DELIBERATE AND IS NOT THIS FILE'S TO CHANGE. Motion
// (move / halt / reverse / rotate-to-pa) needs `control.capture`; the
// range-of-motion CONFIG needs `config.backend`. The server enforces both
// separately (app.py:2716-2775), `RotatorCard` states both in its own two lock
// notes, and an operator without `config.backend` must keep a live rotator.

import type { JSX } from "react";
import { useConfig, useStatus, useStore } from "../../../../store";
import type { RotatorConfig } from "../../../../types";
import { resolveRoleConnected } from "../../../../lib/caps";
import { mod360 } from "../../../../lib/rotation";
import RotatorCard, { DEFAULT_ROTATOR_CFG } from "../../../../components/equipment/RotatorCard";
import { Sheet, Card, ActionButton, ReadoutGrid, ReadoutTile, EmptyCard, ListRow } from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";

/** The contract the Sky hub's framing box will honour, stated where the device
 *  is. It is a promise about another screen, so it says what happens rather than
 *  offering a control this sheet does not own. */
const FRAMING_NOTE =
  "When you press DONE on a framing box, the rotator turns to that position angle.";

/** Why an unsynced rotator makes that promise wrong. The offset between sky PA
 *  and the mechanical angle is not on the wire - it is derived from both live
 *  values - so until a sync has established it, "PA 42" means one thing to the
 *  framing box and another to the device. */
const UNSYNCED_WARNING =
  "Sync to sky first, or the angle framing sends will be off by the mechanical offset.";

const RANGE_LABEL: Record<string, string> = {
  full: "FULL",
  half: "HALF",
  quarter: "QUARTER",
};

export function RotatorSheet(): JSX.Element {
  const status = useStatus();
  const config = useConfig();
  const equipConnected = useStore((s) => s.equipConnected);
  const rot = status?.rotator ?? null;
  const role = resolveRoleConnected(
    "rotator", status?.backend_links, status?.connected, equipConnected,
  );
  const cfg: RotatorConfig = { ...DEFAULT_ROTATOR_CFG, ...(config?.rotator ?? {}) };

  // The live line. `synced` is load-bearing rather than decorative: it is the
  // difference between a sky angle the rig knows and one it is guessing.
  const live = rot
    ? [
      `sky PA ${rot.sky_deg.toFixed(1)}°`,
      `mech ${rot.mech_deg.toFixed(1)}°`,
      rot.synced ? "synced" : "not synced",
      ...(rot.moving ? ["turning"] : []),
    ].join(" · ")
    : "not connected";

  return (
    <Sheet
      title="ROTATOR"
      icon={<NxIcon name="rotator" size={18} />}
      live={live}
      backLabel="RIG"
      onBack={() => nav.back()}
      data-testid="rig-rotator"
    >
      {!rot && (
        <EmptyCard
          title="NO ROTATOR CONNECTED"
          hint={role.error
            ? `The rotator link reports: ${role.error}`
            : "Assign a rotator on ADD A DEVICE, then connect the rig."}
          action={
            <ActionButton kind="secondary" onPress={() => nav.go("/rig/devices/addDevice")}>
              GO TO ADD A DEVICE
            </ActionButton>
          }
          data-testid="rotator-empty"
        />
      )}

      {rot && (
        <ReadoutGrid cols={3} data-testid="rotator-tiles">
          <ReadoutTile
            label="SKY PA"
            value={`${rot.sky_deg.toFixed(1)}°`}
            sub={rot.synced ? "synced" : "not synced - sync to sky"}
            tone={rot.synced ? undefined : "warn"}
            data-testid="tile-sky-pa"
          />
          <ReadoutTile
            label="MECHANICAL"
            value={`${rot.mech_deg.toFixed(1)}°`}
            // offset = mechanical - sky (lib/rotation.ts:105). Not on the wire;
            // derived from the two live values, the same way `adjustedPa` does.
            sub={`offset ${mod360(rot.mech_deg - rot.sky_deg).toFixed(1)}°`}
            data-testid="tile-mechanical"
          />
          <ReadoutTile
            label="RANGE"
            value={RANGE_LABEL[cfg.range_type] ?? cfg.range_type.toUpperCase()}
            sub={`start ${cfg.range_start_deg}° · tol ${cfg.tolerance_deg}°`}
            data-testid="tile-range"
          />
        </ReadoutGrid>
      )}

      {/* The dial, the motion controls, the range-of-motion block and BOTH lock
          notes, mounted whole. `RotatorCard` returns null with no rotator, which
          is why the EmptyCard above is the sheet's own answer to that state. */}
      <RotatorCard />

      <Card data-testid="rotator-framing">
        <ListRow
          title="FRAMING SENDS ITS PA HERE"
          sub={FRAMING_NOTE}
          data-testid="rotator-framing-row"
        />
        {rot && !rot.synced && (
          <p style={{ fontSize: "11.5px", lineHeight: 1.5, color: "var(--warn)", padding: "0 2px", margin: 0 }}
            data-testid="rotator-unsynced-warning">{UNSYNCED_WARNING}</p>
        )}
      </Card>

      <p style={{ fontSize: "11.5px", lineHeight: 1.5, color: "var(--text-faint)", padding: "0 2px", margin: 0 }}
        data-testid="rotator-footer">
        Angles here are sky position angle; the mechanical readout is the raw
        device angle. The shaded arc is the allowed range of motion - set its
        start by turning to a cable-safe position and pressing SET TO CURRENT
        POSITION. Moves are refused during an exposure.
      </p>
      <div style={{ height: "8px", flexShrink: 0 }} />
    </Sheet>
  );
}

export default RotatorSheet;
