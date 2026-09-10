// CloudChart.tsx - CLOUD · NEXT 24 H.
//
// The chart maths are LIFTED from `components/weather/SkyConditionsPanel.tsx`
// (the `chart` useMemo at :172-209 and the `<svg>` body at :227-310), not
// rewritten: `breachSpans`, `fmtHm` and `groupEndLabels` come from
// `lib/weather.ts` so the shading here and the headline above it can never
// disagree about what a sustained breach is. The panel itself is untouched and
// still serves `#/classic`.
//
// The LOOK is the design's (`proto/12-weather-dome.html`, CLOUD · NEXT 24 H):
// total solid accent, high dotted, mid dashed, low finely dashed, an amber
// threshold rule, a blue dark-window band, amber breach shading and a white NOW
// line. The dash patterns are load-bearing rather than decorative - under night
// mode the token swap takes the hues with it, so the series have to be
// distinguishable by shape (the same rule the panel's own comment states).
//
// This component takes the ALREADY WINDOWED samples rather than the forecast:
// `windowSamples` lives in `verdict.ts` and the screen calls it once, so the
// picture and the sentence above it are two renderings of one array.

import type { JSX } from "react";
import { breachSpans, fmtHm, groupEndLabels } from "../../../../lib/weather";
import { Label, Mono } from "../../../ui";
import type { Windowed } from "./verdict";
import { HORIZON_S } from "./verdict";

// Typography: the panel's own retuned sizes (its F7 smoke test found 8-9 px
// "unreadable"), which also satisfy ARCHITECTURE.md #0.4's 10 px floor.
const AXIS_FONT_PX = 11;
const SERIES_LABEL_FONT_PX = 11;
const THRESHOLD_LABEL_FONT_PX = 12;
const END_LABEL_MIN_GAP_PX = SERIES_LABEL_FONT_PX + 2;

const W = 480;
const H = 130;
const PAD_L = 34;
const PAD_R = 54;
const PAD_T = 10;
const PAD_B = 20;
const PLOT_W = W - PAD_L - PAD_R;
const PLOT_H = H - PAD_T - PAD_B;

export interface CloudChartProps {
  win: Windowed;
  thresholdPct: number;
  sustainMinutes: number;
  dark: { start_iso: string; end_iso: string } | null;
  nowTs: number;
}

interface LegendKey {
  label: string;
  color: string;
  dash?: string;
  width?: number;
  block?: boolean;
}

/** `total · high · mid · low · dark · hold`, the fragment's own row. */
const LEGEND: LegendKey[] = [
  { label: "total", color: "var(--accent)", width: 2 },
  { label: "high", color: "var(--text-dim)", dash: "1 4", width: 1.4 },
  { label: "mid", color: "var(--text-dim)", dash: "6 4", width: 1.4 },
  { label: "low", color: "var(--text-dim)", dash: "2 3", width: 1.4 },
  { label: "dark", color: "rgba(77,124,255,.30)", block: true },
  { label: "hold", color: "rgba(255,180,84,.35)", block: true },
];

