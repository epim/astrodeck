// userCreate.test.ts — one account form, two ways in. Inline-assert harness.
import {
  MAX_PASSWORD_BYTES,
  emailLooksValid,
  newUserBlocker,
  newUserBody,
  passwordTooLong,
  signInSummary,
  type NewUserDraft,
} from "../userCreate";

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

const draft = (o: Partial<NewUserDraft> = {}): NewUserDraft => ({
  username: "jane", email: "jane@example.com", password: "hunter2",
  method: "password", ...o,
});

test("a password account needs a password", () => {
  const why = newUserBlocker(draft({ password: "" }), true);
  eq(typeof why === "string" && /password/i.test(why), true, `got: ${why}`);
});

test("a Google account needs NO password", () => {
  eq(newUserBlocker(draft({ method: "google", password: "" }), true), null);
});

test("a Google account is refused when Google is off", () => {
  // Otherwise it mints an account that cannot sign in at all, and nothing says
  // so until the person tries.
  const why = newUserBlocker(draft({ method: "google", password: "" }), false);
  eq(typeof why === "string" && /google/i.test(why), true, `got: ${why}`);
  eq(/no way to sign in/i.test(why as string), true, `must state the consequence: ${why}`);
});

test("email is required for BOTH methods — it is the identity", () => {
  for (const method of ["password", "google"] as const) {
    const why = newUserBlocker(draft({ method, email: "" }), true);
    eq(typeof why === "string" && /email/i.test(why), true, `${method}: ${why}`);
  }
});

test("a malformed email is caught", () => {
  eq(newUserBlocker(draft({ email: "jane@" }), true) !== null, true);
  eq(emailLooksValid("jane@example.com"), true);
  eq(emailLooksValid("jane at example"), false);
});

test("a username is required", () => {
  const why = newUserBlocker(draft({ username: "   " }), true);
  eq(typeof why === "string" && /username/i.test(why), true, `got: ${why}`);
});

test("the blocker is a SENTENCE, never a boolean", () => {
  // The caller has nothing to render but the reason, so it cannot ship a dead
  // grey button with no stated cause.
  const why = newUserBlocker(draft({ password: "" }), true);
  eq(typeof why, "string");
  eq((why as string).length > 10, true, "must be readable, not a code");
});

test("a valid draft is not blocked", () => {
  eq(newUserBlocker(draft(), true), null);
});

test("bcrypt's limit is BYTES, not characters", () => {
  eq(passwordTooLong("a".repeat(MAX_PASSWORD_BYTES)), false);
  eq(passwordTooLong("a".repeat(MAX_PASSWORD_BYTES + 1)), true);
  // 24 multi-byte chars = 72 bytes exactly; 25 goes over while "25 characters"
  // looks well inside a 72-character limit.
  eq(passwordTooLong("é".repeat(36)), false, "36 x 2 bytes = 72");
  eq(passwordTooLong("é".repeat(37)), true, "37 x 2 bytes = 74");
});

test("a Google account posts an EMPTY password, not a hashed one", () => {
  // The server stores "" as no-local-credential; sending anything else would
  // create a usable password nobody chose.
  eq(newUserBody(draft({ method: "google", password: "typed-then-switched" })).password, "");
});

test("a password account posts what was typed, unmangled", () => {
  eq(newUserBody(draft({ password: " spaces kept " })).password, " spaces kept ");
});

test("username and email are trimmed but the password is not", () => {
  const b = newUserBody(draft({ username: " jane ", email: " jane@example.com " }));
  eq(b.username, "jane");
  eq(b.email, "jane@example.com");
});

test("the summary states the consequence of each choice", () => {
  const g = signInSummary("google", "jane@example.com");
  eq(/cannot sign in locally/i.test(g), true, `google: ${g}`);
  eq(g.includes("jane@example.com"), true, "must name the identity");
  const p = signInSummary("password", "jane@example.com");
  eq(/username and password/i.test(p), true, `password: ${p}`);
});

test("the summary degrades when no email is typed yet", () => {
  const s = signInSummary("google", "  ");
  eq(s.includes("undefined"), false);
  eq(/this address/i.test(s), true, `got: ${s}`);
});

console.log(`userCreate.test.ts: ${passed} passed, ${failed} failed`);
if (failed) {
  failures.forEach((f) => console.error(f));
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}
