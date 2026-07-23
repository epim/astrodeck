// views/ReportView.tsx — end-of-night session report viewer (report viewer
// spec §3 Task 3). Thin render over the Task-1 tested `reportChart` helpers +
// the existing api/graphs/ui primitives: a report picker, header block,
// per-filter table, per-target breakdown, safety-event timeline, three
// TrendLines, and a frames.csv download link.
//
// A READ (CAP_VIEW_STATUS) — viewable by every role, no gated controls, no
// writes. Not a primary-nav entry (App.tsx VIEWS comment); reached from the
// SequenceView run-complete link and the NavMoreSheet overflow.

import { useEffect, useState } from "react";
import { ApiError } from "../api";
import { listReports, getReport, getBundlePreview } from "../api/reports";
import { useStore, useLastReportId } from "../store";
import { Panel, EmptyState, Stat } from "../components/ui";
import { Icon } from "../components/icons";
import { TrendLine } from "../components/graphs";
import { fmtDuration } from "../lib/eta";
import { endReasonMeta } from "../lib/reportChart";
import { masterChips, bundleDisabledReason } from "../lib/bundleView";
import { BASE } from "../lib/base";
import type {
  BundleGroupSummary,
  BundlePreview,
  FilterBreakdown,
  SessionReport,
  SessionReportSummary,
} from "../types";

/** `<filter> · <frames> frames · <integration> · HFR <median>` + optional
 *  `<rejected> rejected` — mirrors SequenceView's finished-panel language and
 *  the Sessions bar rollup (report viewer spec §1 "Copy tables"). */
function FilterRow({ f }: { f: FilterBreakdown }) {
  return (
    <div className="flex items-center justify-between gap-3 text-xs py-1.5 border-b border-line/40 last:border-0">
      <span className="mono text-ink shrink-0">{f.filter}</span>
      <span className="text-dim text-right">
        {f.frames} frames · {fmtDuration(f.integration_s)} · HFR{" "}
        {f.hfr_median != null ? f.hfr_median.toFixed(2) : "—"}
        {f.rejected > 0 && <span className="text-warn"> · {f.rejected} rejected</span>}
      </span>
    </div>
  );
}

/** One group line in the stacking-bundle panel:
 *  `M42 · Ha · 300s · g100 · 42 lights · Dark ✓ · Flat ✓ · Bias —`. Master
 *  status uses Icon + kind (never color-only): check when matched, x when not. */
function BundleGroupRow({ g }: { g: BundleGroupSummary }) {
  const parts = [g.target, g.filter ?? "NoFilter", `${g.exposure_s}s`];
  if (g.gain != null) parts.push(`g${g.gain}`);
  if (g.binning != null) parts.push(`bin${g.binning}`);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs py-1.5 border-b border-line/40 last:border-0">
      <span className="mono text-ink shrink-0">{parts.join(" · ")}</span>
      <span className="text-dim">
        {g.light_count} lights
        {g.accepted_count !== g.light_count && ` (${g.accepted_count} accepted)`}
      </span>
      <span className="ml-auto flex items-center gap-2.5">
        {masterChips(g.masters).map((c) => (
          <span
            key={c.kind}
            className={`inline-flex items-center gap-0.5 ${c.ok ? "text-good" : "text-dim"}`}
            title={c.ok ? `${c.kind} master matched` : `no ${c.kind} master`}
          >
            <Icon name={c.ok ? "check" : "x"} size={11} />
            <span className="capitalize">{c.kind}</span>
          </span>
        ))}
      </span>
    </div>
  );
}

/** End-reason word+tone chip — Icon + WORD always (never color-only), same
 *  idiom as monitor.tsx's StateBadge. */
