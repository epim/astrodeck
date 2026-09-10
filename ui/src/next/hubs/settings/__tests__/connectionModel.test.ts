// connectionModel.test.ts - the Connection sheet's derivation, over every
// origin/config permutation the rig can present.
//
//   Run directly:  npx tsx src/next/hubs/settings/__tests__/connectionModel.test.ts
//   Also run by `npm test` and type-checked by `tsc -b`.
//
// WHAT THIS GUARDS. The sheet claims, in words, which address this browser is
// on and where the other one is. Every one of those claims is a sentence a user
// will act on at 2 a.m. with a phone and no other diagnostic, so each one gets
// an assertion:
//
//   - `via` beats the pathname, and a disagreement becomes a visible note
//     rather than a silent correction (the Wave S3 rule);
//   - the card you are ON has no button, and the other card's button is
//     honest-disabled with a REASON when there is no address to open;
//   - AUTO-SWITCH offers only after 30 s, only when it is on, and only when the
//     other origin is actually addressable;
//   - nothing invents an uptime, a hardware model or a MB/s figure.
//
// Convention: pure-lib skeleton (shell-and-tests.md section 4) plus the minimal
// browser stubs `lib/base.ts` needs at import (it reads
// `window.location.pathname` at module scope to pin the relay mount).

class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string): string | null { return this.m.has(k) ? (this.m.get(k) as string) : null; }
  setItem(k: string, v: string): void { this.m.set(k, String(v)); }
  removeItem(k: string): void { this.m.delete(k); }
  clear(): void { this.m.clear(); }
  key(): string | null { return null; }
  get length(): number { return this.m.size; }
}

const g = globalThis as unknown as {
  localStorage?: Storage;
  window?: unknown;
  document?: unknown;
};
if (typeof g.localStorage === "undefined") g.localStorage = new MemStorage() as unknown as Storage;
if (typeof g.window === "undefined") {
  g.window = { location: { pathname: "/", protocol: "http:", host: "rig.local" } };
}

const {
  CONN_PREF_KEY, DEFAULT_CONN_PREF, NO_LAN_ADDRESS, NO_RELAY_ADDRESS, OFFER_AFTER_MS,
  PAIR_OVER_RELAY, connectionModel, readConnPref, testLine, writeConnPref,
} = await import("../sheets/connectionModel");
type ConnectionInputs = Parameters<typeof connectionModel>[0];
type RemoteStatusLike = NonNullable<ConnectionInputs["remote"]>;

// ------------------------------------------------------------------ harness
let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; }
  catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function assert(cond: boolean, msg: string): void { if (!cond) throw new Error(msg); }
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) throw new Error(`${msg} (expected ${String(want)}, got ${String(got)})`);
}

// ------------------------------------------------------------------ fixtures
const LAN: ConnectionInputs = {
  pathname: "/",
  host: "192.168.4.1",
  protocol: "http:",
  hash: "#/settings/general/connection",
  secureContext: false,
  remote: null,
  pref: { ...DEFAULT_CONN_PREF },
  wsPhase: "up",
  downMs: null,
  engineVersion: "0.3.28",
  freeGb: 412.4,
};

const RELAY_ORIGIN: ConnectionInputs = {
  ...LAN,
  pathname: "/h/abc123/",
  host: "relay.astrodeck.app",
  protocol: "https:",
  secureContext: true,
};

function remote(over: Partial<RemoteStatusLike> = {}): RemoteStatusLike {
  return {
    enabled: true,
    home_id: "abc123",
    relay_host: "relay.astrodeck.app",
    connected: true,
    last_error: null,
    since_unix: 1_757_000_000,
    gen: 3,
    via: "direct",
    ...over,
  };
}

// --------------------------------------------- permutation 1: LAN, no relay
test("LAN origin with no relay known: you are here on DIRECT, nothing to open", () => {
  const m = connectionModel(LAN);
  eq(m.here, "direct", "a root pathname is the LAN origin");
  eq(m.direct.here, true, "the DIRECT card is not marked as the one you are on");
  eq(m.direct.status, "you are here", "the DIRECT status chip is wrong");
  eq(m.direct.action, null, "the card you are already on must not offer to open itself");
  eq(m.relay.status, "not set up", "an unpaired relay must say so");
  eq(m.relay.action?.url, null, "there is no relay url to open");
  eq(m.relay.action?.lockedReason, NO_RELAY_ADDRESS, "the relay button locked with no reason");
  eq(m.pairing.url, null, "pairing offered a link with no relay paired");
  eq(m.pairing.remoteReason, null, "the LAN origin is where pairing is allowed");
  assert(
    m.relay.description.includes("An admin pairs the rig with a relay"),
    "the unpaired relay card does not say who can pair it",
  );
});

