// planFile.ts — plan-library import-file parsing (sessions spec §7). The
// server does the real schema validation (POST /api/plans/import → 422 with
// version_too_new/invalid codes); this only guards the obvious non-files so
// the user gets an instant, friendly error without a round-trip.
export function parsePlanFile(text: string): Record<string, unknown> {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error("not a JSON file");
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("not an AstroDeck plan file");
  }
  return raw as Record<string, unknown>;
}

/** The filename GET /api/plans/{id}/export sends as Content-Disposition
 *  (server: `app.py::export_plan`) — mirrored here ONLY so the export toast
 *  (R2-PLN-03) can name the actual downloaded file; the server's sanitizing
 *  is canonical and this never rides a request. */
export function planExportFilename(name: string): string {
  const safe = Array.from(name)
    .map((c) => (/[\p{L}\p{N}_ -]/u.test(c) ? c : "_"))
    .join("")
    .trim() || "plan";
  return `${safe}.astroplan.json`;
}
