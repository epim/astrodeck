// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
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
// A mosaic's panels are the exception to "one heading per target": they sit
// under ONE heading for the mosaic, with the panels' sum, a block per panel
// (#188).

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

/** A target's row as the server writes it. A mosaic's panel also carries the
 *  mosaic's name and its own `r-c` label (#188), which `mosaic` and `panel`
 *  name here; both are null for a target that is no panel, and absent from a
 *  report written before they existed. */
export type MosaicTarget = TargetBreakdown & {
  mosaic?: string | null;
  panel?: string | null;
};

export interface PanelRef {
  target: MosaicTarget;
  /** Where this row sits in the report's own `targets`, which the test ids and
   *  keys use so a panel's id does not change when its mosaic is grouped. */
  index: number;
}

export type TargetRow =
  | { kind: "target"; target: MosaicTarget; index: number }
  | {
    kind: "mosaic";
    mosaic: string;
    panels: PanelRef[];
    /** The panels' accepted frames, rejects and accepted integration, summed. */
    frames: number;
    rejected: number;
    integration_s: number;
  };

/** `[row, column]` of a panel's `r-c` label, or null for one that is not that
 *  shape (a member the plan gave no grid position). */
function gridPosition(label: string | null | undefined): [number, number] | null {
  const m = /^(\d+)-(\d+)$/.exec(label ?? "");
  return m ? [Number(m[1]), Number(m[2])] : null;
}

/** Grid order, then the report's order for a panel with no position. The
 *  report lists targets by first frame, which for a mosaic is the order the
 *  panels were VISITED, so it is never the order the chart reads in. */
function byGrid(a: PanelRef, b: PanelRef): number {
  const pa = gridPosition(a.target.panel);
  const pb = gridPosition(b.target.panel);
  if (pa && pb) return pa[0] - pb[0] || pa[1] - pb[1];
  if (pa) return -1;
  if (pb) return 1;
  return a.index - b.index;
}

/** The report's targets as rows: a target that is no panel stays a row of its
 *  own, and every target that shares a `mosaic` becomes one group with a
 *  summed roll-up. A group sits where its first panel was, so the card still
 *  reads in the order the night ran; its panels are in grid order. Pure, and
 *  shared by the classic report. */
export function groupTargetsByMosaic(targets: MosaicTarget[]): TargetRow[] {
  const rows: TargetRow[] = [];
  const open = new Map<string, Extract<TargetRow, { kind: "mosaic" }>>();
  targets.forEach((t, index) => {
    if (!t.mosaic) {
      rows.push({ kind: "target", target: t, index });
      return;
    }
    let group = open.get(t.mosaic);
    if (!group) {
      group = { kind: "mosaic", mosaic: t.mosaic, panels: [], frames: 0, rejected: 0, integration_s: 0 };
      open.set(t.mosaic, group);
      rows.push(group);
    }
    group.panels.push({ target: t, index });
    group.frames += t.frames;
    group.rejected += t.rejected;
    group.integration_s += t.integration_s;
  });
  for (const group of open.values()) group.panels.sort(byGrid);
  return rows;
}

/** One target's block: its heading and totals, then its filters. A panel uses
 *  the same block under its mosaic's header, headed by its label. */
function TargetBlock({ t, index, heading }: {
  t: TargetBreakdown;
  index: number;
  heading: string;
}): JSX.Element {
  const s = shares(t.by_filter);
  const rejected = rejectedLine(t.rejected);
  return (
    <div className="nx-report-block" data-testid={`report-target-${index}`}>
      <div className="nx-report-head">
        <Label size={11}>{heading}</Label>
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
            testid={`report-target-${index}-filter-${f.filter || "none"}`}
          />
        ))}
      </div>
    </div>
  );
}

export function ByTargetCard({ targets }: { targets: MosaicTarget[] }): JSX.Element {
  return (
    <Card data-testid="report-by-target">
      <div className="nx-report-block">
        <Label size={11}>BY TARGET</Label>
        {targets.length === 0 && (
          <EmptyCard title={REPORT_COPY.noTargets} data-testid="report-target-empty" />
        )}
        {groupTargetsByMosaic(targets).map((row, gi) => {
          if (row.kind === "target") {
            return (
              <TargetBlock
                key={`${row.target.name}-${row.index}`}
                t={row.target}
                index={row.index}
                heading={row.target.name}
              />
            );
          }
          const rejected = rejectedLine(row.rejected);
          return (
            <div className="nx-report-block" key={`mosaic-${row.mosaic}-${gi}`} data-testid={`report-mosaic-${gi}`}>
              <div className="nx-report-head">
                <Label size={11}>{row.mosaic}</Label>
                <span className="nx-report-figures">
                  <Mono size={10.5} tone="dim">
                    {`${row.panels.length} ${row.panels.length === 1 ? "panel" : "panels"} · ${row.frames} frames · ${fmtDuration(row.integration_s)}`}
                  </Mono>
                  {rejected != null && <Mono size={10.5} tone="warn">{rejected}</Mono>}
                </span>
              </div>
              <div className="nx-report-target">
                {row.panels.map((p) => (
                  <TargetBlock
                    key={`${p.target.name}-${p.index}`}
                    t={p.target}
                    index={p.index}
                    heading={p.target.panel ? `Panel ${p.target.panel}` : p.target.name}
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
