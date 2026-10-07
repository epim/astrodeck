// Copyright (c) 2026 James Penick
// SPDX-License-Identifier: Apache-2.0
// The panel parameter on the session stack's two requests (#172 part A, WP-121).
//
//   npx tsx src/api/__tests__/w16SessionStackPanel.test.ts
//
// THE CLAIM IS BYTE IDENTITY. Every client that follows the latest panel (and
// every one-target night) must keep asking for the URLs this app has always
// built, because an empty `?panel=` means the same thing to the server and a
// different thing to every browser cache, and re-fetching every stack image on
// every client is a real cost for a parameter that says nothing. The rule is
// the one `channel` already follows: appended only when given.
//
// Mutant M20 (run from a byte backup inside the worktree, restored
// byte-identically and grepped gone): `sessionStackImageUrl` appends
// `&panel=${encodeURIComponent(panel ?? "")}` unconditionally. 3 of 5 pass; the
// failures, verbatim:
//   "the composite URL is byte-identical for a client that follows the latest
//    panel: the composite URL changed for a client that follows the latest
//    panel: expected /api/sequence/stack/preview.jpg?size=1200&seq=7, got
//    /api/sequence/st..."
//   "the channel URL is the one it always was:  expected
//    /api/sequence/stack/preview.jpg?size=1200&seq=7&channel=Ha, got
//    /api/sequence/stack/preview.jpg?size=1200&seq=7&channel=Ha&panel="

/* eslint-disable @typescript-eslint/no-explicit-any */

// `../sessionStack` pulls in lib/base.ts, which resolves the base path off
// `window.location` at module scope; stub first, import dynamically.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };

const asked: string[] = [];
(globalThis as any).fetch = async (url: any) => {
  asked.push(String(url));
  return { ok: true, status: 200, statusText: "OK", json: async () => ({}) };
};

const { sessionStackImageUrl, getSessionStack } = await import("../sessionStack");

let passed = 0, failed = 0;
const failures: string[] = [];
async function test(name: string, fn: () => void | Promise<void>): Promise<void> {
  try { await fn(); passed++; } catch (e) {
    failed++; failures.push(`x ${name}: ${(e as Error).message}`);
  }
}
function eq<T>(a: T, b: T, m = ""): void {
  if (a !== b) throw new Error(`${m} expected ${String(b)}, got ${String(a)}`);
}

const LEGACY = "/api/sequence/stack/preview.jpg?size=1200&seq=7";

await test("the composite URL is byte-identical for a client that follows the latest panel", () => {
  eq(sessionStackImageUrl(7, 1200), LEGACY, "the composite URL changed for a client that follows the latest panel:");
  eq(sessionStackImageUrl(7, 1200, undefined, undefined), LEGACY, "an undefined panel leaked into the URL:");
  eq(sessionStackImageUrl(7, 1200, undefined, ""), LEGACY, "an empty panel leaked into the URL:");
});

await test("the channel URL is the one it always was", () => {
  eq(sessionStackImageUrl(7, 1200, "Ha"), `${LEGACY}&channel=Ha`);
  eq(sessionStackImageUrl(7, 1200, "Ha", ""), `${LEGACY}&channel=Ha`);
});

await test("a panel is appended after the channel, and only when given", () => {
  eq(sessionStackImageUrl(7, 1200, undefined, "t-1"), `${LEGACY}&panel=t-1`);
  eq(sessionStackImageUrl(7, 1200, "Ha", "t-1"), `${LEGACY}&channel=Ha&panel=t-1`);
});

await test("a panel key is encoded, because a key can be a target's name", () => {
  eq(sessionStackImageUrl(7, 1200, undefined, "M 31/north"),
    `${LEGACY}&panel=M%2031%2Fnorth`);
  eq(sessionStackImageUrl(7, 1200, undefined, "a&b=c"), `${LEGACY}&panel=a%26b%3Dc`,
    "a name carrying & or = would have split into extra parameters:");
});

await test("the status request is the plain one unless a panel is pinned", async () => {
  asked.length = 0;
  await getSessionStack();
  await getSessionStack(null);
  await getSessionStack("");
  eq(asked.join("|"), "/api/sequence/stack|/api/sequence/stack|/api/sequence/stack",
    "the status request changed for a client with no panel:");
  asked.length = 0;
  await getSessionStack("t-1");
  await getSessionStack("M 31");
  eq(asked.join("|"), "/api/sequence/stack?panel=t-1|/api/sequence/stack?panel=M%2031");
});

console.log(`w16SessionStackPanel: ${passed}/${passed + failed} passed`);
for (const f of failures) console.log("  " + f);
export const result = { passed, failed, total: passed + failed };
