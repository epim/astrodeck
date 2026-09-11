// LogExportSheet.tsx - Settings > MORE > Log export (plan section C.6, row
// LOG EXPORT; section B.2.4).
//
// Built FRESH, not reused from Monitor: `hubs/monitor/log/LogExport.tsx`
// (T-MON-1) has not landed yet, and the plan pre-authorises this ONE
// cross-directory dependency to run in the opposite direction instead -
// settings implements the export UI now, and the Monitor task's Log screen
// may import THIS component later instead of building its own (the same two
// buttons are also the footer of `#/monitor/log`, per the plan).
//
// Three sources exist for the engine's log (`inventory-settings-weather.md`
// section on Monitor > Log): the live in-memory ring (last ~200 lines), the
// persisted per-night file, and `GET /api/logs/nights` - which nights are on
// disk. Export always reads the PERSISTED file
// (`GET /api/logs/export?night=YYYY-MM-DD&format=txt|jsonl`), so a night that
// exists only in the live ring (never flushed to disk yet) cannot be
// exported - the buttons say so rather than opening a tab onto a 404.
import { useEffect, useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import { nav } from "../../../router";
import { NxIcon } from "../../../icons";
import { Card, EmptyCard, Label, Sheet, lockedAttrs, lockedClass } from "../../../ui";
import { api } from "../../../../api";
import { u } from "../../../../lib/base";
import "./sheets.css";

interface LogNightEntry {
  night: string;
  bytes: number;
}

interface LogNights {
  current: string;
  persisted: boolean;
  nights: LogNightEntry[];
}

/** "2.31 GB" / "412 MB" - same scale idiom as `SyncPanel.tsx`'s `formatBytes`,
 *  written fresh here rather than imported: this file has no other reason to
 *  reach into `components/settings/`. */
function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)} GB`;
  if (n >= 1e6) return `${Math.round(n / 1e6)} MB`;
  if (n >= 1e3) return `${Math.round(n / 1e3)} kB`;
  return `${Math.round(n)} B`;
}

/** The exact `GET /api/logs/export` URL for one night and format, through
 *  `u()` so the relay mount prefix is carried (`lib/base.ts` - every runtime
 *  URL goes through it). */
export function exportHref(night: string, format: "txt" | "jsonl"): string {
  const q = new URLSearchParams({ night, format }).toString();
  return u(`/api/logs/export?${q}`);
}

export function LogExportSheet(_p: SheetProps): JSX.Element {
  const [data, setData] = useState<LogNights | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [night, setNight] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.get<LogNights>("/api/logs/nights")
      .then((d) => {
        if (!live) return;
        setData(d);
        setNight((cur) => cur ?? d.current);
      })
      .catch((e) => {
        if (!live) return;
        setErr(e instanceof Error ? e.message : "couldn't read which nights are on disk");
      });
    return () => { live = false; };
  }, []);

  const nights = data?.nights ?? [];
  const persisted = data?.persisted ?? false;
  const onDisk = night != null && nights.some((n) => n.night === night);
  // Precedence matches the honest-absent rule (plan D.2): a rig with logging
  // off can never export anything, so that reason wins over "this particular
  // night is missing" - the second sentence would otherwise imply every OTHER
  // night is fine, which on this rig is never true.
  const locked = data == null
    ? "reading which nights are on disk..."
    : !persisted
      ? "log persistence is off on this rig - there is nothing to export"
      : !onDisk
        ? "this night was never written to disk"
        : null;

  const sortedNights = [...nights].sort((a, b) => (a.night < b.night ? 1 : a.night > b.night ? -1 : 0));
  const otherNights = sortedNights.filter((n) => n.night !== data?.current);

  const nightChip = (id: string, label: string, testid: string) => (
    <button
      type="button"
      key={id}
      data-testid={testid}
      onClick={() => setNight(id)}
      aria-pressed={night === id}
      data-selected={night === id ? "true" : "false"}
      className="nx-chip"
      data-tone="accent"
      data-active={night === id ? "true" : "false"}
    >
      {label}
    </button>
  );

  const exportButton = (format: "txt" | "jsonl", label: string) => (
    <a
      data-testid={`log-export-${format}`}
      className={lockedClass(locked, "nx-btn")}
      data-kind="secondary"
      data-size="md"
      href={locked || night == null ? undefined : exportHref(night, format)}
      target={locked || night == null ? undefined : "_blank"}
      rel="noreferrer"
      onClick={locked || night == null ? (e) => e.preventDefault() : undefined}
      {...lockedAttrs(locked)}
    >
      <span className="nx-btn-label">{label}</span>
    </a>
  );

  return (
    <Sheet
      data-testid="settings-logExport"
      title="LOG EXPORT"
      sub={data ? `${nights.length} night${nights.length === 1 ? "" : "s"} on disk` : "reading which nights are on disk"}
      icon={<NxIcon name="download" />}
      onBack={nav.back}
    >
      {err && (
        <EmptyCard title="COULD NOT READ THE LOG NIGHTS" hint={err} />
      )}

      {!err && data && !persisted && (
        <Card tone="dashed">
          <p>Log persistence is off on this rig, so only the last 200 lines are kept.</p>
        </Card>
      )}

      {!err && data && persisted && (
        <Card>
          <Label>NIGHT</Label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}
               role="group" aria-label="Night">
            {nightChip(data.current, "TONIGHT", "night-tonight")}
            {otherNights.map((n) =>
              nightChip(n.night, `${n.night} · ${fmtBytes(n.bytes)}`, `night-${n.night}`))}
          </div>
        </Card>
      )}

      <Card>
        <Label>EXPORT</Label>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}>
          {exportButton("txt", "EXPORT .TXT")}
          {exportButton("jsonl", "EXPORT .JSONL")}
        </div>
        {locked && data && (
          <p style={{ marginTop: 6 }} className="nx-locked-note">{locked}</p>
        )}
      </Card>
    </Sheet>
  );
}
