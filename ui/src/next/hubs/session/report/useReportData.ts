// useReportData.ts - the three fetches behind the night report, and the six
// states they can be in.
//
// Lifted from `views/ReportView.tsx`'s effects rather than re-invented: the
// request-generation guard on the index, the cancelled-flag guard on the
// detail, the CLEAR-FIRST rule, and the best-effort bundle preview all exist
// because of bugs that were found once already. Each keeps its reason here.
//
// One deliberate simplification. The legacy view debounced the keep-threshold
// by 300 ms because its `<input type=number>` committed on every keystroke and
// typing "0.55" fired four server round-trips over the whole night's frames.
// The rebuild's cutoff is a `NumberField`, which commits at blur and Enter and
// nowhere else, so the settle is structural and the timer is gone. One commit,
// one preview.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../../../api";
import {
  getBundlePreview, getReport, listReports, materializeBundle,
} from "../../../../api/reports";
import type { BundleLayout } from "../../../../lib/bundleView";
import { useLastReportId, useStore } from "../../../../store";
import type {
  BundleMaterializeResult, BundlePreview, SessionReport, SessionReportSummary,
} from "../../../../types";
import { REPORT_COPY } from "./reportModel";

export interface BundleOptionsState {
  layout: BundleLayout;
  setLayout: (v: BundleLayout) => void;
  weightAlt: boolean;
  setWeightAlt: (v: boolean) => void;
  keepOn: boolean;
  setKeepOn: (v: boolean) => void;
  keepThreshold: number;
  setKeepThreshold: (v: number) => void;
  /** The threshold as the routes take it: the number when flagging is on, null
   *  when it is off. `bundleQuery` omits a null, which is what keeps the
   *  one-click .zip URL byte-for-byte what it was before this feature. */
  keepParam: number | null;
}

export interface ReportData {
  list: SessionReportSummary[];
  listLoading: boolean;
  listErr: string | null;
  reloadList: () => void;

  sel: string | null;
  select: (id: string) => void;

  report: SessionReport | null;
  loading: boolean;
  err: string | null;

  preview: BundlePreview | null;
  previewLoading: boolean;
  previewErr: string | null;
  retryPreview: () => void;

  bundle: BundleOptionsState;

  materializing: boolean;
  matResult: BundleMaterializeResult | null;
  materialize: () => Promise<void>;
}

/** `reportId` is the sheet's `?id=` param: the night the user asked for. It
 *  wins over the store's `lastReportId` and over the newest report, so a deep
 *  link opens the report it names. */
