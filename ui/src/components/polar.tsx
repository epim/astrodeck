/** Polar alignment bullseye reticle (treatment A).
 *  Center = perfect alignment; the dot is the mount's pole; the vector is the
 *  skew. Rings are arcminute tolerance bands and auto-zoom as error shrinks.
 *
 *  The reticle must always read as a CRISP concentric bullseye regardless of the
 *  error magnitude (0', 5', or 110' total). We therefore cap the visible rings to
 *  ~4 evenly spaced bands: the outer ring is the boundary (its label == smax) and
 *  we pick a "nice" arcmin step so that smax/step lands around 3–5 rings. */

function zoneColor(total: number): string {
  return total < 1 ? "var(--good)" : total < 5 ? "var(--warn)" : "var(--bad)";
}

// Nice-number ladder: choose the smallest step whose count (smax/step) is <= ~5,
// so a small smax gives a 1'/2' step and a huge smax (e.g. ~150') gives a 30–50'
// step. Guarantees ~3–5 clean rings at any zoom instead of a dense moiré.
const _STEP_LADDER = [1, 2, 5, 10, 20, 30, 50, 100, 150];
function niceStep(smax: number): number {
  for (const s of _STEP_LADDER) {
    if (smax / s <= 5) return s;
  }
  return _STEP_LADDER[_STEP_LADDER.length - 1];
}

export function PolarReticle({ az, alt, autoZoom = true }: {
  az: number; alt: number; autoZoom?: boolean;
}) {
  const size = 380, cx = size / 2, cy = size / 2, R = 165;
  const total = Math.hypot(az, alt);
  // Outer ring (smax) is a "nice" round arcmin just past the error so the dot
  // sits inside the boundary. autoZoom shrinks it as you converge.
  const smax = autoZoom ? Math.max(2, Math.ceil((total * 1.35) / 2) * 2) : 10;
  const k = R / smax;
  const col = zoneColor(total);

  // clamp the dot to the outer ring so a large error still reads on-screen
  const rawD = Math.hypot(az * k, alt * k);
  const scale = rawD > R ? R / rawD : 1;
  const dx = cx + az * k * scale;
  const dy = cy - alt * k * scale;

  // Build at most ~5 evenly spaced rings on the nice step, then always force the
  // outermost band to be exactly smax (the boundary). This keeps a clean bullseye
  // — the previous code emitted ~75 rings at large errors (a moiré).
  const step = niceStep(smax);
  const rings: number[] = [];
  for (let m = step; m < smax; m += step) rings.push(m);
  rings.push(smax); // boundary ring is always present + labelled with smax

  // Halo so axis + ring labels stay readable over rings/vector in day AND night:
  // paint a --bg stroke UNDER the fill (paint-order:stroke) for a subtle dark
  // outline on the light --text fill.
  const labelHalo = {
    paintOrder: "stroke" as const,
    stroke: "var(--bg)",
    strokeWidth: 3,
    strokeLinejoin: "round" as const,
  };

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

      {/* tolerance fills — clamp to smax so they never exceed the boundary ring */}
      <circle cx={cx} cy={cy} r={Math.min(5, smax) * k} fill="var(--bad)" fillOpacity={0.05} />
      <circle cx={cx} cy={cy} r={Math.min(1, smax) * k} fill="var(--good)" fillOpacity={0.09} />

      {/* rings (inner = dashed gridlines, outer = solid boundary) */}
      {rings.map((m) => (
        <circle key={m} cx={cx} cy={cy} r={m * k} fill="none" stroke="var(--line-bright)"
          strokeWidth={m === smax ? 1.2 : 0.7} strokeDasharray={m === smax ? "" : "2 5"} />
      ))}

      {/* crosshair */}
      <line x1={cx - R} y1={cy} x2={cx + R} y2={cy} stroke="var(--line)" />
      <line x1={cx} y1={cy - R} x2={cx} y2={cy + R} stroke="var(--line)" />

      {/* arcmin ring labels — drawn AFTER rings, with a dark halo, readable text
          color, and nudged off the vertical crosshair so they don't sit on the
          dot/vector. */}
      {rings.map((m) => (
        <text key={m} x={cx + 6} y={cy - m * k + 12} fill="var(--text)" fontSize={11}
          fontFamily="IBM Plex Mono" style={labelHalo}>{m}'</text>
      ))}

      {/* skew vector + error dot */}
      <line x1={cx} y1={cy} x2={dx} y2={dy} stroke={col} strokeWidth={2} markerEnd="url(#pa-arrow)" />
      <circle cx={dx} cy={dy} r={7} fill="var(--bg)" stroke={col} strokeWidth={2} />
      <circle cx={dx} cy={dy} r={2.5} fill={col} />

      {/* true-pole target */}
      <circle cx={cx} cy={cy} r={3} fill="none" stroke="var(--good)" strokeWidth={1.2} />
      <circle cx={cx} cy={cy} r={1} fill="var(--good)" />

      {/* axis labels — drawn LAST (above rings/vector) with halo + readable text */}
      {labels.map(([x, y, t, anchor], i) => (
        <text key={i} x={x} y={y} fill="var(--text)" fontSize={11} fontFamily="Chakra Petch"
          letterSpacing="2" textAnchor={anchor as "middle" | "start" | "end"}
          style={labelHalo}>{t}</text>
      ))}
    </svg>
  );
}