function ReasonChip({ reason }: { reason: string | null }) {
  const m = endReasonMeta(reason);
  const cls = m.tone === "good" ? "text-good" : m.tone === "warn" ? "text-warn" : "text-bad";
  const glyph = m.tone === "good" ? "check" : m.tone === "bad" ? "x" : "alert";
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-display font-semibold tracking-[0.15em] uppercase ${cls}`}>
      <Icon name={glyph} size={13} />
      {m.word}
    </span>
  );
}

export default function ReportView() {
  const lastReportId = useLastReportId();
  const enqueueToast = useStore((s) => s.enqueueToast);

  const [list, setList] = useState<SessionReportSummary[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [report, setReport] = useState<SessionReport | null>(null);
  const [preview, setPreview] = useState<BundlePreview | null>(null);
  const [weightAlt, setWeightAlt] = useState(false); // opt-in sin(alt) bundle weighting
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Effect A (mount): list all reports, default to lastReportId or the newest
  // (the list route is already newest-first — report.py:374-394).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const l = await listReports();
        if (cancelled) return;
        setList(l);
        setSel((prev) => prev ?? lastReportId ?? l[0]?.id ?? null);
      } catch {
        if (!cancelled) enqueueToast({ level: "error", title: "Couldn't load reports" });
      }
    })();
    return () => {
      cancelled = true;
    };
    // mount-only: a later store `lastReportId` change must not yank the
    // selection out from under someone browsing an older report.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Effect B (sel): load the full detail. Cancelled-flag guard against
  // out-of-order responses (MonitorView.tsx cold-load pattern). The stacking-
  // bundle preview loads alongside, best-effort — a preview failure just leaves
  // the panel's download honest-disabled, never blocks the report render.
  useEffect(() => {
    if (!sel) {
      setReport(null);
      setPreview(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr(null);
    setPreview(null);
    (async () => {
      try {
        const r = await getReport(sel);
        if (cancelled) return;
        setReport(r);
      } catch (e) {
        if (cancelled) return;
        setReport(null);
        setErr(e instanceof ApiError ? e.message : "Couldn't load report");
        enqueueToast({ level: "error", title: "Couldn't load report" });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    (async () => {
      try {
        const p = await getBundlePreview(sel);
        if (!cancelled) setPreview(p);
      } catch {
        // best-effort: leave preview null (panel stays honest-disabled)
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sel, enqueueToast]);

  return (
    <div className="px-3 pb-20 sm:px-0 sm:pb-4 flex flex-col gap-3">
      <Panel title="Session Report">
        {list.length === 0 ? (
          <EmptyState
            icon="plan"
            title="No reports yet"
            hint="A report is generated whenever a sequence finishes, is aborted, or errors out."
          />
        ) : (
          <select
            className="field"
            value={sel ?? ""}
            onChange={(e) => setSel(e.target.value || null)}
            aria-label="Select report"
          >
            {list.map((r) => (
              <option key={r.id} value={r.id}>
                {r.plan_name} · {new Date(r.started_at * 1000).toLocaleString()}
              </option>
            ))}
          </select>
        )}
      </Panel>

      {loading && !report && <p className="text-dim text-xs text-center py-6">loading…</p>}

      {err && (
        <Panel>
          <EmptyState icon="alert" title="Couldn't load report" hint={err} />
        </Panel>
      )}

      {report && !err && (
        <>
          {/* --------------------------------------------------- header block */}
          <Panel title={report.plan_name}>
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex flex-col gap-1.5">
                <ReasonChip reason={report.end_reason} />
                <div className="text-xs text-dim mono">
                  started {new Date(report.started_at * 1000).toLocaleString()}
                  {report.ended_at != null && (
                    <> · ended {new Date(report.ended_at * 1000).toLocaleString()}</>
                  )}
                </div>
              </div>
              <div className="flex gap-4">
                <Stat label="integration" value={fmtDuration(report.integration_s)} />
                <Stat label="accepted" value={report.frames_captured} />
                <Stat
                  label="rejected"
                  value={report.frames_rejected}
                  tone={report.frames_rejected > 0 ? "warn" : undefined}
                />
              </div>
            </div>
          </Panel>

          {/* -------------------------------------------- per-filter (headline) */}
          <Panel title="By filter">
            {report.by_filter.length === 0 ? (
              <p className="text-dim text-xs">no frames</p>
            ) : (
              <div className="overflow-x-auto">
                <div className="min-w-[420px]">
                  {report.by_filter.map((f, i) => (
                    <FilterRow key={`${f.filter}-${i}`} f={f} />
                  ))}
                </div>
              </div>
            )}
          </Panel>

          {/* ------------------------------------------------- per-target breakdown */}
          <Panel title="By target">
            {report.targets.length === 0 ? (
              <p className="text-dim text-xs">no targets</p>
            ) : (
              <div className="flex flex-col gap-4">
                {report.targets.map((t, ti) => (
                  <div key={`${t.name}-${ti}`} className="flex flex-col gap-1">
                    <div className="flex items-center justify-between gap-2 text-sm flex-wrap">
                      <span className="text-ink font-medium">{t.name}</span>
                      <span className="text-xs text-dim mono">
                        {t.frames} frames · {fmtDuration(t.integration_s)}
                        {t.rejected > 0 && ` · ${t.rejected} rejected`}
                      </span>
                    </div>
                    <div className="overflow-x-auto pl-3 border-l border-line/40">
                      <div className="min-w-[400px]">
                        {t.by_filter.map((f, fi) => (
                          <FilterRow key={`${f.filter}-${fi}`} f={f} />
                        ))}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>

          {/* -------------------------------------------------- safety timeline */}
          {report.safety_events.length > 0 && (
            <Panel title="Safety events">
              <div className="overflow-x-auto">
                <div className="min-w-[360px] flex flex-col gap-1.5">
                  {report.safety_events.map((ev, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className="mono text-dim shrink-0">
                        {new Date(ev.ts * 1000).toLocaleTimeString()}
                      </span>
                      <span className="text-ink truncate">{ev.reason}</span>
                      <span className="ml-auto shrink-0 inline-flex items-center gap-1 border border-warn/50 bg-warn/10 px-1.5 py-0.5 text-[10px] text-warn uppercase tracking-wide">
                        {ev.action}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </Panel>
          )}

          {/* ------------------------------------------------------------ trends */}
          <Panel title="Trends">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <TrendLine values={report.trends.hfr.map((p) => p[1])} label="HFR" />
              <TrendLine values={report.trends.temp.map((p) => p[1])} label="Sensor °C" unit="°C" decimals={1} />
              <TrendLine values={report.trends.rms.map((p) => p[1])} label="Guide RMS" unit="″" />
            </div>
          </Panel>

          {/* ------------------------------------------------- stacking bundle */}
          <Panel title="Stacking bundle">
            {(() => {
              const reason = bundleDisabledReason(report.frames_captured, preview);
              return (
                <div className="flex flex-col gap-3">
                  <p className="text-xs text-dim">
                    Light subs grouped by target/filter/exposure with matched masters
                    and a per-sub weighting manifest — one .zip you can materialize
                    with the included build script (PixInsight / Siril / APP).
                  </p>
                  {preview && preview.groups.length > 0 && (
                    <div className="overflow-x-auto">
                      <div className="min-w-[420px]">
                        {preview.groups.map((g, i) => (
                          <BundleGroupRow key={`${g.dir}-${i}`} g={g} />
                        ))}
                      </div>
                    </div>
                  )}
                  {preview?.warnings.map((w, i) => (
                    <p key={i} className="text-xs text-warn flex items-center gap-1.5">
                      <Icon name="alert" size={12} /> {w}
                    </p>
                  ))}
                  {!reason && (
                    <label
                      className="flex items-center gap-2 self-end text-xs text-dim cursor-pointer"
                      title="Fold a sin(altitude) transparency term into each sub's weight — higher subs (less airmass) score higher. Off by default: the weight is sharpness (HFR) + roundness (ecc) + guide RMS."
                    >
                      <input
                        type="checkbox"
                        checked={weightAlt}
                        onChange={(e) => setWeightAlt(e.target.checked)}
                      />
                      Weight subs by altitude
                    </label>
                  )}
                  <div className="flex justify-end">
                    {reason ? (
                      <span
                        aria-disabled="true"
                        title={reason}
                        className="btn inline-flex items-center gap-1.5 min-h-[44px] opacity-50 cursor-not-allowed"
                      >
                        <Icon name="lock" size={12} /> Download bundle.zip
                      </span>
                    ) : (
                      <a
                        href={`${BASE}/api/reports/${encodeURIComponent(sel ?? "")}/bundle.zip${weightAlt ? "?weight_altitude=1" : ""}`}
                        download
                        className="btn inline-flex items-center gap-1.5 min-h-[44px]"
                      >
                        <Icon name="download" size={12} /> Download bundle.zip
                      </a>
                    )}
                  </div>
                </div>
              );
            })()}
          </Panel>

          {/* --------------------------------------------------------- csv link */}
          <div className="flex justify-end">
            <a
              href={`${BASE}/api/reports/${encodeURIComponent(sel ?? "")}/frames.csv`}
              download
              className="btn inline-flex items-center gap-1.5 min-h-[44px]"
            >
              <Icon name="download" size={12} /> Download frames.csv
            </a>
          </div>
        </>
      )}
    </div>
  );
}
