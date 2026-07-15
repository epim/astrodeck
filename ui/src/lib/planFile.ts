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
