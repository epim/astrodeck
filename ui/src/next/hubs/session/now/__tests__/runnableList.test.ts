// runnableList.test.ts - SESSION / NOW's "start something tonight" list never
// offers a flow this build cannot open (#153, spec 2026-09-23 section 3.6).
//
//   Run:  node --import tsx src/next/hubs/session/now/__tests__/runnableList.test.ts   (from ui/)
//
// WHY THIS IS WORTH A FILE. `GET /api/flows` now lists the files the store
// could not read - one a newer AstroDeck saved, one that does not parse, one
// that fails validation - as rows carrying `unreadable` (server
// `FlowStore._row`). Skipping them made a damaged flow look deleted, so the
// libraries show them. This list is different: every row on it is a promise
// that pressing RUN starts a night. A row whose RUN can only end in a 404 is a
// promise nothing keeps, so an unreadable card is dropped here, and the
// readable ones around it keep their verbs and their order.
//
// Every case names the mutant it kills and quotes the failure that mutant
// produced when it was run from a byte-for-byte backup of the source file.

/* eslint-disable @typescript-eslint/no-explicit-any */

// `lib/flowsApi` imports `lib/api` -> `lib/base`, which reads
// `window.location.pathname` at module scope. Nothing here makes a request.
(globalThis as any).window = { location: { pathname: "/", origin: "http://local" } };
(globalThis as any).localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

const { buildRunnables } = await import("../runnableList");
const { unreadableReason, UNREADABLE_FALLBACK } = await import("../../../../../lib/flowsApi");
type FlowCard = import("../../../../../lib/flowsApi").FlowCard;

let passed = 0;
let failed = 0;
const failures: string[] = [];
function test(name: string, fn: () => void): void {
  try { fn(); passed++; } catch (e) { failed++; failures.push(`x ${name}: ${(e as Error).message}`); }
}
function eq<T>(got: T, want: T, msg: string): void {
  if (got !== want) {
    throw new Error(`${msg}\n  expected ${JSON.stringify(want)}\n  got      ${JSON.stringify(got)}`);
  }
}

function card(id: string, extra: Partial<FlowCard> = {}): FlowCard {
  return {
    id, name: id.toUpperCase(), folder: "My flows", tagline: `${id} tagline`,
    readonly: false, stages: 4, wires: 3, last_run: null, last_result: "",
    updated_ts: 1_757_000_000, ...extra,
  };
}

const NEWER = "saved by a newer AstroDeck (schema 4); update to open it";
const BROKEN = "unreadable: not valid JSON (line 1, column 2)";

const IDLE = { plans: [], seqState: "idle", campaignFlowId: null, armed: null };

// ============================================================ the filter

// MUTANT "no filter" (the `if (unreadableReason(card) !== null) continue;` line
// in buildRunnables deleted). Observed, 2/6 - this case, the control below, the
// RESUME case and the empty-reason case:
//   x an unreadable card is never offered as runnable: the newer-schema row is
//     offered as something to RUN - its press can only 404
//     expected false
//     got      true
//   x an unreadable card never becomes RESUME either: an armed
//     session on an unreadable flow put that flow on the list
//     expected "good:RUN"
//     got      "newer:RESUME,good:RUN"
test("an unreadable card is never offered as runnable", () => {
  const rows = buildRunnables({
    ...IDLE,
    cards: [card("good-a"), card("newer", { unreadable: NEWER, readonly: true }),
            card("broken", { unreadable: BROKEN, readonly: true })],
  });
  eq(rows.some((r) => r.id === "newer"), false,
    "the newer-schema row is offered as something to RUN - its press can only 404");
  eq(rows.some((r) => r.id === "broken"), false,
    "the unparseable row is offered as something to RUN");
});

// CONTROL: the filter drops exactly the unreadable rows. A filter that dropped
// every read-only card (the Examples, which DO run) or every card would pass the
// case above and empty the list.
test("control: readable cards are kept, with their verbs and order, around an unreadable one", () => {
  const cards = [
    card("older", { last_run: 1_756_000_000 }),
    card("newer", { unreadable: NEWER, readonly: true, last_run: 1_758_000_000 }),
    card("example", { readonly: true, folder: "Examples", last_run: 1_757_000_000 }),
  ];
  const withRow = buildRunnables({ ...IDLE, cards });
  const without = buildRunnables({ ...IDLE, cards: cards.filter((c) => c.id !== "newer") });
  eq(JSON.stringify(withRow), JSON.stringify(without),
    "adding an unreadable card changed the readable rows");
  eq(withRow.map((r) => r.id).join(","), "example,older",
    "a read-only EXAMPLE is runnable and must stay; most recently run first");
  eq(withRow.every((r) => r.verb === "RUN"), true, "the readable rows keep RUN");
});

test("an unreadable card never becomes RESUME either", () => {
  // An armed session names the unreadable flow. It still does not make it a
  // row: the verb is decided only for rows that exist.
  const rows = buildRunnables({
    ...IDLE,
    seqState: "idle",
    cards: [card("newer", { unreadable: NEWER }), card("good")],
    armed: { origin: "flow", origin_id: "newer" },
  });
  eq(rows.map((r) => `${r.id}:${r.verb}`).join(","), "good:RUN",
    "an armed session on an unreadable flow put that flow on the list");
});

// ====================================================== the reason helper
//
// `unreadableReason` is the one reading of the key all three surfaces share.

test("the helper returns the server's own reason, verbatim", () => {
  eq(unreadableReason(card("n", { unreadable: NEWER })), NEWER, "the newer-schema reason");
  eq(unreadableReason(card("b", { unreadable: BROKEN })), BROKEN, "the unreadable reason");
});

test("control: a card without the key (every card an older server sends) is readable", () => {
  eq(unreadableReason(card("plain")), null, "a plain card read as unreadable");
  eq(unreadableReason({ ...card("nulled"), unreadable: null as any }), null,
    "an explicit null is not a reason");
});

// MUTANT "empty reason reads as readable" (`unreadableReason` in flowsApi.ts
// returns null instead of UNREADABLE_FALLBACK for a blank reason). Observed, 5/6:
//   x a row the server marked with an EMPTY reason is still unreadable, with a
//     sentence: an empty reason made the row readable again
//     expected "this AstroDeck cannot open this flow"
//     got      null
test("a row the server marked with an EMPTY reason is still unreadable, with a sentence", () => {
  // The KEY is the server saying "this is a row, not a record". An empty string
  // there is a broken reason, not a readable flow - reading it as readable
  // would put a RUN on a file every route answers 404 for.
  eq(unreadableReason(card("e", { unreadable: "" })), UNREADABLE_FALLBACK,
    "an empty reason made the row readable again");
  eq(unreadableReason(card("s", { unreadable: "   " })), UNREADABLE_FALLBACK,
    "a blank reason made the row readable again");
  const rows = buildRunnables({ ...IDLE, cards: [card("e", { unreadable: "" }), card("good")] });
  eq(rows.map((r) => r.id).join(","), "good", "the empty-reason row was offered as runnable");
});

// ------------------------------------------------------------------- tally
const total = passed + failed;
console.log(`runnableList.test: ${passed}/${total} passed`);
for (const f of failures) console.log("  " + f);
if (failed) (globalThis as any).process.exitCode = 1;
export default { passed, failed, total };
export { passed, failed, total };
