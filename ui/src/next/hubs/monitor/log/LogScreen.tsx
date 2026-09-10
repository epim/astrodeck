// LogScreen.tsx - MONITOR - LOG. Everything the engine did tonight, newest
// first, plus the nights before it and a way to get the raw file off the rig.
//
// THE THREE THINGS THIS SCREEN IS FOR:
//
//  1. The ring is NOT the log. `store.logs` holds 200 lines - about 40 minutes
//     of a ten-hour run - and the legacy drawer showed only that, so "what
//     happened at 23:40" was unanswerable at 03:00 on the one screen that
//     exists to answer it. TONIGHT reads the ring; every other chip reads the
//     PERSISTED file through `GET /api/logs?night=`.
//  2. Selecting TONIGHT issues NO request. The ring is already here; re-asking
//     for it would look identical and cost a round trip per tap.
//  3. A humanised line that hides the actual error string is not diagnosable.
//     `humanizeLog` leads, and where it rewrote the line the raw text is one
//     tap away on the row itself.
//
// `openLog()` on mount / `closeLog()` on unmount (plan §F.11): the legacy
// `LogDrawer` is not mounted under `NextApp`, so `logOpen` is now only the
// `unseenError` reset flag - entering this screen is what clears the error
// badge, exactly as opening the drawer used to.

import { useEffect, useMemo, useState, type JSX } from "react";
import { api } from "../../../../api";
import { useStore, useLogs, useSeq } from "../../../../store";
import { humanizeLog } from "../../../../lib/humanize";
import { fmtLogTime, severityWord } from "../../../../lib/logFormat";
import type { LogLine } from "../../../../types";
import { Card, Chip, Label, Mono } from "../../../ui";
import { monState } from "../monState";
import { LogExport, useLogNights } from "./LogExport";
import {
  LEVELS, RING, filterByLevel, logQueryPath, newestFirst, nightOptions, parseLevel,
  type LevelFilter,
} from "./logSources";

const NIGHT_KEY = "astrodeck-next-mon-night";
const LEVEL_KEY = "astrodeck-next-mon-level";

function read(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function write(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* private mode - in memory only */ }
}

/** The design's note, with its last clause replaced. The original says "Full
 *  logs with debug lines export from the tablet"; there is no separate tablet
 *  build and the export is on this screen, so the sentence pointed at nothing. */
export const LOG_NOTE =
  "Everything the engine did tonight, newest first. Older nights and the raw lines are under EXPORT.";

const TONE: Record<string, string> = {
  error: "var(--bad)",
  warning: "var(--warn)",
  info: "var(--text)",
  debug: "var(--text-faint)",
};

