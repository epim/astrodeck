// lib/apiError.ts — parses a failed fetch response's JSON body into an
// { message, code, id } triple for api.ts's req(). Kept dependency-free (no
// lib/base.ts import, which reads `window.location` at module-load time) so
// it can be unit-tested under plain Node/tsx — see
// lib/__tests__/apiError.test.ts.
//
// FastAPI's `HTTPException(status, detail={"detail": "...", "code": "..."})`
// idiom (used throughout the server for machine-readable error codes, e.g.
// server/astrodeck/api/app.py's "name_collision"/"running"/"version_too_new")
// serializes as a NESTED `{"detail": {"detail": "...", "code": "..."}}` —
// proven by server/tests/test_activate_profile.py:111 asserting
// `r.json()["detail"]["code"]`. A flat top-level `{"code": "...", "detail": "..."}`
// or a plain string `{"detail": "..."}` are also accepted so this stays
// backward-compatible with any endpoint using a simpler shape.
//
// `id` carries the target-resource id some 409 payloads include — e.g.
// POST /api/locations' name_collision detail is
// `{"code": "name_collision", "id": <existing location id>}`
// (app.py:1427-1429), which the UI needs as the authoritative overwrite
// target (client-side name re-matching can disagree with the server's
// casefold()).
export function parseApiError(
  status: number,
  body: unknown,
  fallback: string = `HTTP ${status}`,
): { message: string; code?: string; id?: string } {
  if (body && typeof body === "object") {
    const j = body as Record<string, unknown>;
    const detail = j.detail;
    if (detail && typeof detail === "object") {
      // nested: HTTPException(status, detail={"detail": "...", "code": "..."})
      const d = detail as Record<string, unknown>;
      return {
        message: typeof d.detail === "string" ? d.detail : JSON.stringify(detail),
        code: typeof d.code === "string" ? d.code : undefined,
        id: typeof d.id === "string" ? d.id : undefined,
      };
    }
    return {
      message: typeof detail === "string" ? detail : JSON.stringify(detail ?? j),
      code: typeof j.code === "string" ? j.code : undefined,
      id: typeof j.id === "string" ? j.id : undefined,
    };
  }
  return { message: fallback };
}
