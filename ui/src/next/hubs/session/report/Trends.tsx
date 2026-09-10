// Trends.tsx - HFR, sensor temperature and guide RMS across the night.
//
// The PLOT is not design vocabulary and is not invented here: the geometry
// comes from `lib/reportChart.ts`'s `trendGeomTimed`, the same tested helper
// the classic view's `TrendLine` uses. Only the chrome is rebuilt, which is
// the whole rule of this wave.
//
// One fix rides along. The classic view passed `values` only, so the x axis
// was the sample INDEX - and each of these three series is downsampled
// independently (HFR on lights only, temperature on every frame), so an hour
// of guiding recovery compressed to one pixel step and the three panels did
// not line up in wall-clock time. The report carries `[ts, value]` pairs; this
// rebuild plots them on a real time axis and captions the span, so a gap in
// the line is a gap in the night.

import type { JSX } from "react";
import { Card, Label, Mono } from "../../../ui";
import { trendGeomTimed } from "../../../../lib/reportChart";
import type { SessionReport } from "../../../../types";
import { fmtClock, REPORT_COPY, trendPoints } from "./reportModel";

const W = 240;
const H = 56;

function Trend({ pairs, label, unit, decimals, testid }: {
  pairs: [number, number][];
  label: string;
  unit: string;
  decimals: number;
  testid: string;
}): JSX.Element {
  const points = trendPoints(pairs);
  const geom = trendGeomTimed(points, W, H);
  const last = points.length ? points[points.length - 1].v : null;

  return (
    <div className="nx-report-trend" data-testid={testid}>
      <div className="nx-report-head">
        <Label size={10}>{label}</Label>
        <Mono size={10} tone="dim">
          {last != null ? `${last.toFixed(decimals)}${unit}` : "-"}
        </Mono>
      </div>
      {geom == null ? (
        <Mono size={10} tone="dim">{REPORT_COPY.trendNone}</Mono>
      ) : (
        <>
          <svg
            className="nx-report-plot"
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            role="img"
            aria-label={`${label} across the night`}
          >
            <path
              d={geom.d}
              fill="none"
              stroke="var(--accent)"
              strokeWidth={1.4}
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          <div className="nx-report-axis">
            <Mono size={10} tone="dim">{`${geom.yMin.toFixed(decimals)}${unit}`}</Mono>
            <Mono size={10} tone="dim">{`${geom.yMax.toFixed(decimals)}${unit}`}</Mono>
          </div>
          {geom.tMin != null && geom.tMax != null && (
            <div className="nx-report-axis">
              <Mono size={10}>{fmtClock(geom.tMin)}</Mono>
              <Mono size={10}>{fmtClock(geom.tMax)}</Mono>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function Trends({ trends }: { trends: SessionReport["trends"] }): JSX.Element {
  return (
    <Card data-testid="report-trends">
      <div className="nx-report-block">
        <Label size={11}>TRENDS</Label>
        <div className="nx-report-trends">
          <Trend
            pairs={trends.hfr} label="HFR" unit="" decimals={2}
            testid="report-trend-hfr"
          />
          <Trend
            pairs={trends.temp} label="SENSOR" unit=" C" decimals={1}
            testid="report-trend-temp"
          />
          <Trend
            pairs={trends.rms} label="GUIDE RMS" unit=" arcsec" decimals={2}
            testid="report-trend-rms"
          />
        </div>
      </div>
    </Card>
  );
}
