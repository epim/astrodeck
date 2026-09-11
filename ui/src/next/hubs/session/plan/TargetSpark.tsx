// TargetSpark.tsx - tonight's altitude for one target, as a chip.
//
// The rebuild of `components/sequence/TargetSpark.tsx`: the same three layers
// (astro-dark band, alt-limit dashed line, altitude curve) from the same pure,
// size-parameterised `buildSparkGeometry`, in the design's own chrome. Nothing
// about the geometry is re-derived here.
//
// THE FETCH CACHE IS THE POINT, not an optimisation. A mosaic's panels all
// share one centre, and a plan with a six-panel mosaic would otherwise fire six
// identical `/api/visibility` requests every time the list re-renders. Keyed on
// the ROUNDED coordinates plus the alt limit, so sub-arcminute drift does not
// mint a distinct request and two cards at the same place with different
// horizon limits do not alias onto one stale night. A rejected promise is
// evicted, so a later remount retries instead of being wedged on a failure.
//
// The legacy module holds its own copy of this cache and keeps it for
// `#/classic`; only one root is mounted at a time, so the two never both run.
//
// A failed visibility fetch degrades to the same muted box as loading. It is
// non-critical chrome: the plan stays fully usable, and shouting about it here
// would put an error next to a target that is perfectly fine.

import { useEffect, useMemo, useState, type JSX } from "react";

import { api } from "../../../../api";
import { buildSparkGeometry, fmtAlt, fmtTime } from "../../../../lib/visibility";
import type { VisibilityNight } from "../../../../types";

const SPARK_W = 108;
const SPARK_H = 28;

const nightCache = new Map<string, Promise<VisibilityNight>>();

/** Test hatch: the cache is module-level and would otherwise carry one test's
 *  stubbed night into the next file that mounts a target row. */
export function resetTargetSparkCacheForTests(): void {
  nightCache.clear();
}

function loadNight(keyRa: number, keyDec: number, altLimit: number): Promise<VisibilityNight> {
  const key = `${keyRa}|${keyDec}|${altLimit}`;
  let p = nightCache.get(key);
  if (!p) {
    p = api.get<VisibilityNight>(
      `/api/visibility?ra=${encodeURIComponent(keyRa)}`
      + `&dec=${encodeURIComponent(keyDec)}`
      + `&alt_limit=${encodeURIComponent(altLimit)}`,
    );
    p.catch(() => { if (nightCache.get(key) === p) nightCache.delete(key); });
    nightCache.set(key, p);
  }
  return p;
}

export function TargetSpark({ ra_hours, dec_deg, altLimit }: {
  ra_hours: number;
  dec_deg: number;
  altLimit: number;
}): JSX.Element {
  const keyRa = Math.round(ra_hours * 1000) / 1000;
  const keyDec = Math.round(dec_deg * 100) / 100;

  const [night, setNight] = useState<VisibilityNight | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setNight(null);
    setFailed(false);
    loadNight(keyRa, keyDec, altLimit).then(
      (n) => { if (alive) setNight(n); },
      () => { if (alive) setFailed(true); },
    );
    return () => { alive = false; };
  }, [keyRa, keyDec, altLimit]);

  const geo = useMemo(
    () => (night ? buildSparkGeometry(night, SPARK_W, SPARK_H) : null),
    [night],
  );

  if (!night) {
    return (
      <span
        className="nx-plan-spark"
        data-state={failed ? "unknown" : "loading"}
        data-testid="plan-target-spark"
        style={{ width: SPARK_W, height: SPARK_H }}
        aria-label={failed
          ? "Tonight's altitude is unavailable for this target"
          : "Loading tonight's altitude"}
        title={failed ? "The visibility check did not answer" : undefined}
      />
    );
  }

  // Never clears the limit tonight. A chart of a curve that never crosses the
  // line says less than the sentence does, and this is the one state that
  // changes what the user should DO about the target.
  if (night.never_rises_above_limit) {
    return (
      <span
        className="nx-plan-spark"
        data-state="warn"
        data-testid="plan-target-spark"
        style={{ height: SPARK_H }}
        aria-label={`Does not rise above ${Math.round(night.alt_limit_deg)} degrees tonight`}
      >
        LOW ALL NIGHT
      </span>
    );
  }

  const label = `Tonight: transits ${fmtTime(night.transit_unix)} at ${fmtAlt(night.transit_alt)}`
    + `, horizon limit ${Math.round(night.alt_limit_deg)} degrees`;

  return (
    <span className="nx-plan-spark" data-state="ok" data-testid="plan-target-spark"
      style={{ width: SPARK_W, height: SPARK_H }}>
      <svg
        width={SPARK_W}
        height={SPARK_H}
        viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
        role="img"
        aria-label={label}
      >
        <title>{label}</title>
        {/* astro-dark band - the only fill, low opacity so the curve reads on top */}
        {geo?.darkBand && geo.darkBand.w > 0 && (
          <rect x={geo.darkBand.x} y={0} width={geo.darkBand.w} height={SPARK_H}
            fill="var(--accent)" opacity={0.12} />
        )}
        {/* the horizon limit, dashed: layers differentiate by stroke, not hue */}
        {geo && (
          <line x1={0} y1={geo.altLimitY} x2={SPARK_W} y2={geo.altLimitY}
            stroke="var(--warn)" strokeWidth={1} strokeDasharray="3 2" />
        )}
        {geo?.altPath && (
          <path d={geo.altPath} fill="none" stroke="var(--accent)" strokeWidth={1.5}
            strokeLinejoin="round" />
        )}
      </svg>
    </span>
  );
}
