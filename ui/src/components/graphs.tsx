/** Hand-rolled SVG instrument graphs: histogram, V-curve, guide scatter. */
import type { FocusPoint } from "../types";

export function Histogram({ data }: { data: number[] }) {
  const w = 256, h = 64;
  if (!data.length) return null;
  const logMax = Math.log10(Math.max(...data) + 1);
  const bw = w / data.length;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-16 block" preserveAspectRatio="none">
      {data.map((v, i) => {
        const bh = logMax > 0 ? (Math.log10(v + 1) / logMax) * h : 0;
        return (
          <rect key={i} x={i * bw} y={h - bh} width={Math.max(bw - 0.4, 0.6)} height={bh}
            fill="var(--accent)" opacity={0.75} />
        );
      })}
    </svg>
  );
}

export function VCurve({ points, best }: {
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
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full block">
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1={pad} x2={w - pad} y1={pad + f * (h - 2 * pad)} y2={pad + f * (h - 2 * pad)}
          stroke="var(--line)" strokeDasharray="2 4" />
      ))}
      <path d={path} fill="none" stroke="var(--accent)" strokeWidth={1.5} />
      {points.map((p, i) => (
        <circle key={i} cx={sx(p.position)} cy={sy(p.hfr)} r={3} fill="var(--bg)"
          stroke="var(--accent)" strokeWidth={1.5} />
      ))}
      {best && (
        <g>
          <line x1={sx(best.position)} x2={sx(best.position)} y1={pad / 2} y2={h - pad}
            stroke="var(--good)" strokeWidth={1} strokeDasharray="4 3" />
          <text x={sx(best.position)} y={pad / 2 - 2} textAnchor="middle" fill="var(--good)"
            fontSize={10} fontFamily="IBM Plex Mono">{best.position}</text>
        </g>
      )}
      <text x={pad} y={h - 6} fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">{x0}</text>
      <text x={w - pad} y={h - 6} textAnchor="end" fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">{x1}</text>
    </svg>
  );
}

export function GuideGraph({ samples }: { samples: { t: number; ra: number; dec: number }[] }) {
  const w = 420, h = 140, mid = h / 2;
  const scale = 20; // px per arcsec
  const n = Math.max(samples.length, 1);
  const sx = (i: number) => (i / Math.max(n - 1, 1)) * w;
  const line = (key: "ra" | "dec") =>
    samples.map((s, i) => `${i === 0 ? "M" : "L"}${sx(i)},${mid - s[key] * scale}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full block" preserveAspectRatio="none">
      {[-2, -1, 1, 2].map((a) => (
        <line key={a} x1={0} x2={w} y1={mid - a * scale} y2={mid - a * scale}
          stroke="var(--line)" strokeDasharray="2 5" />
      ))}
      <line x1={0} x2={w} y1={mid} y2={mid} stroke="var(--line-bright)" />
      {samples.length > 1 && (
        <>
          <path d={line("ra")} fill="none" stroke="var(--accent)" strokeWidth={1.2} />
          <path d={line("dec")} fill="none" stroke="var(--warn)" strokeWidth={1.2} />
        </>
      )}
      <text x={4} y={12} fill="var(--accent)" fontSize={9} fontFamily="IBM Plex Mono">RA</text>
      <text x={26} y={12} fill="var(--warn)" fontSize={9} fontFamily="IBM Plex Mono">DEC</text>
      <text x={w - 4} y={mid - 2 * scale - 3} textAnchor="end" fill="var(--text-dim)" fontSize={8}
        fontFamily="IBM Plex Mono">+2"</text>
    </svg>
  );
}

export function GuideScatter({ samples }: { samples: { ra: number; dec: number }[] }) {
  const size = 140, mid = size / 2, scale = 22;
  return (
    <svg viewBox={`0 0 ${size} ${size}`} className="w-[140px] h-[140px] block shrink-0">
      {[1, 2].map((r) => (
        <circle key={r} cx={mid} cy={mid} r={r * scale} fill="none" stroke="var(--line)" />
      ))}
      <line x1={mid} x2={mid} y1={0} y2={size} stroke="var(--line)" />
      <line x1={0} x2={size} y1={mid} y2={mid} stroke="var(--line)" />
      {samples.slice(-60).map((s, i, arr) => (
        <circle key={i} cx={mid + s.ra * scale} cy={mid - s.dec * scale} r={1.5}
          fill={i === arr.length - 1 ? "var(--good)" : "var(--accent)"}
          opacity={0.25 + (0.75 * i) / arr.length} />
      ))}
    </svg>
  );
}
