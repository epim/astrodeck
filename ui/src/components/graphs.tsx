/** Hand-rolled SVG instrument graphs: histogram, V-curve, guide scatter.
 *  Night-safe strokes (data numbers -> --text-dim, gridline strokes -> --text-faint),
 *  larger relative label fonts, no preserveAspectRatio="none" for data graphs
 *  (use vector-effect non-scaling-stroke so strokes stay crisp without distortion),
 *  Histogram collapsed to a single <path>, guide graphs React.memo'd (perf review). */
import { memo, useMemo } from "react";
import type { FocusPoint } from "../types";

export const Histogram = memo(function Histogram({ data }: { data: number[] }) {
  const w = 256, h = 64;
  const d = useMemo(() => {
    if (!data.length) return "";
    const logMax = Math.log10(Math.max(...data) + 1);
    const bw = w / data.length;
    // single filled path (one element instead of N rects)
    let path = `M0,${h}`;
    for (let i = 0; i < data.length; i++) {
      const bh = logMax > 0 ? (Math.log10(data[i] + 1) / logMax) * h : 0;
      const x = i * bw;
      path += ` L${x.toFixed(2)},${(h - bh).toFixed(2)} L${(x + bw).toFixed(2)},${(h - bh).toFixed(2)}`;
    }
    path += ` L${w},${h} Z`;
    return path;
  }, [data]);
  if (!d) return null;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16 block">
      <path d={d} fill="var(--accent)" opacity={0.75} vectorEffect="non-scaling-stroke" />
    </svg>
  );
});

export const VCurve = memo(function VCurve({ points, best }: {
  points: FocusPoint[];
  best: { position: number; hfr: number | null } | null;
}) {
  const w = 420, h = 180, pad = 30;
  if (points.length === 0) {
    return (
      <div className="h-[180px] flex items-center justify-center text-dim text-xs tracking-widest uppercase">
        no focus data — run autofocus
      </div>
    );
  }
  const xs = points.map((p) => p.position);
  const ys = points.map((p) => p.hfr);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const y0 = Math.min(...ys) * 0.85, y1 = Math.max(...ys) * 1.1;
  const sx = (x: number) => pad + ((x - x0) / Math.max(x1 - x0, 1)) * (w - 2 * pad);
  const sy = (y: number) => h - pad - ((y - y0) / Math.max(y1 - y0, 0.01)) * (h - 2 * pad);
  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.position)},${sy(p.hfr)}`).join(" ");
  // aspect-locked + centered so fill-grow never distorts the V (asymmetric minimum = misleading)
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full block instr-fit" style={{ aspectRatio: `${w} / ${h}` }}>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1={pad} x2={w - pad} y1={pad + f * (h - 2 * pad)} y2={pad + f * (h - 2 * pad)}
          stroke="var(--text-faint)" strokeDasharray="2 4" vectorEffect="non-scaling-stroke" />
      ))}
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      {points.map((p, i) => (
        <circle key={i} cx={sx(p.position)} cy={sy(p.hfr)} r={3} fill="var(--bg)"
          stroke="var(--accent)" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
      ))}
      {best && (
        <g>
          <line x1={sx(best.position)} x2={sx(best.position)} y1={pad / 2} y2={h - pad}
            stroke="var(--good)" strokeWidth={1} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
          <text x={sx(best.position)} y={pad / 2 - 2} textAnchor="middle" fill="var(--text-dim)"
            fontSize={12} fontFamily="IBM Plex Mono">{best.position}</text>
        </g>
      )}
      <text x={pad} y={h - 6} fill="var(--text-dim)" fontSize={12} fontFamily="IBM Plex Mono">{x0}</text>
      <text x={w - pad} y={h - 6} textAnchor="end" fill="var(--text-dim)" fontSize={12} fontFamily="IBM Plex Mono">{x1}</text>
    </svg>
  );
});

export const GuideGraph = memo(function GuideGraph({ samples }: { samples: { t: number; ra: number; dec: number }[] }) {
  const w = 420, h = 140, mid = h / 2;
  const scale = 20; // px per arcsec
  const n = Math.max(samples.length, 1);
  const sx = (i: number) => (i / Math.max(n - 1, 1)) * w;
  const line = (key: "ra" | "dec") =>
    samples.map((s, i) => `${i === 0 ? "M" : "L"}${sx(i)},${mid - s[key] * scale}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full block">
      {[-2, -1, 1, 2].map((a) => (
        <line key={a} x1={0} x2={w} y1={mid - a * scale} y2={mid - a * scale}
          stroke="var(--text-faint)" strokeDasharray="2 5" vectorEffect="non-scaling-stroke" />
      ))}
      <line x1={0} x2={w} y1={mid} y2={mid} stroke="var(--line-bright)" vectorEffect="non-scaling-stroke" />
      {samples.length > 1 && (
        <>
          <path d={line("ra")} fill="none" stroke="var(--accent)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
          <path d={line("dec")} fill="none" stroke="var(--warn)" strokeWidth={1.2} vectorEffect="non-scaling-stroke" />
        </>
      )}
      <text x={4} y={14} fill="var(--accent)" fontSize={12} fontFamily="IBM Plex Mono">RA</text>
      <text x={30} y={14} fill="var(--warn)" fontSize={12} fontFamily="IBM Plex Mono">DEC</text>
      <text x={w - 4} y={mid - 2 * scale - 3} textAnchor="end" fill="var(--text-dim)" fontSize={11}
        fontFamily="IBM Plex Mono">+2"</text>
    </svg>
  );
});

export const GuideScatter = memo(function GuideScatter({ samples }: { samples: { ra: number; dec: number }[] }) {
  const size = 140, mid = size / 2, scale = 22;
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-[140px] h-[140px] block shrink-0">
      {[1, 2].map((r) => (
        <circle key={r} cx={mid} cy={mid} r={r * scale} fill="none" stroke="var(--text-faint)"
          vectorEffect="non-scaling-stroke" />
      ))}
      <line x1={mid} x2={mid} y1={0} y2={size} stroke="var(--text-faint)" vectorEffect="non-scaling-stroke" />
      <line x1={0} x2={size} y1={mid} y2={mid} stroke="var(--text-faint)" vectorEffect="non-scaling-stroke" />
      {samples.slice(-60).map((s, i, arr) => (
        <circle key={i} cx={mid + s.ra * scale} cy={mid - s.dec * scale} r={1.5}
          fill={i === arr.length - 1 ? "var(--good)" : "var(--accent)"}
          opacity={0.25 + (0.75 * i) / arr.length} />
      ))}
    </svg>
  );
});