// -------------------------------- permutation 2: LAN, relay paired and up
test("LAN origin with the tunnel up: RELAY reads connected and offers its url", () => {
  const m = connectionModel({ ...LAN, remote: remote() });
  eq(m.here, "direct", "via=direct must keep this tab on the LAN card");
  eq(m.relay.status, "connected", "a live tunnel must read connected");
  eq(m.relay.statusTone, "good", "a live tunnel is not a warning");
  eq(m.relay.address, "relay.astrodeck.app/h/abc123/", "the relay address is wrong");
  eq(
    m.relay.action?.url,
    "https://relay.astrodeck.app/h/abc123/#/settings/general/connection",
    "the relay link must carry the current hash so the user lands back where they were",
  );
  eq(m.relay.action?.label, "OPEN VIA RELAY", "the relay button is not a link label");
  eq(m.pairing.url, "https://relay.astrodeck.app/h/abc123/", "the pair link is wrong");
  assert(
    m.relay.description.includes("The tunnel has been up since"),
    "since_unix never reached the card",
  );
  eq(m.notes.length, 0, "agreeing sources must not raise a note");
});

test("a tunnel that is enabled but down carries its last error, not a green chip", () => {
  const m = connectionModel({
    ...LAN,
    remote: remote({ connected: false, since_unix: null, last_error: "dial-out refused" }),
  });
  eq(m.relay.status, "not connected", "a dead tunnel must not read as configured");
  eq(m.relay.statusTone, "warn", "a dead tunnel is a warning");
  assert(
    m.relay.description.includes("Last error from the tunnel: dial-out refused."),
    "the tunnel's own error was swallowed",
  );
});

// ---------------------------- permutation 3: relay origin, S3 not answering
test("relay origin with no remote status: the pathname still names the origin", () => {
  const m = connectionModel(RELAY_ORIGIN);
  eq(m.here, "relay", "/h/<home> is the relay mount");
  eq(m.relay.here, true, "the RELAY card is not marked as the one you are on");
  eq(m.relay.action, null, "the relay card must not offer to open the page you are on");
  eq(m.direct.status, "no address yet", "an unknown LAN address must say so");
  eq(m.direct.action?.lockedReason, NO_LAN_ADDRESS, "the DIRECT button locked with no reason");
  eq(m.pairing.remoteReason, PAIR_OVER_RELAY, "pairing over the relay must state the LAN-only rule");
  eq(m.pairing.url, "https://relay.astrodeck.app/h/abc123/", "the relay origin knows its own url");
  eq(m.transfers, "through the relay - a 2 GB FITS set takes a while", "the transfer line is wrong");
});

test("relay origin with a remembered LAN host: DIRECT becomes a real link", () => {
  const m = connectionModel({
    ...RELAY_ORIGIN,
    pref: { prefer: "relay", auto: true, lanHost: "192.168.4.1", lanProtocol: "http:" },
  });
  eq(m.direct.status, "open", "a known LAN address must be offered");
  eq(
    m.direct.action?.url,
    "http://192.168.4.1/#/settings/general/connection",
    "the direct link is wrong",
  );
  eq(m.direct.action?.lockedReason, null, "a known address must not be locked");
});

