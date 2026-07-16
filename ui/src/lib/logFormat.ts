// logFormat.ts — pure formatting helpers for the Event Log drawer (R2-LOG-01
// partial: entries showed source + message but no timestamp or severity
// word). Both GET /api/logs and the WS "log" event already carry a top-level
// `ts` (epoch seconds — events.py Event.to_json) and `data.level`, so this is
// presentation-only; no payload change was needed.
export function fmtLogTime(ts: number): string {
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  const ss = String(d.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

const SEVERITY_WORDS: Record<string, string> = {
  error: "Error", warning: "Warning", info: "Info", debug: "Debug",
};

/** A human severity word. An unrecognized level (future-proofing) falls back
 *  to the raw value Title-cased rather than disappearing. */
export function severityWord(level: string): string {
  const known = SEVERITY_WORDS[level];
  if (known) return known;
  return level ? level.charAt(0).toUpperCase() + level.slice(1) : "Info";
}