function LogRow({ row }: { row: LogLine }): JSX.Element {
  const [raw, setRaw] = useState(false);
  const message = row.data?.message ?? "";
  const human = humanizeLog(row.data);
  const rewritten = human !== message;
  const color = TONE[row.data?.level ?? "info"] ?? "var(--text)";

  const body = (
    <>
      <Mono size={10.5} tone="dim">{fmtLogTime(row.ts)}</Mono>
      <span style={{
        fontFamily: '"IBM Plex Mono", monospace', fontSize: 10.5, lineHeight: 1.5, color,
        minWidth: 0, overflowWrap: "anywhere",
      }}>
        {human}
      </span>
    </>
  );

  if (!rewritten) {
    return (
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", minHeight: 22 }}>
        {body}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      <button
        type="button"
        onClick={() => setRaw((v) => !v)}
        aria-expanded={raw}
        aria-label={`${severityWord(row.data.level)} at ${fmtLogTime(row.ts)} - show the raw line`}
        style={{
          display: "flex", gap: 8, alignItems: "baseline", minHeight: 44, width: "100%",
          background: "transparent", border: 0, padding: 0, textAlign: "left", cursor: "pointer",
        }}
      >
        {body}
        <span style={{ marginLeft: "auto", color: "var(--text-faint)", fontSize: 10 }}>
          {raw ? "HIDE RAW" : "RAW"}
        </span>
      </button>
      {raw && (
        <span style={{
          fontFamily: '"IBM Plex Mono", monospace', fontSize: 10, lineHeight: 1.5,
          color: "var(--text-faint)", paddingLeft: 8, overflowWrap: "anywhere",
        }}>
          [{severityWord(row.data.level)} · {row.data.source}] {message}
        </span>
      )}
    </div>
  );
}

export function LogScreen(): JSX.Element {
  const seq = useSeq();
  const ring = useLogs();
  const { nights, error: nightsError } = useLogNights();

  const [night, setNight] = useState<string>(() => read(NIGHT_KEY) ?? RING);
  const [level, setLevel] = useState<LevelFilter>(() => parseLevel(read(LEVEL_KEY)));
  const [fileRows, setFileRows] = useState<LogLine[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  // The unseen-error badge is reset by being here, not by a drawer opening.
  useEffect(() => {
    useStore.getState().openLog();
    return () => { useStore.getState().closeLog(); };
  }, []);

  // A night the rig no longer lists (it aged out of the store between sessions)
  // would otherwise leave the screen asking for a file that is gone, forever.
  useEffect(() => {
    if (!night || !nights) return;
    if (nights.persisted && nights.nights.some((n) => n.night === night)) return;
    setNight(RING);
    write(NIGHT_KEY, RING);
  }, [night, nights]);

  const path = logQueryPath(night, level);
  useEffect(() => {
    if (path == null) { setFileRows(null); setLoadError(null); setLoading(false); return; }
    let live = true;
    setLoading(true);
    setLoadError(null);
    void api.get<LogLine[]>(path)
      .then((rows) => { if (live) { setFileRows(Array.isArray(rows) ? rows : []); setLoading(false); } })
      .catch((e) => {
        if (!live) return;
        setFileRows([]);
        setLoading(false);
        setLoadError(e instanceof Error ? e.message : "that night could not be read");
      });
    return () => { live = false; };
  }, [path]);

  // The ring filters here; a night was filtered by the route. One meaning, two
  // places, so the filter itself is one function (`filterByLevel`).
  const rows = useMemo(
    () => newestFirst(night ? (fileRows ?? []) : filterByLevel(ring, level)),
    [night, fileRows, ring, level],
  );

  const opts = nightOptions(nights);
  const sub = monState({ state: seq.state, target: seq.target, incidentTitle: null });

  return (
    <div data-testid="monitor-log" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 10 }}>
        <Label size={11}>LOG</Label>
        <Mono size={10} tone="dim">{sub}</Mono>
      </div>

      <div role="group" aria-label="Which night" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {opts.map((o) => (
          <Chip
            key={o.id || "tonight"}
            active={o.id === night}
            onClick={() => { setNight(o.id); write(NIGHT_KEY, o.id); }}
            data-testid={o.id ? `log-night-${o.id}` : "log-night-tonight"}
          >
            {o.label}
          </Chip>
        ))}
      </div>

      {nights && !nights.persisted && (
        <div className="nx-empty-hint">
          Log persistence is off on this rig, so only the last 200 lines are kept.
        </div>
      )}

      {nightsError && <div className="nx-empty-hint">{nightsError}</div>}

      <div role="group" aria-label="Level" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {LEVELS.map((l) => (
          <Chip
            key={l.id || "all"}
            active={l.id === level}
            onClick={() => { setLevel(l.id); write(LEVEL_KEY, l.id); }}
            data-testid={`log-level-${l.id || "all"}`}
          >
            {l.label}
          </Chip>
        ))}
      </div>

      <Card padding={12}>
        <div data-testid="log-rows" style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {loading && <Mono size={10.5} tone="dim">reading {night}...</Mono>}
          {!loading && loadError && (
            <Mono size={10.5} tone="bad">{loadError}</Mono>
          )}
          {!loading && !loadError && rows.length === 0 && (
            <Mono size={10.5} tone="dim">
              {level
                ? `nothing at ${level} level ${night ? `on ${night}` : "tonight"}`
                : night ? `no lines were kept for ${night}` : "no events yet tonight"}
            </Mono>
          )}
          {rows.map((r, i) => <LogRow key={`${r.ts}-${i}`} row={r} />)}
        </div>
      </Card>

      <div className="nx-empty-hint">{LOG_NOTE}</div>

      <LogExport night={night} nights={nights} error={nightsError} />
    </div>
  );
}
