// FilterRows.tsx - `By filter` and `By target`, both built from one row.
//
// The row is the campaign-ledger line (proto 10): the filter's name beside its
// swatch, a bar showing its share of the night's integration, and the figures
// right-aligned. Colour is never the carrier - the NAME is always rendered,
// the swatch only repeats it, and a filter this palette has no token for gets
// the neutral faint colour rather than being painted as some other glass.
//
// `By target` is the same row nested under a target heading, so a filter that
// looks thin in the headline table can be traced to the target that ran short.

import type { JSX } from "react";
import { Bar, Card, EmptyCard, Label, Mono } from "../../../ui";
import { fmtDuration } from "../../../../lib/eta";
import type { FilterBreakdown, TargetBreakdown } from "../../../../types";
import {
  filterColor, filterLabel, filterLine, rejectedLine, REPORT_COPY,
} from "./reportModel";

/** One filter's line. `share` is 0..1 of the widest row in the same table, so
 *  the bars in one card compare with each other and with nothing else. */
export function FilterRow({ f, share, testid }: {
  f: FilterBreakdown;
  share: number;
  testid: string;
}): JSX.Element {
  const rejected = rejectedLine(f.rejected);
  const name = filterLabel(f.filter);
  return (
    <div className="nx-report-row" data-testid={testid}>
      <span className="nx-report-name">
        <span
          className="nx-report-swatch"
          style={{ background: filterColor(f.filter) }}
          aria-hidden="true"
        />
        {name}
      </span>
      <Bar
        height={6}
        label={`${name}: ${fmtDuration(f.integration_s)} of this table's longest filter`}
        segments={[{ frac: 1, fill: share, color: filterColor(f.filter) }]}
      />
      <span className="nx-report-figures">
        <Mono size={10.5} tone="dim">{filterLine(f)}</Mono>
        {rejected != null && <Mono size={10.5} tone="warn">{rejected}</Mono>}
      </span>
    </div>
  );
}

/** The share denominator: the longest filter in THIS table. A share against
 *  the report total would flatten every row on a night that ran one filter. */
function shares(rows: FilterBreakdown[]): number[] {
  const top = rows.reduce((m, r) => Math.max(m, r.integration_s), 0);
  return rows.map((r) => (top > 0 ? r.integration_s / top : 0));
}

export function ByFilterCard({ rows }: { rows: FilterBreakdown[] }): JSX.Element {
  const s = shares(rows);
  return (
    <Card data-testid="report-by-filter">
      <div className="nx-report-block">
        <Label size={11}>BY FILTER</Label>
        {rows.length === 0
          ? <Mono size={10.5} tone="dim">{REPORT_COPY.noFrames}</Mono>
          : rows.map((f, i) => (
            <FilterRow
              key={`${f.filter}-${i}`}
              f={f}
              share={s[i]}
              testid={`report-filter-${f.filter || "none"}`}
            />
          ))}
      </div>
    </Card>
  );
}

export function ByTargetCard({ targets }: { targets: TargetBreakdown[] }): JSX.Element {
  return (
    <Card data-testid="report-by-target">
      <div className="nx-report-block">
        <Label size={11}>BY TARGET</Label>
        {targets.length === 0 && (
          <EmptyCard title={REPORT_COPY.noTargets} data-testid="report-target-empty" />
        )}
        {targets.map((t, ti) => {
          const s = shares(t.by_filter);
          const rejected = rejectedLine(t.rejected);
          return (
            <div className="nx-report-block" key={`${t.name}-${ti}`} data-testid={`report-target-${ti}`}>
              <div className="nx-report-head">
                <Label size={11}>{t.name}</Label>
                <span className="nx-report-figures">
                  <Mono size={10.5} tone="dim">
                    {`${t.frames} frames · ${fmtDuration(t.integration_s)}`}
                  </Mono>
                  {rejected != null && <Mono size={10.5} tone="warn">{rejected}</Mono>}
                </span>
              </div>
              <div className="nx-report-target">
                {t.by_filter.map((f, fi) => (
                  <FilterRow
                    key={`${f.filter}-${fi}`}
                    f={f}
                    share={s[fi]}
                    testid={`report-target-${ti}-filter-${f.filter || "none"}`}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
