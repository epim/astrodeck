// TargetSpark.tsx — a ~120×28 altitude sparkline chip for the Plan tab's
// per-target cards (wave-3 §3). It lazily fetches tonight's visibility for the
// target's (rounded) coords + horizon limit and draws three layers a chip can
// carry: the astro-dark band, the alt-limit dashed line, and the target-altitude
// curve. Colors come from CSS vars only (design spec §8 — layers differentiate by
// stroke/dash/fill, not hue) and geometry from the pure, size-parameterized
// buildSparkGeometry (lib/visibility).
//
// Sharing: fetches route through a MODULE-LEVEL promise cache keyed on the rounded
// coords + alt limit, so N cards with duplicate coordinates (a mosaic's panels all
// share one center!) fire exactly ONE /api/visibility request for the page's
// lifetime, and re-renders never refetch. The cache is bounded by the number of
// DISTINCT (ra, dec, altLimit) targets per page load and is not persisted, so it
// resets on reload. A rejected fetch is evicted so a later remount can retry rather
// than being wedged on a cached failure.

import { useEffect, useMemo, useState } from "react";
import type { VisibilityNight } from "../../types";
import { api } from "../../api";
import { buildSparkGeometry, fmtAlt, fmtTime } from "../../lib/visibility";

const SPARK_W = 120;
const SPARK_H = 28;

const nightCache = new Map<string, Promise<VisibilityNight>>();

function loadNight(
  keyRa: number,
  keyDec: number,
  altLimit: number,
): Promise<VisibilityNight> {
  const key = `${keyRa}|${keyDec}|${altLimit}`;
  let p = nightCache.get(key);
  if (!p) {
    p = api.get<VisibilityNight>(
      `/api/visibility?ra=${encodeURIComponent(keyRa)}` +
        `&dec=${encodeURIComponent(keyDec)}` +
        `&alt_limit=${encodeURIComponent(altLimit)}`,
    );
    // Don't cache a failure forever — drop it so a later remount can retry.
    p.catch(() => {
      if (nightCache.get(key) === p) nightCache.delete(key);
    });
    nightCache.set(key, p);
  }
  return p;
}

export interface TargetSparkProps {
  ra_hours: number;
  dec_deg: number;
  altLimit: number;
}

export default function TargetSpark({
  ra_hours,
  dec_deg,
  altLimit,
}: TargetSparkProps): JSX.Element {
  // Round exactly like VisibilityPanel (wave-1 §2): sub-arcminute drift must not
  // key a distinct request, and mosaic panels sharing a rounded center collapse
  // onto one cache entry. altLimit is part of the key so two cards at the same
  // coords but different horizon limits don't alias to one (stale) night.
  const keyRa = Math.round(ra_hours * 1000) / 1000;
  const keyDec = Math.round(dec_deg * 100) / 100;

  const [night, setNight] = useState<VisibilityNight | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setNight(null);
    setFailed(false);
    loadNight(keyRa, keyDec, altLimit)
      .then((n) => {
        if (alive) setNight(n);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [keyRa, keyDec, altLimit]);

  const geo = useMemo(
    () => (night ? buildSparkGeometry(night, SPARK_W, SPARK_H) : null),
    [night],
  );

  // Loading (and quiet-fail fallback): a dim placeholder box, never an error wall.
  // A failed visibility fetch is non-critical chrome, so it degrades to the same
  // muted box rather than shouting — the plan stays fully usable.
  if (!night) {
    return (
      <span
        className="inline-block align-middle border border-line/50 bg-bg/40"
        style={{ width: SPARK_W, height: SPARK_H }}
        aria-label={
          failed
            ? "tonight's altitude unavailable"
            : "loading tonight's altitude"
        }
        title={failed ? "couldn't compute tonight's visibility" : undefined}
      />
    );
  }

  // Never clears the horizon limit tonight — a compact warn chip, no chart.
  if (night.never_rises_above_limit) {
    return (
      <span
        className="inline-flex items-center align-middle text-[10px] text-warn border border-warn/40 px-1.5"
        style={{ height: SPARK_H }}
        aria-label={`Does not rise above ${Math.round(night.alt_limit_deg)}° tonight`}
      >
        ⚠ low all night
      </span>
    );
  }

  const label = `Tonight: transits ${fmtTime(night.transit_unix)} at ${fmtAlt(
    night.transit_alt,
  )}, horizon limit ${Math.round(night.alt_limit_deg)}°`;

  return (
    <svg
      width={SPARK_W}
      height={SPARK_H}
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      className="inline-block align-middle border border-line/50 bg-bg/40"
      role="img"
      aria-label={label}
    >
      <title>{label}</title>
      {/* astro-dark band — the only fill; low-opacity so the curve reads on top */}
      {geo?.darkBand && geo.darkBand.w > 0 && (
        <rect
          x={geo.darkBand.x}
          y={0}
          width={geo.darkBand.w}
          height={SPARK_H}
          fill="var(--accent)"
          opacity={0.1}
        />
      )}
      {/* alt-limit dashed horizontal (warn tone, matches VisChart) */}
      {geo && (
        <line
          x1={0}
          y1={geo.altLimitY}
          x2={SPARK_W}
          y2={geo.altLimitY}
          stroke="var(--warn)"
          strokeWidth={1}
          strokeDasharray="3 2"
        />
      )}
      {/* target altitude curve — the hero layer */}
      {geo?.altPath && (
        <path
          d={geo.altPath}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={1.5}
          strokeLinejoin="round"
          className="svg-halo"
        />
      )}
    </svg>
  );
}
