// ReportSummary.tsx - the header block: how the run ended, when it ran, and
// the three numbers that are the night.
//
// The end reason is a `StatusPill`: a WORD and a tone, never a tone alone.
// `endReasonMeta`'s table already decides the word (COMPLETE / UNFINISHED /
// ABORTED / QUALITY STOP / COOLING SKIP / UNSAFE - STOPPED / ERROR / DAWN
// CUTOFF, and an honest IN PROGRESS for a report written before the run
// ended), so this file only paints it.

import type { JSX } from "react";
import { Card, Label, Mono, ReadoutGrid, ReadoutTile, StatusPill } from "../../../ui";
import { fmtDuration } from "../../../../lib/eta";
import type { SessionReport } from "../../../../types";
import { endReasonTone, endReasonWord, fmtStamp } from "./reportModel";

export function ReportSummary({ report }: { report: SessionReport }): JSX.Element {
  const ran = report.ended_at != null
    ? `${fmtStamp(report.started_at)} to ${fmtStamp(report.ended_at)}`
    : `${fmtStamp(report.started_at)}, no end recorded`;

  return (
    <Card data-testid="report-summary">
      <div className="nx-report-block">
        <div className="nx-report-head">
          <Label size={11}>{report.plan_name}</Label>
          <StatusPill
            data-testid="report-end-reason"
            text={endReasonWord(report.end_reason)}
            tone={endReasonTone(report.end_reason)}
          />
        </div>
        <Mono size={10.5} tone="dim" data-testid="report-ran">{ran}</Mono>
        <ReadoutGrid cols={3}>
          <ReadoutTile
            data-testid="report-stat-integration"
            label="INTEGRATION"
            value={fmtDuration(report.integration_s)}
          />
          <ReadoutTile
            data-testid="report-stat-accepted"
            label="ACCEPTED"
            value={String(report.frames_captured)}
            sub="frames kept"
          />
          <ReadoutTile
            data-testid="report-stat-rejected"
            label="REJECTED"
            value={String(report.frames_rejected)}
            sub={report.frames_rejected > 0 ? "graded out" : "none"}
            tone={report.frames_rejected > 0 ? "warn" : undefined}
          />
        </ReadoutGrid>
      </div>
    </Card>
  );
}
