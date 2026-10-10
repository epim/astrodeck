// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The scan report, version 2 (SPEC-v2 13.10, D25; issue #902), and the summary
// statistic it is made of.
//
// The report leaves the phone: it is a download the owner may attach to an
// issue, so it is built by naming what goes in rather than by removing what
// must stay out. SHAPE below is the ScanReport type written out as a value, and
// buildReport copies exactly the fields it lists, to every depth, from whatever
// it is handed. A key the type does not declare is dropped wherever it sits - a
// coordinate stashed on the input, a declination, a raw photo key, a user-agent
// string, a Date - so "no coordinates, no declination, no photo key, no
// user-agent, no wall-clock time" holds because nothing else is ever copied,
// not because something remembers to delete it. A spread of the input would
// look the same on a clean input and carry all of that out the door.
//
// The same rule is applied to a leaf. A field declared as a number is copied
// only when it is a number, and a string only when it is a string, so a Date
// parked in a numeric slot cannot turn into an ISO timestamp on the way through
// JSON. Anything that is not the declared kind becomes null. A key whose value
// is undefined is left out, which is what JSON would do anyway and keeps an
// absent optional field absent instead of claiming it was measured as null.
//
// SHAPE is typed against ScanReport (Desc below), so a field added to the type
// without an entry here fails `tsc` instead of vanishing from every report.
//
// Nothing in this file reads the clock. The times in a report are values the
// scanner measured and handed in, on the performance.now() timeline.
import type { ReportInput, ScanReport, Stats } from './types';

/** A leaf is a string, a number or a boolean; `string | null` is still 'str', since null always passes. */
type Shape = 'str' | 'num' | 'bool' | { array: Shape } | { object: { readonly [key: string]: Shape } };

/** The Shape that describes T. SHAPE is typed with it, so a field missing from SHAPE or not in the type is a compile error. */
type Desc<T> =
  NonNullable<T> extends string ? 'str'
  : NonNullable<T> extends number ? 'num'
  : NonNullable<T> extends boolean ? 'bool'
  : NonNullable<T> extends readonly (infer U)[] ? { array: Desc<U> }
  : { object: { [K in keyof NonNullable<T>]-?: Desc<NonNullable<T>[K]> } };

const STATS: Desc<Stats> = { object: { n: 'num', p50: 'num', p95: 'num', max: 'num' } };

/** Written in the order of ScanReport: the keys of a report come out in this order. */
const SHAPE: Desc<ReportInput> = {
  object: {
    scanner: { object: { commit: 'str', sensorOnly: 'bool' } },
    browser: { object: { brands: { array: 'str' }, mobile: 'bool', platform: 'str' } },
    timeline: { object: { beginMs: 'num', finishMs: 'num', endedBy: 'str' } },
    camera: {
      object: {
        settings: {
          object: {
            width: 'num', height: 'num', frameRate: 'num', resizeMode: 'str', zoom: 'num',
            focusMode: 'str', focusDistance: 'num', facingMode: 'str', label: 'str',
          },
        },
        bench: { object: { aMs: STATS, bMs: STATS, chosen: 'str', smoothing: 'str' } },
        farbled: 'bool',
        exposureReadable: 'bool',
        analysis: { object: { w: 'num', h: 'num' } },
      },
    },
    frames: {
      object: {
        delivered: 'num', viaRvfc: 'bool', captureTimePresent: 'num',
        slips: { object: { pairs: 'num', slips: 'num' } },
        lag: { object: { nowMinusCapture: STATS, presentMinusCapture: STATS, expectedMinusNow: STATS } },
        intervalMs: STATS,
        presentedGaps: 'num',
      },
    },
    sensors: {
      object: {
        modeAtBegin: 'str',
        modeChanges: { array: { object: { tMs: 'num', from: 'str', to: 'str' } } },
        events: {
          array: {
            object: {
              type: 'str', total: 'num', absoluteTrue: 'num', absoluteFalse: 'num', absoluteMissing: 'num',
              nullReadings: 'num', duplicates: 'num',
            },
          },
        },
        movingHz: { object: { relative: 'num', absolute: 'num', motion: 'num' } },
        rateByOmega: {
          array: { object: { omegaFrom: 'num', omegaTo: 'num', relativeHz: 'num', absoluteHz: 'num' } },
        },
        blocked: 'bool',
        axis: {
          object: {
            perm: { array: 'num' }, sign: { array: 'num' }, unit: 'str', fit: 'num', confirmed: 'bool', samples: 'num',
          },
        },
        gyroZeroTriples: 'num',
        gyroSeenNonZero: 'bool',
        staleRefusals: 'num',
        staleLimitMs: STATS,
        latency: { object: { priorMs: 'num', tauMs: 'num', sigmaMs: 'num', pairs: 'num', applied: 'bool' } },
      },
    },
    focal: {
      object: {
        source: 'str', state: 'str', fNorm: 'num', sdPct: 'num', ratios: 'num', shortFovDeg: 'num',
        closurePct: 'num', gyroScale: 'num', ultraWideSuspected: 'bool',
      },
    },
    loop: {
      object: {
        closed: 'bool', method: 'str', preDeg: { array: 'num' }, postDeg: 'num',
        match: { object: { early: 'num', late: 'num' } },
        unwrappedDeg: 'num',
      },
    },
    north: {
      object: {
        offsetDeg: 'num', sigmaDeg: 'num', spreadDeg: 'num', samples: 'num', nEff: 'num', stable: 'bool', source: 'str',
      },
    },
    declinationApplied: 'bool',
    keyframes: {
      object: {
        total: 'num', aligned: 'num', blurred: 'num', sensor: 'num', ms: STATS, readbackMs: STATS,
        innovationSteadyDeg: STATS, innovationAccelDeg: STATS, perSliceMs: STATS,
      },
    },
    liveFrame: { object: { drawMs: STATS, redraws: 'num' } },
    horizon: {
      object: {
        tau: 'num', points: 'num', measured: 'num', low: 'num', unknown: 'num', tall: 'num', kept: 'num',
        lowLight: 'bool',
      },
    },
    recording: { object: { on: 'bool', every: 'num', frames: 'num' } },
    captureLog: {
      array: {
        object: {
          at: 'num', frameId: 'num', outcome: 'str', detail: 'str', kf: 'num', stepDeg: 'num', rateDegS: 'num',
          psr: 'num', zncc: 'num', innovationDeg: 'num', wYaw: 'num', extrapolatedMs: 'num',
        },
      },
    },
    slices: { array: { object: { kf: 'num', dataUrl: 'str' } } },
    error: 'str',
  },
};

