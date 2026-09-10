// RotatorArc.tsx - the 120 px range-of-motion dial, DISPLAY ONLY.
//
// Deliberately not drag-interactive, and that decision is older than this
// rebuild (`components/equipment/RotatorCard.tsx:6`): the person looking at
// this is at the scope in the dark wearing gloves, and a 120 px circle that
// commands a rotation on a stray touch is a cable wrap. Every input goes
// through the dial strip, the MOVE TO field and the buttons below it, all of
// which are 44 px and honest-disabled.
//
// What it shows that the numbers beside it do not: WHERE in the allowed sweep
// the rotator currently is. The tiles can say "mech 100.5 deg" and "start 20
// deg, 180 deg sweep" and still leave a person working out whether the next
// nudge runs into the end of the range.

import type { JSX } from "react";
import { allowedSweepDeg, arcPath, polarXY } from "../../../../lib/rotatorDial";
import type { RotatorConfig, RotatorStatus } from "../../../../types";

const SIZE = 120;
const CX = 60;
const CY = 60;
const R = 48;

export function RotatorArc({ rot, cfg }: {
  rot: RotatorStatus;
  cfg: RotatorConfig;
}): JSX.Element {
  const sweep = allowedSweepDeg(cfg.range_type);
  const cur = polarXY(CX, CY, R, rot.mech_deg);
  const startMark = polarXY(CX, CY, R, cfg.range_start_deg);

  // The label is the whole picture in words, because the picture is the only
  // place this relationship is drawn: a reader gets the position, the sweep and
  // where the sweep starts, not "chart".
  const label = sweep === 360
    ? `Rotator at ${rot.mech_deg.toFixed(1)} degrees mechanical, sky position `
      + `angle ${rot.sky_deg.toFixed(1)} degrees, free to turn the full circle`
    : `Rotator at ${rot.mech_deg.toFixed(1)} degrees mechanical, sky position `
      + `angle ${rot.sky_deg.toFixed(1)} degrees, inside a ${sweep} degree range `
      + `starting at ${cfg.range_start_deg} degrees`;

  return (
    <svg
      className="nx-rot-arc"
      width={SIZE}
      height={SIZE}
      viewBox={`0 0 ${SIZE} ${SIZE}`}
      role="img"
      aria-label={label}
      data-moving={rot.moving ? "true" : "false"}
      data-testid="rotator-arc"
    >
      <circle cx={CX} cy={CY} r={R} fill="none" stroke="var(--line-bright)" strokeWidth={2} />
      {sweep < 360 && (
        <path
          d={arcPath(CX, CY, R, cfg.range_start_deg, sweep)}
          fill="none"
          stroke="var(--accent)"
          strokeOpacity={0.55}
          strokeWidth={5}
        />
      )}
      {sweep === 360 && (
        <circle cx={CX} cy={CY} r={R} fill="none" stroke="var(--accent)"
          strokeOpacity={0.35} strokeWidth={5} />
      )}
      {sweep < 360 && (
        <circle cx={startMark.x} cy={startMark.y} r={3.5} fill="var(--warn)"
          data-testid="rotator-arc-start" />
      )}
      <circle
        className="nx-rot-arc-cur"
        cx={cur.x}
        cy={cur.y}
        r={4.5}
        fill="var(--accent)"
        stroke="var(--bg)"
        strokeWidth={1.5}
        data-testid="rotator-arc-cur"
      />
      <text x={CX} y={CY - 4} textAnchor="middle" fill="var(--text)" fontSize="13"
        fontFamily="var(--font-mono, monospace)">
        {`${rot.sky_deg.toFixed(1)}°`}
      </text>
      <text x={CX} y={CY + 12} textAnchor="middle" fill="var(--text-dim)" fontSize="10">
        {`mech ${rot.mech_deg.toFixed(1)}°${rot.synced ? "" : " · unsynced"}`}
      </text>
    </svg>
  );
}
