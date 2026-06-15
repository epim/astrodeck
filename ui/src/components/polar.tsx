/** Polar alignment bullseye reticle (treatment A).
 *  Center = perfect alignment; the dot is the mount's pole; the vector is the
 *  skew. Rings are arcminute tolerance bands and auto-zoom as error shrinks. */

function zoneColor(total: number): string {
  return total < 1 ? "var(--good)" : total < 5 ? "var(--warn)" : "var(--bad)";
}

export function PolarReticle({ az, alt, autoZoom = true }: {
  az: number; alt: number; autoZoom?: boolean;
}) {
  const size = 380, cx = size / 2, cy = size / 2, R = 165;
  const total = Math.hypot(az, alt);
  const smax = autoZoom ? Math.max(2, Math.ceil((total * 1.35) / 2) * 2) : 10;
  const k = R / smax;
  const col = zoneColor(total);

  // clamp the dot to the outer ring so a large error still reads on-screen
  const rawD = Math.hypot(az * k, alt * k);
  const scale = rawD > R ? R / rawD : 1;
  const dx = cx + az * k * scale;
  const dy = cy - alt * k * scale;

  const step = smax <= 4 ? 1 : 2;
  const rings: number[] = [];
  for (let m = step; m <= smax; m += step) rings.push(m);

  const labels: [number, number, string, string][] = [
    [cx, cy - R + 2, "ALT +", "middle"],
    [cx, cy + R - 1, "ALT −", "middle"],
    [cx - R + 2, cy - 5, "AZ E", "start"],
    [cx + R - 2, cy - 5, "AZ W", "end"],
  ];

  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-full max-w-[420px] mx-auto block instr-fit"
      style={{ aspectRatio: "1 / 1" }}>
      <defs>
        <marker id="pa-arrow" markerWidth="7" markerHeight="7" refX="5" refY="3" orient="auto">
          <path d="M0,0 L6,3 L0,6 Z" fill={col} />
        </marker>
      </defs>

      {/* tolerance fills */}
      <circle cx={cx} cy={cy} r={Math.min(5, smax) * k} fill="var(--bad)" fillOpacity={0.05} />
      <circle cx={cx} cy={cy} r={Math.min(1, smax) * k} fill="var(--good)" fillOpacity={0.09} />

      {/* rings + arcmin labels */}
      {rings.map((m) => (
        <g key={m}>
          <circle cx={cx} cy={cy} r={m * k} fill="none" stroke="var(--line-bright)"
            strokeWidth={m === smax ? 1.2 : 0.7} strokeDasharray={m === smax ? "" : "2 5"} />
          <text x={cx + 4} y={cy - m * k + 12} fill="var(--text-dim)" fontSize={11}
            fontFamily="IBM Plex Mono">{m}'</text>
        </g>
      ))}

      {/* crosshair + axis labels */}
      <line x1={cx - R} y1={cy} x2={cx + R} y2={cy} stroke="var(--line)" />
      <line x1={cx} y1={cy - R} x2={cx} y2={cy + R} stroke="var(--line)" />
      {labels.map(([x, y, t, anchor], i) => (
        <text key={i} x={x} y={y} fill="var(--text-dim)" fontSize={11} fontFamily="Chakra Petch"
          letterSpacing="2" textAnchor={anchor as "middle" | "start" | "end"}>{t}</text>
      ))}

      {/* skew vector + error dot */}
      <line x1={cx} y1={cy} x2={dx} y2={dy} stroke={col} strokeWidth={2} markerEnd="url(#pa-arrow)" />
      <circle cx={dx} cy={dy} r={7} fill="var(--bg)" stroke={col} strokeWidth={2} />
      <circle cx={dx} cy={dy} r={2.5} fill={col} />

      {/* true-pole target */}
      <circle cx={cx} cy={cy} r={3} fill="none" stroke="var(--good)" strokeWidth={1.2} />
      <circle cx={cx} cy={cy} r={1} fill="var(--good)" />
    </svg>
  );
}
