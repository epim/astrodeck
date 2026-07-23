// dome.ts — pure formatters for the observatory roof/dome (PRO-4). Mirrors the
// server's DomeShutterState string vocabulary (devices/base.py). Pure + tiny so
// the SafetyPanel roof section and its tsx logic test share one source of truth.

export type DomeShutter =
  | "open"
  | "closed"
  | "opening"
  | "closing"
  | "unknown"
  | "error";

/** Human status line for the roof, e.g. "closed" -> "Roof closed". */
export function domeStatusLabel(s: DomeShutter): string {
  switch (s) {
    case "open":
      return "Roof open";
    case "closed":
      return "Roof closed";
    case "opening":
      return "Roof opening…";
    case "closing":
      return "Roof closing…";
    case "error":
      return "Roof error";
    default:
      return "Roof status unknown";
  }
}

export function domeIsClosed(s: DomeShutter): boolean {
  return s === "closed";
}
