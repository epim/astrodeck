// apiError.test.ts — pure tests for lib/apiError.ts's parseApiError, which
// api.ts's req() wires into ApiError.message/.code. FastAPI's
// `HTTPException(status, detail={"detail": "...", "code": "..."})` idiom
// serializes as a NESTED `{"detail": {"detail": "...", "code": "..."}}` (see
// server/tests/test_activate_profile.py:111); this exercises the parser
// against that shape plus the string-detail, top-level-code, and
// non-object/absent-body fallback shapes, using the exact payloads the
// server emits for name_collision and version_too_new.
//
// Imports directly from lib/apiError.ts (not api.ts) because api.ts pulls in
// lib/base.ts, which reads `window.location` at module-load time — that's
// fine in the browser but throws under plain Node/tsx, which is how this
// suite is run (see repo verify command: `npx tsx src/lib/__tests__/*.test.ts`).
import { parseApiError } from "../apiError";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }

// (a) nested object detail — the FastAPI HTTPException(detail={...}) idiom.
test("nested object detail extracts message and code", () => {
  const { message, code } = parseApiError(409, {
    detail: { detail: "a plan named 'X' already exists", code: "name_collision" },
  });
  assert(message === "a plan named 'X' already exists", `message: ${message}`);
  assert(code === "name_collision", `code: ${code}`);
});

test("nested object detail with non-string detail.detail stringifies it", () => {
  // detail.code (nested) is honored; a sibling top-level `code` is not —
  // matches the FastAPI idiom where both live inside `detail`.
  const inner = { detail: { foo: "bar", code: "weird" } };
  const { message, code } = parseApiError(422, inner);
  assert(message === JSON.stringify(inner.detail), `message: ${message}`);
  assert(code === "weird", `code: ${code}`);
});

// (b) plain string detail — no code.
test("string detail extracts message with no code", () => {
  const { message, code } = parseApiError(404, { detail: "plan not found" });
  assert(message === "plan not found", `message: ${message}`);
  assert(code === undefined, `code: ${code}`);
});

// (c) top-level code alongside a string detail — pre-existing flat shape.
test("top-level code with string detail keeps existing behavior", () => {
  const { message, code } = parseApiError(409, { code: "running", detail: "rig is busy" });
  assert(message === "rig is busy", `message: ${message}`);
  assert(code === "running", `code: ${code}`);
});

// (d) non-object/absent body — falls back.
test("absent body falls back to the given fallback string", () => {
  const { message, code } = parseApiError(500, undefined, "Internal Server Error");
  assert(message === "Internal Server Error", `message: ${message}`);
  assert(code === undefined, `code: ${code}`);
});

test("non-object body (string) falls back", () => {
  const { message, code } = parseApiError(500, "oops", "fallback text");
  assert(message === "fallback text", `message: ${message}`);
  assert(code === undefined, `code: ${code}`);
});

test("null body falls back to a status-derived default when no fallback given", () => {
  const { message } = parseApiError(503, null);
  assert(message === "HTTP 503", `message: ${message}`);
});

// Exact server payloads (server/astrodeck/api/app.py).
test("exact name_collision payload (POST /api/plans, 409)", () => {
  const payload = {
    detail: {
      detail: "a plan named 'M31 LRGB' already exists",
      code: "name_collision",
    },
  };
  const { message, code } = parseApiError(409, payload);
  assert(code === "name_collision", `code: ${code}`);
  assert(message === "a plan named 'M31 LRGB' already exists", `message: ${message}`);
});

test("exact version_too_new payload (POST /api/plans/import, 422)", () => {
  const payload = {
    detail: {
      detail: "This plan was exported by a newer AstroDeck version",
      code: "version_too_new",
    },
  };
  const { message, code } = parseApiError(422, payload);
  assert(code === "version_too_new", `code: ${code}`);
  assert(message === "This plan was exported by a newer AstroDeck version", `message: ${message}`);
});

test("exact invalid schema_version payload (POST /api/plans/import, 422)", () => {
  const payload = { detail: { detail: "invalid schema_version", code: "invalid" } };
  const { message, code } = parseApiError(422, payload);
  assert(code === "invalid", `code: ${code}`);
  assert(message === "invalid schema_version", `message: ${message}`);
});

