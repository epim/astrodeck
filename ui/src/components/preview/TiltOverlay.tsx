// TiltOverlay.tsx — sensor-tilt / corner-vs-center aberration heatmap, inside
// the shared transform (PRO-13). Passive readout: NO reject gate, no
// interaction — pointer-events off.
//
// Encoding is NEVER color-alone (§11.1): each zone carries THREE redundant
// channels — a heat-banded fill (color), the zone's HFR printed as text
// (number), and an elongation tick (major-axis line, length ∝ ecc, angle =
// theta). Dataless zones (too few stars) render muted with no tick/label —
// honest: no data, not "zero".
import type { TiltInfo } from "../../types";
import { zoneHfrRange, heatFrac } from "../../lib/tilt";

interface Props {
  tilt: TiltInfo;
  dispW: number; // display px (== PreviewStage dispW)
  dispH: number;
}

export function TiltOverlay({ tilt, dispW, dispH }: Props) {
  const range = zoneHfrRange(tilt);
  const cw = dispW / tilt.cols;
  const ch = dispH / tilt.rows;
  return (
    <g style={{ pointerEvents: "none" }}>
      {tilt.zones.map((z, i) => {
        const r = Math.floor(i / tilt.cols);
        const c = i % tilt.cols;
        const x = c * cw;
        const y = r * ch;
        const f = z.hfr != null && range ? heatFrac(z.hfr, range.min, range.max) : null;
        const fill =
          f == null ? "var(--halo)" : f < 0.34 ? "var(--good)" : f < 0.67 ? "var(--warn)" : "var(--bad)";
        const op = f == null ? 0.06 : 0.16 + f * 0.22;
        const tickLen = z.ecc != null ? (Math.min(cw, ch) / 2) * 0.7 * z.ecc : 0;
        return (
          <g key={i}>
            <rect
              x={x}
              y={y}
              width={cw}
              height={ch}
              fill={fill}
              fillOpacity={op}
              stroke="var(--halo)"
              strokeOpacity={0.5}
              vectorEffect="non-scaling-stroke"
            />
            {z.hfr != null && (
              <text
                x={x + cw / 2}
                y={y + ch / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={Math.max(10, Math.min(cw, ch) * 0.14)}
                fill="var(--ink)"
                style={{ paintOrder: "stroke", stroke: "var(--halo)", strokeWidth: 3 }}
              >
                {z.hfr.toFixed(2)}
              </text>
            )}
            {tickLen > 0 && z.theta != null && (
              <line
                x1={x + cw / 2 - Math.cos(z.theta) * tickLen}
                y1={y + ch / 2 - Math.sin(z.theta) * tickLen}
                x2={x + cw / 2 + Math.cos(z.theta) * tickLen}
                y2={y + ch / 2 + Math.sin(z.theta) * tickLen}
                stroke="var(--ink)"
                strokeWidth={2}
                vectorEffect="non-scaling-stroke"
              />
            )}
          </g>
        );
      })}
    </g>
  );
}
