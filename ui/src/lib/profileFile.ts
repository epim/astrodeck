// profileFile.ts — profile-library import/export file helpers (F7 #5b,
// PlanLibraryPanel/planFile.ts precedent, commit de2839a). Profiles have no
// server-side export route (unlike GET /api/plans/{id}/export), so export is
// fully client-side: stringify the full Profile and trigger a Blob download.
// Import posts the parsed JSON straight to POST /api/profiles, which is
// already the real validator (Profile pydantic model) AND already refuses to
// trust a client-supplied id for a brand-new record (app.py::save_profile —
// mints a fresh server-side uuid unless that id already exists on THIS
// server), so a stranger's export can never silently clobber an existing
// profile. This module only screens the obvious non-files so a bad drop gets
// an instant, friendly error without a round-trip.
export function parseProfileFile(text: string): Record<string, unknown> {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    throw new Error("not a JSON file");
  }
  if (raw === null || typeof raw !== "object" || Array.isArray(raw)) {
    throw new Error("not an AstroDeck profile file");
  }
  const obj = raw as Record<string, unknown>;
  if (typeof obj.name !== "string" || !Array.isArray(obj.devices)) {
    throw new Error("not an AstroDeck profile file");
  }
  return obj;
}

/** Sanitized download filename — mirrors planExportFilename's character rule
 *  (lib/planFile.ts) so the two export flows read as one system. */
export function profileExportFilename(name: string): string {
  const safe = Array.from(name)
    .map((c) => (/[\p{L}\p{N}_ -]/u.test(c) ? c : "_"))
    .join("")
    .trim() || "profile";
  return `${safe}.astroprofile.json`;
}