test("exact running payload (POST /api/profiles/{id}/activate, 409)", () => {
  const payload = {
    detail: {
      detail: "a sequence, capture loop or polar alignment is running",
      code: "running",
    },
  };
  const { message, code } = parseApiError(409, payload);
  assert(code === "running", `code: ${code}`);
  assert(message === "a sequence, capture loop or polar alignment is running", `message: ${message}`);
});

// (e) nested detail carrying a target-resource id — the exact
// POST /api/locations name_collision payload (app.py:1427-1429): the id is
// the EXISTING location's id, the authoritative overwrite target for the UI.
test("exact locations name_collision payload yields code AND id", () => {
  const payload = {
    detail: { code: "name_collision", id: "3f2a77c0deadbeef3f2a77c0deadbeef" },
  };
  const { code, id } = parseApiError(409, payload);
  assert(code === "name_collision", `code: ${code}`);
  assert(id === "3f2a77c0deadbeef3f2a77c0deadbeef", `id: ${id}`);
});

test("nested detail with detail+code+id yields all three", () => {
  const payload = {
    detail: { detail: "already exists", code: "name_collision", id: "abc123" },
  };
  const { message, code, id } = parseApiError(409, payload);
  assert(message === "already exists", `message: ${message}`);
  assert(code === "name_collision", `code: ${code}`);
  assert(id === "abc123", `id: ${id}`);
});

test("id is undefined when the nested detail has none", () => {
  const { id } = parseApiError(409, {
    detail: { detail: "library is full", code: "library_full" },
  });
  assert(id === undefined, `id: ${id}`);
});

test("non-string id is ignored (undefined)", () => {
  const { id } = parseApiError(409, {
    detail: { code: "name_collision", id: 42 },
  });
  assert(id === undefined, `id: ${id}`);
});

test("flat top-level id alongside string detail is honored", () => {
  const { code, id } = parseApiError(409, {
    code: "name_collision",
    id: "flat99",
    detail: "exists",
  });
  assert(code === "name_collision", `code: ${code}`);
  assert(id === "flat99", `id: ${id}`);
});

test("string detail and absent body yield no id", () => {
  assert(parseApiError(404, { detail: "not found" }).id === undefined, "string detail");
  assert(parseApiError(500, undefined, "ISE").id === undefined, "absent body");
});

// (f) plain FastAPI/pydantic 422 validation-error array — Array.isArray is
// also `typeof === "object"`, so this must NOT fall into the nested-custom-
// shape branch and get JSON.stringify'd whole into the toast message.
test("pydantic validation-error array yields a readable message, not raw JSON", () => {
  // exact shape server/astrodeck/sequence/models.py's
  // `exposure_s: float = Field(gt=0, le=3600)` produces on a 0/negative step.
  const payload = {
    detail: [
      {
        type: "greater_than",
        loc: ["body", "targets", 0, "steps", 0, "exposure_s"],
        msg: "Input should be greater than 0",
        input: 0,
        ctx: { gt: 0 },
      },
    ],
  };
  const { message, code } = parseApiError(422, payload);
  assert(!message.startsWith("["), `message must not be raw JSON: ${message}`);
  assert(message.includes("Input should be greater than 0"), `message: ${message}`);
  assert(message.includes("exposure_s"), `message should name the field: ${message}`);
  assert(code === undefined, `code: ${code}`);
});

test("pydantic validation-error array with multiple items surfaces the first", () => {
  const payload = {
    detail: [
      { type: "missing", loc: ["body", "name"], msg: "Field required" },
      { type: "greater_than", loc: ["body", "count"], msg: "Input should be greater than 0" },
    ],
  };
  const { message } = parseApiError(422, payload);
  assert(message.includes("Field required"), `message: ${message}`);
  assert(message.includes("name"), `message: ${message}`);
});

test("empty validation-error array falls back to a generic message, not a crash", () => {
  const { message } = parseApiError(422, { detail: [] });
  assert(typeof message === "string" && message.length > 0, `message: ${message}`);
});

test("validation-error array with a non-object item falls back gracefully", () => {
  const { message } = parseApiError(422, { detail: ["oops"] });
  assert(typeof message === "string" && message.length > 0, `message: ${message}`);
  assert(!message.startsWith("["), `message must not be raw JSON: ${message}`);
});

console.log(`apiError.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
