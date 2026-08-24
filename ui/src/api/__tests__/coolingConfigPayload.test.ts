// setCoolingConfig must not carry a setpoint the panel does not own.
//
// The `delete setpoint_c` in setCoolingConfig BECAME LOAD-BEARING the moment
// the server started honouring an explicitly-sent setpoint. Before that change
// the server discarded whatever arrived in that field (which was the bug: the
// setpoint was unwritable through POST /api/config, and a flow-driven run put
// 19 lights on disk at +23 C against a -10 C dark library). Now absent means
// unchanged, null means "no cooling intent", and a number WINS.
//
// So the client-side strip is the only thing standing between a warm-rate edit
// and a cancelled Cool. SafetyLimitsPanel's `coolDraft` is a JSON deep copy of
// the config as it was WHEN THE PANEL OPENED, so `setpoint_c` is present on the
// object at runtime even though the TS CoolingConfig type has no such field.
// The sequence that costs a night:
//
//   21:00  open Settings                (draft captures setpoint_c = null)
//   21:05  press Cool -15 on the camera panel   (server: setpoint_c = -15)
//   21:10  nudge the warm rate, Save     (stale null goes back, cooling cancelled)
//
// Measured: reverting the destructure to `{ cooling }` left the whole UI suite
// green (203 files, 2486 assertions) and the whole Python suite green. Nothing
// anywhere read the request body of this function.
//
// Run with:  npx tsx src/api/__tests__/coolingConfigPayload.test.ts

/* eslint-disable @typescript-eslint/no-explicit-any */

// `../backends` pulls in lib/base.ts, which resolves the API base path off
// `window.location` at module scope — a bare import takes the file down before
// an assertion runs. Stub first, then import dynamically (the apiErrorPayload
// idiom).
(globalThis as unknown as { window: unknown }).window = {
  location: { pathname: "/", origin: "http://local" },
} as unknown as Window;

let sent: any = null;
(globalThis as any).fetch = async (_url: any, init: any) => {
  sent = init?.body ? JSON.parse(init.body) : null;
  return {
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => ({}),
    text: async () => "{}",
  };
};

const { setCoolingConfig } = await import("../backends");

let passed = 0, failed = 0;
const failures: string[] = [];
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}

const POLICY = {
  cool_timeout_s: 900,
  warm_ramp: true,
  warm_rate_c_per_min: 3.0,
  warm_ambient_c: null,
};

await test("a draft carrying a setpoint does not send it", async () => {
  sent = null;
  // `as any` on purpose: the runtime object HAS the key even though the TS
  // type does not, which is exactly the situation the strip exists for. A test
  // that passed a type-clean object would assert nothing.
  await setCoolingConfig({ ...POLICY, setpoint_c: -15.0 } as any);
  assert(sent != null, "setCoolingConfig sent no body");
  assert(!("setpoint_c" in sent.cooling),
    `the warm-ramp save carried a setpoint the panel does not own: ${JSON.stringify(sent.cooling)}`);
});

await test("a null setpoint is stripped too, not sent as an explicit clear", async () => {
  sent = null;
  // The worse half: null is not silence to the server, it is "no cooling
  // intent". A stale null from a draft opened before the operator pressed Cool
  // would CANCEL the standing request rather than merely fail to update it.
  await setCoolingConfig({ ...POLICY, setpoint_c: null } as any);
  assert(!("setpoint_c" in sent.cooling),
    `a null setpoint was forwarded as an explicit clear: ${JSON.stringify(sent.cooling)}`);
});

await test("the policy fields the panel DOES own still go out", async () => {
  sent = null;
  await setCoolingConfig({ ...POLICY, warm_rate_c_per_min: 2.5,
                           warm_ramp: false } as any);
  assert(sent.cooling.warm_rate_c_per_min === 2.5,
    `the warm rate did not reach the server: ${JSON.stringify(sent.cooling)}`);
  assert(sent.cooling.warm_ramp === false, "warm_ramp did not reach the server");
  assert(sent.cooling.cool_timeout_s === 900, "cool_timeout_s was dropped");
  assert("warm_ambient_c" in sent.cooling,
    "warm_ambient_c was dropped — the strip took more than the one field");
});

console.log(`coolingConfigPayload: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
if (failed > 0) process.exit(1);
