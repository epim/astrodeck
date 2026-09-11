// LogExport.tsx - the two download buttons under the Log screen, and the body
// of Settings > MORE > Log export (plan §B.2.4; T-SET-4 imports `LogExportPanel`
// from here, the one pre-authorised cross-directory import in this programme).
//
// WHY AN ANCHOR AND NOT A HANDLER: `/api/logs/export` answers with
// `Content-Disposition: attachment` bytes, and byte routes carry auth on the
// session cookie (server-routes.md §5). A plain link therefore downloads; a
// `fetch` + blob would re-implement the browser's download UI and lose the
// filename the server chose (`astrodeck-<night>.log.<ext>`).
//
// WHY THE LOCK IS PRE-CHECKED: the route 404s "log persistence is disabled" and
// "no persisted log for <night>". Both are knowable from `/api/logs/nights`
// BEFORE the tap, and a link that opens a new tab onto a 404 has spent the tap
// to say what this screen already knew.

import { useEffect, useState, type JSX } from "react";
import { api } from "../../../../api";
import { u } from "../../../../lib/base";
import { Chip, Label } from "../../../ui";
import {
  exportLockedReason, exportPath, nightOptions, resolveNight, RING,
  type ExportFormat, type NightsIndex,
} from "./logSources";

/** `GET /api/logs/nights` (view.status - every role). One fetch, shared by the
 *  Log screen's night chips and by the export lock, so the chip row and the
 *  buttons can never disagree about which nights exist.
 *
 *  No interval: the set of nights on disk changes once a day, at the night
 *  rollover, and a poll would be a request per minute for an answer that is the
 *  same until dawn. A session that spans the rollover keeps the chip row it
 *  mounted with; the ring, which is what TONIGHT reads, is live regardless. */
export function useLogNights(): { nights: NightsIndex | null; error: string | null } {
  const [nights, setNights] = useState<NightsIndex | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void api.get<NightsIndex>("/api/logs/nights")
      .then((r) => {
        if (!live) return;
        setNights({
          current: r.current ?? "",
          persisted: !!r.persisted,
          nights: Array.isArray(r.nights) ? r.nights : [],
        });
      })
      .catch(() => {
        // An older server has no /api/logs/nights. The ring still works, so the
        // screen degrades to TONIGHT-only rather than showing nothing - but it
        // must NOT then claim persistence is off, which is a different fact and
        // one this failure gives no evidence for.
        if (live) setError("this rig cannot list past nights or export them");
      });
    return () => { live = false; };
  }, []);
  return { nights, error };
}

function ExportButton({ night, format, nights, error, testId }: {
  night: string;
  format: ExportFormat;
  nights: NightsIndex | null;
  error: string | null;
  testId: string;
}): JSX.Element {
  const locked = error ?? exportLockedReason(night, nights);
  const label = format === "txt" ? "EXPORT .TXT" : "EXPORT .JSONL";
  if (locked) {
    return (
      <button
        type="button"
        className="nx-btn nx-locked"
        data-kind="secondary"
        aria-disabled="true"
        title={locked}
        data-testid={testId}
        data-locked-reason={locked}
        onClick={() => { /* refused - the reason is stated under the row */ }}
      >
        <span className="nx-btn-label">{label}</span>
      </button>
    );
  }
  return (
    <a
      className="nx-btn"
      data-kind="secondary"
      href={u(exportPath(resolveNight(night, nights), format))}
      target="_blank"
      rel="noreferrer"
      data-testid={testId}
    >
      <span className="nx-btn-label">{label}</span>
    </a>
  );
}

/** The buttons alone, for a screen that already knows which night is selected. */
export function LogExport({ night, nights, error = null }: {
  night: string;
  nights: NightsIndex | null;
  error?: string | null;
}): JSX.Element {
  const locked = error ?? exportLockedReason(night, nights);
  return (
    <div data-testid="log-export" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div style={{ display: "flex", gap: 6 }}>
        <ExportButton night={night} format="txt" nights={nights} error={error} testId="log-export-txt" />
        <ExportButton night={night} format="jsonl" nights={nights} error={error} testId="log-export-jsonl" />
      </div>
      <div className="nx-empty-hint">
        {locked
          ? locked
          : ".txt is the readable transcript, .jsonl the raw rows including debug lines."}
      </div>
    </div>
  );
}

/** The whole sheet body: pick a night, then download it. Self-contained, so
 *  Settings can mount it without knowing anything about the Log screen's state
 *  (plan §C.6 - the `logExport` sheet). */
export function LogExportPanel(): JSX.Element {
  const { nights, error } = useLogNights();
  const [night, setNight] = useState<string>(RING);
  const opts = nightOptions(nights);
  return (
    <div data-testid="log-export-panel" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <Label>NIGHT</Label>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {opts.map((o) => (
          <Chip key={o.id || "tonight"} active={o.id === night} onClick={() => setNight(o.id)}>
            {o.label}
          </Chip>
        ))}
      </div>
      <LogExport night={night} nights={nights} error={error} />
    </div>
  );
}
