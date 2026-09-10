// ReportScreen.tsx - the night report, rebuilt in the new UI's own vocabulary
// (wave R7, area C: `views/ReportView.tsx`).
//
// This is the AREA ROOT: it is the only file here that imports `report.css`,
// and it is the only file that reads the network (through `useReportData`).
// Every panel under it takes props, which is what lets the DOM test mount a
// viewer's report and an admin's report from the same fixture.
//
// The legacy view is NOT deleted and NOT edited - `#/classic` still renders it.
// What is shared is the LOGIC: `lib/reportChart.ts` (end reasons, trend
// geometry), `lib/bundleView.ts` (every bundle helper), `lib/eta.ts`
// (`fmtDuration`) and `api/reports.ts`. What is rebuilt is presentation.

import type { JSX } from "react";
import { EmptyCard, Mono } from "../../../ui";
import { ByFilterCard, ByTargetCard } from "./FilterRows";
import { BundlePanel } from "./BundlePanel";
import { NxIcon } from "../../../icons";
import { u } from "../../../../lib/base";
import { ReportPicker } from "./ReportPicker";
import { ReportSummary } from "./ReportSummary";
import { REPORT_COPY } from "./reportModel";
import { SafetyEvents } from "./SafetyEvents";
import { Trends } from "./Trends";
import { useReportData } from "./useReportData";
import "./report.css";

/** `reportId` is the sheet's `?id=` param. It names the night the user asked
 *  for and wins over the store's `lastReportId`, so a deep link out of a
 *  session card opens THAT report instead of the newest one. */
export function ReportScreen({ reportId }: { reportId?: string }): JSX.Element {
  const d = useReportData(reportId);

  return (
    <div className="nx-report" data-testid="report-body">
      <ReportPicker
        list={d.list}
        listLoading={d.listLoading}
        listErr={d.listErr}
        onRetry={d.reloadList}
        sel={d.sel}
        onSelect={d.select}
      />

      {d.loading && <Mono size={10.5} tone="dim">{REPORT_COPY.detailLoading}</Mono>}

      {d.err && (
        <EmptyCard
          data-testid="report-error"
          title={REPORT_COPY.detailErrorTitle}
          hint={d.err}
        />
      )}

      {d.report && !d.err && (
        <>
          <ReportSummary report={d.report} />
          <ByFilterCard rows={d.report.by_filter} />
          <ByTargetCard targets={d.report.targets} />
          <SafetyEvents events={d.report.safety_events} />
          <Trends trends={d.report.trends} />
          <BundlePanel
            reportId={d.sel ?? ""}
            framesCaptured={d.report.frames_captured}
            preview={d.preview}
            previewLoading={d.previewLoading}
            previewErr={d.previewErr}
            retryPreview={d.retryPreview}
            bundle={d.bundle}
            materializing={d.materializing}
            matResult={d.matResult}
            onMaterialize={() => { void d.materialize(); }}
          />
          <div className="nx-report-actions">
            <a
              className="nx-btn"
              data-kind="ghost"
              data-testid="report-csv"
              href={u(`/api/reports/${encodeURIComponent(d.sel ?? "")}/frames.csv`)}
              download
            >
              <span className="nx-btn-glyph"><NxIcon name="download" size={14} /></span>
              <span className="nx-btn-label">{REPORT_COPY.csvLabel}</span>
            </a>
          </div>
        </>
      )}
    </div>
  );
}
