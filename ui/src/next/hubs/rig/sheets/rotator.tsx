// rotator.tsx - the ROTATOR device sheet (plan hub-rig.md B.8; GAP-ANALYSIS
// section 2 "Missing - rotator"; deviation E28).
//
// THERE IS NO DESIGN FRAGMENT FOR THIS SHEET. The design's device list has no
// rotator row at all, so the sheet is built in the shared device-sheet language
// of hub-rig.md 0.2/0.3: a header with one live line, a 4-column readout grid
// where the selected tile is what the one dial edits, and cards below it.
//
// STAGE 2, CLOSING THE STAGE-1 DECISION THIS FILE USED TO DOCUMENT. Wave 1
// mounted `components/equipment/RotatorCard.tsx` whole - a `Panel`, eleven
// native `disabled` attributes and a hand-rolled radiogroup inside a device
// sheet - and named the full rebuild as a follow-up. Wave R7's T-R7-8 is that
// follow-up: `hubs/rig/rotator/RotatorPanel.tsx` re-implements the presentation
// and SHARES the logic that must never be re-derived (`lib/rotation.ts`'s
// `adjustedPa`/`mod360`, where the sky/mechanical offset is DERIVED because it
// is not on the wire, and `lib/rotatorDial.ts`'s arc geometry). The legacy card
// is not edited, not deleted and not imported from anywhere under `next/`; it
// still serves `#/classic`.
//
// WHAT THE SHEET OWNS, AS OPPOSED TO THE PANEL:
//
//   - The header and its ONE live line, which carries the numbers the body does
//     not repeat: sky PA, mechanical angle, and whether the two are related by a
//     KNOWN offset (`synced`) or an unknown one.
//   - The framing hand-off row (GAP-2: "Framing already has camera rotation;
//     when a rotator exists, DONE should send the PA to it"). This sheet does
//     NOT implement the hand-off - the Sky hub owns framing - so the row states
//     the contract and, when the rotator is not synced, warns that the angle
//     framing sends would be off by the mechanical offset.
//   - An EmptyCard when no rotator is connected. A route that renders nothing
//     is a dead end, so the sheet answers that state itself and the panel is
//     only ever mounted with a device to talk to.
//
// THE SPLIT GATE IS DELIBERATE. Motion (move / halt / reverse / rotate-to-pa /
// sync-to-sky) needs `control.capture`; the range-of-motion CONFIG needs
// `config.backend`. The server enforces both separately (app.py:6125-6199 vs
// the `config.backend` gate on `POST /api/config/rotator`), the panel states
// both in its own two lock notes, and an operator without `config.backend`
// must keep a live rotator.

import type { JSX } from "react";
import { useConfig, useStatus, useStore } from "../../../../store";
import type { RotatorConfig } from "../../../../types";
import { resolveRoleConnected } from "../../../../lib/caps";
import { RotatorPanel, DEFAULT_ROTATOR_CFG } from "../rotator";
import { Sheet, Card, ActionButton, EmptyCard, ListRow } from "../../../ui";
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

export function RotatorSheet(): JSX.Element {
  const status = useStatus();
  const config = useConfig();
  const equipConnected = useStore((s) => s.equipConnected);
  const rot = status?.rotator ?? null;
  const role = resolveRoleConnected(
    "rotator", status?.backend_links, status?.connected, equipConnected,
  );
  // Read here only for the live line's tolerance-free summary; the panel holds
  // its own draft of the same block.
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
    : `not connected · range ${cfg.range_type}`;

  return (
    <Sheet
      title="ROTATOR"
      // The device's own name. The legacy card carried it in its `Panel` title
      // (`Rotator · ZWO CAA`) and nothing else in the new chrome names WHICH
      // rotator this is - a rig with a CAA and a third-party rotator declared
      // would otherwise show two identical sheets.
      sub={rot?.name}
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

      {/* The tiles, the dial, the arc, the motion controls, the range-of-motion
          block and BOTH lock notes. */}
      {rot && <RotatorPanel rot={rot} />}

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
