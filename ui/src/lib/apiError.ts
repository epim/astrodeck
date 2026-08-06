// lib/apiError.ts — parses a failed fetch response's JSON body into an
// { message, code, id } triple for api.ts's req(). Kept dependency-free (no
// lib/base.ts import, which reads `window.location` at module-load time) so
// it can be unit-tested under plain Node/tsx — see
// lib/__tests__/apiError.test.ts. The one import, lib/humanize.ts, has no
// imports of its own and touches no globals, so that stays true.
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
//
// A THIRD shape: a plain (un-pre-validated) FastAPI/pydantic `Field(...)`
// constraint violation — e.g. `exposure_s: float = Field(gt=0, le=3600)` with
// no matching client-side guard — serializes as FastAPI's default 422 body,
// `{"detail": [{"type": "...", "loc": [...], "msg": "...", ...}, ...]}`. An
// array also satisfies `typeof detail === "object"` in JS, so without an
// explicit check it fell into the nested-custom-shape branch, found no
// `.detail`, and stringified the whole raw array into the toast message.
// Surface the first item's `msg` (+ the field name from `loc`) instead.
import { humanizeLaneConflict } from "./humanize";

/** Last stop before a server string becomes a red toast.
 *
 *  Applied to every branch below rather than to the one that produces lane
 *  refusals today, because which branch that is depends on how the route was
 *  written: `_spawn`'s 409 is a bare string detail, but any route that grows a
 *  machine-readable `code` for the same refusal moves it into the nested shape.
 *  One funnel means a lane name cannot reappear by being raised differently. */
function forHumans(message: string): string {
  return humanizeLaneConflict(message) ?? message;
}

function messageFromValidationErrors(items: unknown[]): string {
  const first = items[0];
  if (first && typeof first === "object") {
    const f = first as Record<string, unknown>;
    const msg = typeof f.msg === "string" ? f.msg : undefined;
    const loc = Array.isArray(f.loc) ? f.loc : undefined;
    // loc is typically ["body", "targets", 0, "steps", 0, "exposure_s"] —
    // the last non-index segment is the most useful field name to show.
    const field = loc ? [...loc].reverse().find((p) => typeof p === "string") : undefined;
    if (msg) return field ? `${field}: ${msg}` : msg;
  }
  return "Invalid request";
}

export function parseApiError(
  status: number,
  body: unknown,
  fallback: string = `HTTP ${status}`,
): { message: string; code?: string; id?: string } {
  if (body && typeof body === "object") {
    const j = body as Record<string, unknown>;
    const detail = j.detail;
    if (Array.isArray(detail)) {
      // plain pydantic 422 validation-error array — never the custom
      // {detail, code} shape, and never JSON.stringify'd raw into the UI.
      return { message: forHumans(messageFromValidationErrors(detail)) };
    }
    if (detail && typeof detail === "object") {
      // nested: HTTPException(status, detail={"detail": "...", "code": "..."})
      const d = detail as Record<string, unknown>;
      return {
        message: forHumans(typeof d.detail === "string" ? d.detail : JSON.stringify(detail)),
        code: typeof d.code === "string" ? d.code : undefined,
        id: typeof d.id === "string" ? d.id : undefined,
      };
    }
    return {
      message: forHumans(typeof detail === "string" ? detail : JSON.stringify(detail ?? j)),
      code: typeof j.code === "string" ? j.code : undefined,
      id: typeof j.id === "string" ? j.id : undefined,
    };
  }
  return { message: forHumans(fallback) };
}
