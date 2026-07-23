// Unit tests for the Settings → Alerts pure logic (PRO-9 spec §3 task U2).
//
// There is no vitest/jest wired into this UI (build is `tsc -b && vite build`),
// so these use the same tiny inline-assert harness as eta.test.ts / foundation.test.ts.
// They compile under `tsc -b` and run directly with a TS-aware runner, e.g.
//   npx tsx src/lib/__tests__/alertSinks.test.ts

import { validateDraft, deriveSinkHealth, deadmanVerdict, defaultDraft } from "../alertSinks";
import type { AlertSink, AlertHealth } from "../../types";

// ---------------------------------------------------------------- harness
let passed = 0;
let failed = 0;
const failures: string[] = [];

function test(name: string, fn: () => void): void {
  try {
    fn();
    passed++;
  } catch (e) {
    failed++;
    failures.push(`✗ ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, msg = ""): void {
  if (a !== b) throw new Error(`${msg} expected ${String(b)}, got ${String(a)}`);
}

// ---------------------------------------------------------------- validateDraft
test("ntfy requires an http url", () => {
  eq(validateDraft(defaultDraft("ntfy", "a"), false) !== null, true);
  eq(validateDraft({ ...defaultDraft("ntfy", "a"), url: "https://ntfy.sh/t" }, false), null);
});
test("webhook requires an http url", () => {
  eq(validateDraft(defaultDraft("webhook", "w"), false) !== null, true);
  eq(validateDraft({ ...defaultDraft("webhook", "w"), url: "https://example/hook" }, false), null);
  eq(validateDraft({ ...defaultDraft("webhook", "w"), url: "not-a-url" }, false) !== null, true);
});
test("telegram requires a chat id and a bot token", () => {
  const d = defaultDraft("telegram", "t");
  eq(validateDraft(d, false) !== null, true);                         // nothing set
  eq(validateDraft({ ...d, chat_id: "42" }, false) !== null, true);   // chat id but no token
  eq(validateDraft({ ...d, chat_id: "42" }, true), null);             // token already stored
  eq(validateDraft({ ...d, chat_id: "42", token: "BOT" }, false), null);
});
test("discord accepts a stored secret with a blank input on edit", () => {
  const d = defaultDraft("discord", "d");
  eq(validateDraft(d, false) !== null, true);                 // nothing set
  eq(validateDraft(d, true), null);                           // secret already stored
  eq(validateDraft({ ...d, token: "https://discord.com/api/webhooks/1/x" }, false), null);
  eq(validateDraft({ ...d, token: "not-a-url" }, false) !== null, true);
});
test("slack accepts a stored secret with a blank input on edit", () => {
  const d = defaultDraft("slack", "s");
  eq(validateDraft(d, false) !== null, true);
  eq(validateDraft(d, true), null);
  eq(validateDraft({ ...d, token: "https://hooks.slack.com/services/T/B/xyz" }, false), null);
  eq(validateDraft({ ...d, token: "not-a-url" }, false) !== null, true);
});
test("email requires host/from/to and a valid port", () => {
  const e = { ...defaultDraft("email", "e"), smtp_host: "smtp.x", smtp_from: "a@x", smtp_to: "b@x" };
  eq(validateDraft(e, false), null);
  eq(validateDraft({ ...e, smtp_host: "" }, false) !== null, true);
  eq(validateDraft({ ...e, smtp_port: 0 }, false) !== null, true);
  eq(validateDraft({ ...e, smtp_port: 70000 }, false) !== null, true);
  eq(validateDraft({ ...e, smtp_from: "" }, false) !== null, true);
  eq(validateDraft({ ...e, smtp_to: "" }, false) !== null, true);
});

// ---------------------------------------------------------------- deriveSinkHealth
test("health verdict: queue > verified > untested; disabled dims", () => {
  const base: AlertSink = { id: "n", kind: "ntfy", enabled: true, url: "", min_level: "warning",
                            events: [], verified: true, heartbeat_min: 0 };
  const h: AlertHealth = { undelivered: 2, undelivered_by_sink: { n: 2 },
                           deadman: { configured: false, healthy: false, last_ping_age_s: null } };
  eq(deriveSinkHealth(base, h).tone, "bad");                  // queued wins over verified
  eq(deriveSinkHealth(base, null).tone, "good");              // verified
  eq(deriveSinkHealth({ ...base, verified: false }, null).tone, "warn");
  eq(deriveSinkHealth({ ...base, enabled: false }, h).tone, "dim");
});

// ---------------------------------------------------------------- deadmanVerdict
test("deadman verdict maps configured/healthy", () => {
  eq(deadmanVerdict(null).tone, "dim");
  eq(deadmanVerdict({ undelivered: 0, undelivered_by_sink: {},
     deadman: { configured: true, healthy: true, last_ping_age_s: 5 } }).tone, "good");
  eq(deadmanVerdict({ undelivered: 0, undelivered_by_sink: {},
     deadman: { configured: true, healthy: false, last_ping_age_s: null } }).tone, "bad");
  eq(deadmanVerdict({ undelivered: 0, undelivered_by_sink: {},
     deadman: { configured: false, healthy: false, last_ping_age_s: null } }).tone, "dim");
});

// ---------------------------------------------------------------- defaultDraft
test("defaultDraft seeds sensible per-kind defaults", () => {
  const email = defaultDraft("email", "e");
  eq(email.smtp_port, 587);
  eq(email.smtp_starttls, true);
  eq(email.token, "");
  const ntfy = defaultDraft("ntfy", "n");
  eq(ntfy.events.includes("run_end"), true);
  eq(ntfy.min_level, "warning");
});

// ---------------------------------------------------------------- report
const total = passed + failed;
// eslint-disable-next-line no-console
console.log(`\nalertSinks.test: ${passed}/${total} passed`);
if (failures.length) {
  // eslint-disable-next-line no-console
  console.error(failures.join("\n"));
  // No @types/node in this project (tsc -b gate) — guard process.exit via
  // globalThis so a failure still exits non-zero without a bare `process`
  // reference (connection.test.ts idiom).
  (globalThis as unknown as { process?: { exit(code: number): void } }).process?.exit(1);
}

export const result = { passed, failed, total };