export function CloudChart({
  win, thresholdPct, sustainMinutes, dark, nowTs,
}: CloudChartProps): JSX.Element {
  const t0 = nowTs;
  const sx = (t: number): number => PAD_L + ((t - t0) / HORIZON_S) * PLOT_W;
  const sy = (pct: number): number => PAD_T + (1 - pct / 100) * PLOT_H;
  const path = (vals: number[]): string =>
    win.ts
      .map((t, i) => `${i === 0 ? "M" : "L"}${sx(t).toFixed(1)},${sy(vals[i]).toFixed(1)}`)
      .join(" ");

  const spans = breachSpans(win.cloud, thresholdPct, sustainMinutes);

  let band: { x0: number; x1: number } | null = null;
  if (dark) {
    const ds = Date.parse(dark.start_iso) / 1000;
    const de = Date.parse(dark.end_iso) / 1000;
    if (Number.isFinite(ds) && Number.isFinite(de) && de > t0 && ds < t0 + HORIZON_S) {
      band = { x0: sx(Math.max(ds, t0)), x1: sx(Math.min(de, t0 + HORIZON_S)) };
    }
  }

  const ticks: { x: number; label: string }[] = [];
  for (let h = 0; h <= 24; h += 6) {
    const t = t0 + h * 3600;
    ticks.push({ x: sx(t), label: fmtHm(new Date(t * 1000).toISOString()) });
  }

  // Coincident series (an all-zero night stacks four labels on the baseline)
  // are consolidated into one row rather than overprinting - lib/weather.ts's
  // groupEndLabels, the same helper the panel uses.
  const endLabels = groupEndLabels(
    [
      { name: "total", y: sy(win.cloud[win.cloud.length - 1]) },
      { name: "low", y: sy(win.low[win.low.length - 1]) },
      { name: "mid", y: sy(win.mid[win.mid.length - 1]) },
      { name: "high", y: sy(win.high[win.high.length - 1]) },
    ],
    END_LABEL_MIN_GAP_PX,
  );

  const thrY = sy(thresholdPct);
  const darkLabel = band && dark
    ? ` · dark ${fmtHm(dark.start_iso)} - ${fmtHm(dark.end_iso)}`
    : "";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} data-testid="wx-cloud-chart">
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8,
      }}>
        <Label size={10}>CLOUD · NEXT 24 H</Label>
        <Mono size={10} tone="dim">
          {`now ${Math.round(win.cloud[0] ?? 0)}%`}
          {darkLabel}
        </Mono>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: "100%", display: "block" }}
        role="img"
        aria-label="Cloud cover forecast, next 24 hours"
      >
        {band && (
          <rect
            x={band.x0}
            y={PAD_T}
            width={Math.max(0, band.x1 - band.x0)}
            height={PLOT_H}
            fill="rgba(77,124,255,.12)"
          />
        )}
        {spans.map((s, i) => (
          <rect
            key={`b${i}`}
            x={sx(win.ts[s.start])}
            y={PAD_T}
            width={Math.max(1, sx(win.ts[Math.min(s.end + 1, win.ts.length - 1)]) - sx(win.ts[s.start]))}
            height={PLOT_H}
            fill="rgba(255,180,84,.18)"
          />
        ))}

        {/* The rule, labelled with the policy that draws it, so the line and
            the sentence explaining it come from one payload. */}
        <line
          x1={PAD_L} x2={W - PAD_R} y1={thrY} y2={thrY}
          stroke="var(--warn)" strokeOpacity={0.75} strokeDasharray="4 4"
        />
        <text
          x={PAD_L + 3}
          y={Math.max(PAD_T + THRESHOLD_LABEL_FONT_PX, thrY - 3)}
          fill="var(--warn)"
          fontSize={THRESHOLD_LABEL_FONT_PX}
          fontFamily="IBM Plex Mono"
        >
          {`hold ≥${thresholdPct}% for ${sustainMinutes}m`}
        </text>

        <line x1={PAD_L} x2={PAD_L} y1={PAD_T} y2={PAD_T + PLOT_H} stroke="var(--text)" />
        <text
          x={PAD_L + 3} y={PAD_T + AXIS_FONT_PX - 2}
          fill="var(--text)" fontSize={AXIS_FONT_PX} fontFamily="IBM Plex Mono"
        >
          NOW
        </text>

        <path d={path(win.high)} fill="none" stroke="var(--text-dim)" strokeWidth={1.4} strokeDasharray="1 4" strokeLinecap="round" />
        <path d={path(win.mid)} fill="none" stroke="var(--text-dim)" strokeWidth={1.4} strokeDasharray="6 4" />
        <path d={path(win.low)} fill="none" stroke="var(--text-dim)" strokeWidth={1.4} strokeDasharray="2 3" />
        <path d={path(win.cloud)} fill="none" stroke="var(--accent)" strokeWidth={2} strokeLinejoin="round" />

        {endLabels.map((g) => (
          <text
            key={g.label}
            x={W - PAD_R + 3}
            y={g.y + 3}
            fill={g.label.split("+").includes("total") ? "var(--accent)" : "var(--text-dim)"}
            fontSize={SERIES_LABEL_FONT_PX}
            fontFamily="IBM Plex Mono"
          >
            {g.label}
          </text>
        ))}

        <text x={2} y={PAD_T + AXIS_FONT_PX} fill="var(--text-dim)" fontSize={AXIS_FONT_PX} fontFamily="IBM Plex Mono">100%</text>
        <text x={2} y={PAD_T + PLOT_H} fill="var(--text-dim)" fontSize={AXIS_FONT_PX} fontFamily="IBM Plex Mono">0%</text>
        {ticks.map((t, i) => (
          <text
            key={`t${i}`} x={t.x} y={H - 5} textAnchor="middle"
            fill="var(--text-faint)" fontSize={AXIS_FONT_PX} fontFamily="IBM Plex Mono"
          >
            {t.label}
          </text>
        ))}
      </svg>

      <div style={{
        display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center",
      }}>
        {LEGEND.map((k) => (
          <span key={k.label} style={{ display: "flex", alignItems: "center", gap: 5 }}>
            {k.block ? (
              <span style={{ width: 10, height: 8, background: k.color, display: "inline-block" }} />
            ) : (
              <svg width="18" height="4" viewBox="0 0 18 4" aria-hidden="true">
                <line
                  x1="0" y1="2" x2="18" y2="2"
                  stroke={k.color} strokeWidth={k.width ?? 1.4}
                  strokeDasharray={k.dash} strokeLinecap={k.dash === "1 4" ? "round" : undefined}
                />
              </svg>
            )}
            <Mono size={10} tone="dim">{k.label}</Mono>
          </span>
        ))}
      </div>
    </div>
  );
}
