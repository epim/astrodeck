// api/reports.ts — typed wrappers for the session-report routes (report viewer
// spec §3 Task 1). Cookie auth is automatic; ApiError on non-2xx.
import { api } from "../api";
import type { BundlePreview, SessionReport, SessionReportSummary } from "../types";

export const listReports = (): Promise<SessionReportSummary[]> =>
  api.get<SessionReportSummary[]>("/api/reports");

export const getReport = (id: string): Promise<SessionReport> =>
  api.get<SessionReport>(`/api/reports/${encodeURIComponent(id)}`);

// PRO-10 stacking-bundle preview (slim per-group summary; the .zip is a plain
// <a download> to /bundle.zip, not a fetch).
export const getBundlePreview = (id: string): Promise<BundlePreview> =>
  api.get<BundlePreview>(`/api/reports/${encodeURIComponent(id)}/bundle`);
