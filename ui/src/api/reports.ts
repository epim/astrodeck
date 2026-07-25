// api/reports.ts — typed wrappers for the session-report routes (report viewer
// spec §3 Task 1). Cookie auth is automatic; ApiError on non-2xx.
import { api } from "../api";
import type {
  BundleMaterializeResult,
  BundlePreview,
  SessionReport,
  SessionReportSummary,
} from "../types";
import { bundleQuery, type BundleLayout } from "../lib/bundleView";

export const listReports = (): Promise<SessionReportSummary[]> =>
  api.get<SessionReportSummary[]>("/api/reports");

export const getReport = (id: string): Promise<SessionReport> =>
  api.get<SessionReport>(`/api/reports/${encodeURIComponent(id)}`);

// PRO-10 stacking-bundle preview (slim per-group summary; the .zip is a plain
// <a download> to /bundle.zip, not a fetch).
export interface BundleOpts {
  layout?: BundleLayout;
  weightAlt?: boolean;
  keepThreshold?: number | null;
}

export const getBundlePreview = (id: string, opts: BundleOpts = {}): Promise<BundlePreview> =>
  api.get<BundlePreview>(
    `/api/reports/${encodeURIComponent(id)}/bundle${bundleQuery(opts)}`,
  );

// PRO-10 enrichment (a): ask the server to lay the ACTUAL FITS out under
// captures/exports/<id>/. POST (it writes to disk) and CAP_CONTROL_CAPTURE-gated
// server-side — only meaningful when AstroDeck runs on the capture box.
export const materializeBundle = (
  id: string,
  opts: BundleOpts = {},
): Promise<BundleMaterializeResult> =>
  api.post<BundleMaterializeResult>(
    `/api/reports/${encodeURIComponent(id)}/bundle/materialize${bundleQuery(opts)}`,
  );