// ------------------------------------------------- permutation 4: they disagree
test("via beats the pathname, and the disagreement is a note the user can see", () => {
  const m = connectionModel({ ...LAN, remote: remote({ via: "relay" }) });
  eq(m.here, "relay", "the rig's own answer must win");
  eq(m.notes.length, 1, "a disagreement between via and the pathname must be surfaced");
  assert(/DIRECT/.test(m.notes[0]), "the note does not name what the address reads as");
  assert(/rig's answer/.test(m.notes[0]), "the note does not say which source won");
});

// ---------------------------------------------------------------- the offer
test("AUTO-SWITCH offers only after 30 s, and only with somewhere to go", () => {
  const base = { ...LAN, remote: remote() };
  eq(connectionModel({ ...base, downMs: null }).offer, null, "a live link must not offer a switch");
  eq(
    connectionModel({ ...base, downMs: OFFER_AFTER_MS - 1 }).offer,
    null,
    "the offer fired before 30 s",
  );
  const on = connectionModel({ ...base, downMs: OFFER_AFTER_MS + 1000, wsPhase: "reconnecting" });
  assert(on.offer != null, "31 s of silence did not raise the offer");
  eq(on.offer?.label, "OPEN VIA RELAY", "the offer's CTA is not the other origin");
  assert(/has not answered for/.test(on.offer?.text ?? ""), "the offer does not say how long");
  eq(
    connectionModel({ ...base, downMs: 60_000, pref: { ...base.pref, auto: false } }).offer,
    null,
    "AUTO-SWITCH off must silence the offer",
  );
  eq(
    connectionModel({ ...LAN, remote: null, downMs: 60_000 }).offer,
    null,
    "an offer with no reachable other origin is an offer that cannot be taken",
  );
});

// -------------------------------------------------------------- the rig card
test("the rig card states the link phase and omits what the wire does not carry", () => {
  const up = connectionModel(LAN);
  eq(up.rigState, "online", "an up link is not online");
  eq(up.rigMode, "DIRECT", "the mode word is wrong");
  eq(up.rigLine, "192.168.4.1 · engine 0.3.28", "the rig line is wrong");
  eq(up.rigSub, "412 GB free", "the disk clause is wrong");

  const down = connectionModel({ ...LAN, wsPhase: "down", engineVersion: null, freeGb: null });
  eq(down.rigState, "unreachable", "a down link must say unreachable");
  eq(down.rigStateTone, "bad", "a down link is not a warning, it is a failure");
  eq(down.rigLine, "192.168.4.1", "an unknown engine version must drop the clause, not print a dash");
  eq(down.rigSub, null, "an unknown free space must be absent, not zero");

  const blob = JSON.stringify(down);
  assert(!/MB\/s/.test(blob), "a throughput figure appeared that nothing measured");
  assert(!/\bup \d+ d\b/.test(blob), "an uptime appeared that nothing on the wire carries");
  assert(!/owner key paired/.test(blob), "a pairing date appeared that nothing on the wire carries");
});

// ------------------------------------------------------------ secure context
test("an insecure origin says what breaks and what still works", () => {
  const insecure = connectionModel(LAN);
  assert(
    insecure.secure[0] === "AR camera and gyro need a secure connection - set up in Connection.",
    "the insecure headline is not the design's sentence",
  );
  assert(
    insecure.secure.some((s) => s.includes("falls back to MAP mode")),
    "the insecure case never says the finder still works",
  );
  const secure = connectionModel({ ...LAN, secureContext: true });
  assert(
    secure.secure[0].startsWith("This connection is secure"),
    "a secure origin still warns about the camera",
  );
  assert(
    secure.secure.some((s) => s.includes("certificate")),
    "the LAN-HTTPS paragraph is missing",
  );
});

// -------------------------------------------------------------- the TEST line
test("testLine names both halves of the check", () => {
  eq(
    testLine({ ok: true, ms: 12, signedIn: true, email: "a@b.c", role: "admin", error: null }),
    "reachable in 12 ms · signed in as a@b.c (admin)",
    "the success line is wrong",
  );
  eq(
    testLine({ ok: true, ms: 12, signedIn: false, email: null, role: null, error: null }),
    "the rig answered, but this browser is not signed in on this address - sign in again here",
    "the reachable-but-signed-out line is the one that makes a move between addresses diagnosable",
  );
  eq(
    testLine({ ok: false, ms: null, signedIn: false, email: null, role: null, error: "network error" }),
    "network error",
    "a failure must carry the error it got",
  );
  eq(
    testLine({ ok: false, ms: null, signedIn: false, email: null, role: null, error: null }),
    "the rig did not answer on this address",
    "a failure with no error text still needs a sentence",
  );
});

// ------------------------------------------------------------- the preference
test("the preference round-trips, and garbage reads as the default", () => {
  localStorage.clear();
  eq(readConnPref().auto, true, "AUTO-SWITCH must default to on");
  eq(readConnPref().lanHost, null, "nothing stored must not invent a LAN host");
  writeConnPref({ prefer: "relay", auto: false, lanHost: "10.0.0.9", lanProtocol: "http:" });
  const back = readConnPref();
  eq(back.prefer, "relay", "the preferred origin did not survive");
  eq(back.auto, false, "the auto flag did not survive");
  eq(back.lanHost, "10.0.0.9", "the LAN host did not survive");
  eq(back.lanProtocol, "http:", "the LAN scheme did not survive");
  localStorage.setItem(CONN_PREF_KEY, "{not json");
  eq(readConnPref().prefer, "direct", "unparseable storage must render as the default, not throw");
  localStorage.clear();
});

const total = passed + failed;
console.log(`connectionModel.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
export default { passed, failed, total };
export { passed, failed, total };