/** Copy `v` as `d` describes it and nothing else. null passes; a value of the wrong kind becomes null. */
function copyShape(v: unknown, d: Shape): unknown {
  if (v === null) return null;
  if (d === 'str') return typeof v === 'string' ? v : null;
  if (d === 'num') return typeof v === 'number' ? v : null;
  if (d === 'bool') return typeof v === 'boolean' ? v : null;
  if ('array' in d) {
    if (!Array.isArray(v)) return null;
    const out: unknown[] = new Array(v.length);
    for (let i = 0; i < v.length; i++) out[i] = copyShape(v[i], d.array);
    return out;
  }
  // A plain object or a class instance; an array, a Date, a Map and the rest
  // answer to another tag and are not an object of the report.
  if (typeof v !== 'object' || Object.prototype.toString.call(v) !== '[object Object]') return null;
  const src = v as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(d.object)) {
    const x = src[key];
    if (x !== undefined) out[key] = copyShape(x, d.object[key]);
  }
  return out;
}

/** A report made of the fields ScanReport declares and of nothing else. The result shares no object or array with `input`. */
export function buildReport(input: ReportInput): ScanReport {
  const body = copyShape(input, SHAPE as Shape) as ReportInput;
  return { format: 'astrodeck-pano-report', version: 2, ...body };
}

/**
 * The text of a report as it is downloaded: two-space JSON, like the older
 * scanner's. It writes the report it is handed and does not filter it again,
 * so `r` has to come out of buildReport; Recorder.finish runs buildReport
 * itself on the report it is given.
 */
export function reportJson(r: ScanReport): string {
  return JSON.stringify(r, null, 2);
}

/**
 * n, nearest-rank p50 and p95, and the maximum. The k-th smallest of n values
 * is the p-th percentile for k = ceil(p n / 100), worked out in integers so
 * that no float rounding can move a rank. Values that are not finite (a NaN
 * lag from a frame that had no capture time) are left out and not counted, so
 * n is the number of values the statistics are of. `values` is not modified.
 */
export function statsOf(values: readonly number[]): Stats {
  const sorted = Float64Array.from(values.filter(Number.isFinite)).sort();
  const n = sorted.length;
  if (n === 0) return { n: 0, p50: null, p95: null, max: null };
  const rank = (pct: number) => sorted[Math.floor((pct * n + 99) / 100) - 1];
  return { n, p50: rank(50), p95: rank(95), max: sorted[n - 1] };
}