export function useReportData(reportId?: string): ReportData {
  const lastReportId = useLastReportId();
  const enqueueToast = useStore((s) => s.enqueueToast);

  const [list, setList] = useState<SessionReportSummary[]>([]);
  // The report INDEX has three outcomes, and "No reports yet" is a positive
  // claim about the user's data - it must never stand in for "we have not
  // asked yet" or "the ask failed".
  const [listLoading, setListLoading] = useState(true);
  const [listErr, setListErr] = useState<string | null>(null);

  const [sel, setSel] = useState<string | null>(reportId || null);
  const [report, setReport] = useState<SessionReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const [preview, setPreview] = useState<BundlePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewErr, setPreviewErr] = useState<string | null>(null);
  const [previewGen, setPreviewGen] = useState(0);

  // Every bundle option is default-off and none is persisted: the novice path
  // is the one-click .zip, and rig data never goes to localStorage.
  const [layout, setLayout] = useState<BundleLayout>("grouped");
  const [weightAlt, setWeightAlt] = useState(false);
  const [keepOn, setKeepOn] = useState(false);
  const [keepThreshold, setKeepThreshold] = useState(0.5);
  const [materializing, setMaterializing] = useState(false);
  const [matResult, setMatResult] = useState<BundleMaterializeResult | null>(null);
  const keepParam = keepOn ? keepThreshold : null;

  // A later store `lastReportId` change must not yank the selection out from
  // under someone reading an older night, so the loader reads it through a ref
  // instead of taking it as a dependency - and stays callable from RETRY.
  const wantedRef = useRef<string | null>(reportId || lastReportId);
  wantedRef.current = reportId || lastReportId;
  const listReq = useRef(0);

  const reloadList = useCallback(() => {
    const req = ++listReq.current;
    setListLoading(true);
    void (async () => {
      try {
        const l = await listReports();
        if (req !== listReq.current) return;
        setList(l);
        setListErr(null);
        setSel((prev) => prev ?? wantedRef.current ?? l[0]?.id ?? null);
      } catch (e) {
        if (req !== listReq.current) return;
        // The INDEX failed, which says nothing about the reports themselves.
        // Keep the wanted id selected so the run that just finished still
        // opens, and state the failure where the list would have been.
        setListErr(e instanceof ApiError ? e.message : "couldn't reach the server");
        setSel((prev) => prev ?? wantedRef.current ?? null);
      } finally {
        if (req === listReq.current) setListLoading(false);
      }
    })();
  }, []);

  useEffect(() => { reloadList(); }, [reloadList]);

  // A NEW `?id=` ON A MOUNTED SHEET IS A NEW REQUEST, not noise. `sel` was
  // seeded from `reportId` once, at first render, so the second half of every
  // deep link into an already-open report sheet was dropped: the Gallery's
  // REPORT verb on a second card, a notification link, the picker's own
  // `nav.sheet("report", {id})` - all of them left the previous night on screen
  // under the new URL. The store's `lastReportId` is deliberately NOT a
  // dependency (see `wantedRef`): that one moves when a run finishes, and it
  // must not yank the selection out from under someone reading an older night.
  useEffect(() => {
    if (reportId) setSel(reportId);
  }, [reportId]);

  // Detail. Cancelled-flag guard against out-of-order responses, and CLEAR
  // FIRST: holding the outgoing report on screen while the new one loads left
  // the previous night's title, integration, rejected count and trends fully
  // rendered above a frames.csv button already pointing at the night that had
  // just been picked - every number belonging to one session and the download
  // to another.
  useEffect(() => {
    if (!sel) { setReport(null); return; }
    let cancelled = false;
    setReport(null);
    setLoading(true);
    setErr(null);
    void (async () => {
      try {
        const r = await getReport(sel);
        if (cancelled) return;
        setReport(r);
      } catch (e) {
        if (cancelled) return;
        setReport(null);
        setErr(e instanceof ApiError ? e.message : REPORT_COPY.detailErrorTitle);
        enqueueToast({ level: "error", title: REPORT_COPY.detailErrorTitle });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [sel, enqueueToast]);

  // The stacking-bundle preview, best-effort: a failure leaves the download
  // honest-locked and the group breakdown missing, and never blocks the report.
  // Re-runs when an option changes so the server-computed `kept_count` matches
  // the cutoff the user just set (the alternative - shipping a 2000-row weight
  // vector to the client - is what this route exists to avoid).
  useEffect(() => {
    if (!sel) {
      setPreview(null);
      setPreviewErr(null);
      setPreviewLoading(false);
      return;
    }
    let cancelled = false;
    setMatResult(null);  // a stale "linked 42" must not outlive its options
    setPreviewLoading(true);
    void (async () => {
      try {
        const p = await getBundlePreview(sel, { layout, weightAlt, keepThreshold: keepParam });
        if (cancelled) return;
        setPreview(p);
        setPreviewErr(null);
      } catch (e) {
        if (cancelled) return;
        setPreview(null);
        setPreviewErr(e instanceof ApiError ? e.message : "couldn't reach the server");
      } finally {
        if (!cancelled) setPreviewLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [sel, layout, weightAlt, keepParam, previewGen]);

  const materialize = useCallback(async () => {
    if (!sel) return;
    setMaterializing(true);
    try {
      const r = await materializeBundle(sel, { layout, weightAlt, keepThreshold: keepParam });
      setMatResult(r);
    } catch (e) {
      setMatResult(null);
      enqueueToast({
        level: "error",
        title: REPORT_COPY.materializeFailed,
        detail: e instanceof ApiError ? e.message : undefined,
      });
    } finally {
      setMaterializing(false);
    }
  }, [sel, layout, weightAlt, keepParam, enqueueToast]);

  return {
    list, listLoading, listErr, reloadList,
    sel, select: setSel,
    report, loading, err,
    preview, previewLoading, previewErr,
    retryPreview: () => setPreviewGen((g) => g + 1),
    bundle: {
      layout, setLayout, weightAlt, setWeightAlt,
      keepOn, setKeepOn, keepThreshold, setKeepThreshold, keepParam,
    },
    materializing, matResult, materialize,
  };
}
